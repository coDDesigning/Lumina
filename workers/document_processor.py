"""Separate process entry point for durable document extraction."""

import argparse
import logging
import multiprocessing
import os
import socket
import threading
import time
from collections.abc import Callable
from uuid import uuid4

from sqlalchemy.orm import Session

from backend.app.config import settings
from backend.app.database import SessionLocal
from services.document_extraction import (
    DocumentProcessingError,
    extract_document_chunks,
)
from services.processing_jobs import (
    ClaimedJob,
    claim_next_job,
    complete_job,
    fail_job,
    heartbeat_job,
    recover_expired_jobs,
)
from storage.base import Storage
from storage.dependencies import get_storage

logger = logging.getLogger(__name__)
SessionFactory = Callable[[], Session]
RECOVERY_BATCH_SIZE = 100
MAX_RECOVERY_BATCHES_PER_PASS = 10
HEARTBEAT_SHUTDOWN_SECONDS = 30


class WorkerProcessFatalError(RuntimeError):
    """The worker process must exit so its supervisor can recycle it."""


def _default_worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{uuid4()}"


def _heartbeat_loop(
    session_factory: SessionFactory,
    job: ClaimedJob,
    lease_seconds: int,
    stop: threading.Event,
    claim_lost: threading.Event,
) -> None:
    interval = min(30.0, max(0.05, lease_seconds / 3))
    while not stop.is_set():
        try:
            with session_factory() as session:
                current = heartbeat_job(session, job.id, job.claim_token, lease_seconds)
        except Exception:
            logger.exception("Failed to heartbeat processing job %s", job.id)
            if stop.wait(interval):
                return
            continue
        if not current:
            claim_lost.set()
            return
        if stop.wait(interval):
            return


def _record_failure(
    session_factory: SessionFactory,
    job: ClaimedJob,
    error: DocumentProcessingError,
) -> None:
    exponent = min(6, max(0, job.attempt_count - 1))
    retry_delay = min(60.0, 2.0**exponent)
    with session_factory() as session:
        fail_job(
            session,
            job.id,
            job.claim_token,
            error_code=error.code,
            error_message=str(error),
            retryable=error.retryable,
            retry_delay_seconds=retry_delay,
        )


def _extraction_process(connection, storage: Storage, job: ClaimedJob) -> None:
    try:
        chunks = extract_document_chunks(
            storage,
            storage_provider=job.storage_provider,
            storage_key=job.storage_key,
            expected_hash=job.file_hash,
            expected_size=job.file_size,
            file_type=job.file_type,
        )
        connection.send(("succeeded", chunks))
    except DocumentProcessingError as exc:
        connection.send(("failed", exc.code, str(exc), exc.retryable))
    except Exception:
        connection.send(("unexpected",))
    finally:
        connection.close()


def _extract_with_timeout(
    storage: Storage,
    job: ClaimedJob,
    timeout_seconds: int,
):
    context = multiprocessing.get_context("spawn")
    parent_connection, child_connection = context.Pipe(duplex=False)
    process = context.Process(
        target=_extraction_process,
        args=(child_connection, storage, job),
        daemon=True,
    )
    started = False
    result = None
    timed_out = False
    reaped = True
    try:
        process.start()
        started = True
        child_connection.close()
        deadline = time.monotonic() + timeout_seconds
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                break
            try:
                if parent_connection.poll(min(0.1, remaining)):
                    result = parent_connection.recv()
                    break
            except (EOFError, OSError):
                break
            if not process.is_alive():
                try:
                    if parent_connection.poll(0.1):
                        result = parent_connection.recv()
                except (EOFError, OSError):
                    pass
                break
    finally:
        parent_connection.close()
        child_connection.close()
        if started:
            reaped = _reap_process(process)

    if not reaped:
        raise WorkerProcessFatalError("Unable to terminate extraction subprocess")
    if timed_out and result is None:
        raise DocumentProcessingError(
            "PROCESSING_TIMEOUT",
            "Document processing exceeded the attempt time limit.",
            retryable=True,
        )
    if result is None or result[0] == "unexpected":
        raise DocumentProcessingError(
            "UNEXPECTED_PROCESSING_ERROR",
            "Document processing failed unexpectedly.",
            retryable=True,
        )
    if result[0] == "failed":
        raise DocumentProcessingError(
            result[1],
            result[2],
            retryable=result[3],
        )
    return result[1]


def _reap_process(process) -> bool:
    process.join(timeout=1)
    if process.is_alive():
        process.terminate()
        process.join(timeout=2)
    if process.is_alive():
        process.kill()
        process.join(timeout=2)
    return not process.is_alive()


def _stop_heartbeat(
    stop: threading.Event,
    heartbeat: threading.Thread,
    claim_lost: threading.Event,
) -> bool:
    stop.set()
    heartbeat.join(timeout=HEARTBEAT_SHUTDOWN_SECONDS)
    if heartbeat.is_alive():
        logger.error("Heartbeat did not stop before finalization")
        claim_lost.set()
        return False
    return True


def process_next_job(
    *,
    session_factory: SessionFactory = SessionLocal,
    storage: Storage | None = None,
    worker_id: str | None = None,
    lease_seconds: int | None = None,
) -> bool:
    if storage is None:
        storage = get_storage()
    if lease_seconds is None:
        lease_seconds = settings.processing_job_lease_seconds
    if worker_id is None:
        worker_id = _default_worker_id()

    with session_factory() as session:
        job = claim_next_job(
            session,
            worker_id,
            storage.provider,
            lease_seconds,
        )
    if job is None:
        return False

    stop = threading.Event()
    claim_lost = threading.Event()
    heartbeat = threading.Thread(
        target=_heartbeat_loop,
        args=(session_factory, job, lease_seconds, stop, claim_lost),
        daemon=True,
    )
    heartbeat.start()
    try:
        chunks = _extract_with_timeout(
            storage,
            job,
            settings.processing_job_attempt_timeout_seconds,
        )
    except WorkerProcessFatalError:
        _stop_heartbeat(stop, heartbeat, claim_lost)
        logger.critical("Extraction subprocess could not be reaped; exiting worker")
        raise
    except DocumentProcessingError as exc:
        heartbeat_stopped = _stop_heartbeat(stop, heartbeat, claim_lost)
        if heartbeat_stopped and not claim_lost.is_set():
            try:
                _record_failure(session_factory, job, exc)
            except Exception:
                logger.exception("Failed to record processing error for job %s", job.id)
        return True
    except Exception:
        logger.exception("Unexpected processing failure for job %s", job.id)
        heartbeat_stopped = _stop_heartbeat(stop, heartbeat, claim_lost)
        if heartbeat_stopped and not claim_lost.is_set():
            try:
                _record_failure(
                    session_factory,
                    job,
                    DocumentProcessingError(
                        "UNEXPECTED_PROCESSING_ERROR",
                        "Document processing failed unexpectedly.",
                        retryable=True,
                    ),
                )
            except Exception:
                logger.exception("Failed to record processing error for job %s", job.id)
        return True

    if not _stop_heartbeat(stop, heartbeat, claim_lost) or claim_lost.is_set():
        return True
    try:
        with session_factory() as session:
            completed = complete_job(session, job.id, job.claim_token, chunks)
    except Exception:
        # Leave the fenced running state intact; periodic recovery safely retries it.
        logger.exception("Failed to finalize processing job %s", job.id)
        return True
    if not completed:
        logger.info("Processing claim was lost before job %s completed", job.id)
    return True


def run_worker(
    *,
    once: bool,
    worker_id: str | None = None,
    stop_event: threading.Event | None = None,
) -> None:
    stop = stop_event or threading.Event()
    recovery_interval = min(
        30.0,
        max(0.1, settings.processing_job_lease_seconds / 2),
    )
    next_recovery = 0.0

    while not stop.is_set():
        monotonic_now = time.monotonic()
        if monotonic_now >= next_recovery:
            recovery_saturated = False
            try:
                recovered_total = 0
                for _ in range(MAX_RECOVERY_BATCHES_PER_PASS):
                    with SessionLocal() as session:
                        recovered = recover_expired_jobs(
                            session,
                            limit=RECOVERY_BATCH_SIZE,
                        )
                    recovered_total += recovered
                    if recovered < RECOVERY_BATCH_SIZE:
                        break
                else:
                    recovery_saturated = True
                if recovered_total:
                    logger.info("Recovered %s expired processing jobs", recovered_total)
            except Exception:
                logger.exception("Failed to recover expired processing jobs")
            next_recovery = (
                monotonic_now
                if recovery_saturated
                else monotonic_now + recovery_interval
            )

        try:
            processed = process_next_job(worker_id=worker_id)
        except WorkerProcessFatalError:
            logger.critical("Document worker requires process recycle")
            raise
        except Exception:
            logger.exception("Document worker iteration failed")
            processed = False
        if once:
            return
        if not processed:
            stop.wait(settings.processing_job_poll_seconds)


def main() -> None:
    parser = argparse.ArgumentParser(description="Process durable document jobs")
    parser.add_argument("--once", action="store_true", help="process at most one job")
    parser.add_argument("--worker-id", help="stable identifier shown in job leases")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    run_worker(once=args.once, worker_id=args.worker_id)


if __name__ == "__main__":
    main()
