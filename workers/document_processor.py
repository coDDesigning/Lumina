"""Separate process entry point for durable document extraction."""

import argparse
import logging
import multiprocessing
import os
import signal
import socket
import threading
import time
from collections.abc import Callable, Sequence
from typing import Protocol
from uuid import uuid4

from sqlalchemy.orm import Session

from backend.app.config import settings
from backend.app.database import SessionLocal
from backend.app.models import Course
from backend.app.observability import configure_logging, emit_emf_metrics
from backend.app.readiness import ReadinessError, check_readiness
from services.document_embedding import (
    EMBEDDING_STAGE,
    classify_embedding_error,
    embed_document_chunks,
)
from schemas.prompt_context import PromptContext
from services.prompt_context import resolve_prompt_context
from services.document_extraction import (
    DocumentProcessingError,
    extract_document,
)
from services.processing_jobs import (
    ClaimedJob,
    ChunkData,
    PageData,
    claim_next_job,
    complete_job,
    fail_job,
    heartbeat_job,
    processing_queue_metrics,
    recover_expired_jobs,
    replace_document_pages,
    update_job_stage,
)
from services.embeddings import EmbeddingProvider
from services.vector_store import VectorStore, VectorStoreError
from storage.base import Storage
from storage.dependencies import get_storage

logger = logging.getLogger(__name__)
SessionFactory = Callable[[], Session]
RECOVERY_BATCH_SIZE = 100
MAX_RECOVERY_BATCHES_PER_PASS = 10
HEARTBEAT_SHUTDOWN_SECONDS = 30
WORKER_SHUTDOWN_SIGNALS = {signal.SIGTERM, signal.SIGINT}


class WorkerProcessFatalError(RuntimeError):
    """The worker process must exit so its supervisor can recycle it."""


class StopEvent(Protocol):
    def is_set(self) -> bool: ...

    def wait(self, timeout: float) -> bool: ...


class _SignalStopEvent:
    """Lock-free stop flag written by Python's main-thread signal handler."""

    def __init__(self) -> None:
        self.requested = False

    def is_set(self) -> bool:
        return self.requested

    def wait(self, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while not self.requested:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(0.1, remaining))
        return self.requested


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


def _extraction_process(
    connection,
    storage: Storage,
    job: ClaimedJob,
    prompt_context: PromptContext | None = None,
) -> None:
    # The parent owns graceful shutdown and the hard timeout for this child.
    for shutdown_signal in WORKER_SHUTDOWN_SIGNALS:
        signal.signal(shutdown_signal, signal.SIG_IGN)
    if hasattr(signal, "pthread_sigmask"):
        signal.pthread_sigmask(signal.SIG_UNBLOCK, WORKER_SHUTDOWN_SIGNALS)
    try:

        def report_stage(stage) -> None:
            connection.send(("stage", stage.value))

        def report_extraction(pages: list[PageData]) -> None:
            connection.send(("extracted", pages))
            if connection.recv() != ("continue",):
                raise RuntimeError("Raw extraction persistence was rejected")

        result = extract_document(
            storage,
            storage_provider=job.storage_provider,
            storage_key=job.storage_key,
            expected_hash=job.file_hash,
            expected_size=job.file_size,
            file_type=job.file_type,
            stage_callback=report_stage,
            extraction_callback=report_extraction,
            prompt_context=prompt_context,
        )
        connection.send(("succeeded", result.pages, result.chunks))
    except DocumentProcessingError as exc:
        logger.info(
            "Document processing failed for job %s with code %s", job.id, exc.code
        )
        connection.send(("failed", exc.code, str(exc), exc.retryable))
    except Exception:
        connection.send(("unexpected",))
    finally:
        connection.close()


def _extract_with_timeout(
    storage: Storage,
    job: ClaimedJob,
    timeout_seconds: int,
    stage_callback: Callable[[str], None] | None = None,
    extraction_callback: Callable[[list[PageData], float], None] | None = None,
    *,
    prompt_context: PromptContext | None = None,
):
    context = multiprocessing.get_context("spawn")
    parent_connection, child_connection = context.Pipe(duplex=True)
    process = context.Process(
        target=_extraction_process,
        args=(child_connection, storage, job, prompt_context),
        daemon=True,
    )
    started = False
    result = None
    timed_out = False
    reaped = True
    try:
        _start_extraction_process(process)
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
                    if not isinstance(result, tuple) or not result:
                        break
                    if result[0] == "stage":
                        if len(result) != 2 or not isinstance(result[1], str):
                            break
                        if stage_callback is not None:
                            stage_callback(result[1])
                        result = None
                        continue
                    if result[0] == "extracted":
                        if (
                            len(result) != 2
                            or not isinstance(result[1], list)
                            or any(not isinstance(page, PageData) for page in result[1])
                        ):
                            break
                        try:
                            if extraction_callback is not None:
                                extraction_callback(
                                    result[1],
                                    max(0.001, deadline - time.monotonic()),
                                )
                        except Exception:
                            try:
                                parent_connection.send(("abort",))
                            except (BrokenPipeError, EOFError, OSError):
                                pass
                            raise
                        if time.monotonic() >= deadline:
                            timed_out = True
                            try:
                                parent_connection.send(("abort",))
                            except (BrokenPipeError, EOFError, OSError):
                                pass
                            result = None
                            break
                        parent_connection.send(("continue",))
                        result = None
                        continue
                    break
            except (EOFError, OSError):
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
    if result is None:
        raise DocumentProcessingError(
            "UNEXPECTED_PROCESSING_ERROR",
            "Document processing failed unexpectedly.",
            retryable=True,
        )
    return _document_data_from_process_result(result)


def _document_data_from_process_result(
    result,
) -> tuple[list[PageData], list[ChunkData]]:
    if not isinstance(result, tuple) or not result:
        raise DocumentProcessingError(
            "UNEXPECTED_PROCESSING_ERROR",
            "Document processing failed unexpectedly.",
            retryable=True,
        )
    if result[0] == "failed" and (
        len(result) != 4
        or not isinstance(result[1], str)
        or not isinstance(result[2], str)
        or type(result[3]) is not bool
    ):
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
    if result[0] != "succeeded" or len(result) != 3:
        raise DocumentProcessingError(
            "UNEXPECTED_PROCESSING_ERROR",
            "Document processing failed unexpectedly.",
            retryable=True,
        )
    pages = result[1]
    chunks = result[2]
    if not isinstance(pages, list) or any(
        not isinstance(page, PageData) for page in pages
    ):
        raise DocumentProcessingError(
            "UNEXPECTED_PROCESSING_ERROR",
            "Document processing failed unexpectedly.",
            retryable=True,
        )
    if not isinstance(chunks, list) or any(
        not isinstance(chunk, ChunkData) for chunk in chunks
    ):
        raise DocumentProcessingError(
            "UNEXPECTED_PROCESSING_ERROR",
            "Document processing failed unexpectedly.",
            retryable=True,
        )
    return pages, chunks


def _chunks_from_process_result(result) -> list[ChunkData]:
    """Retain the narrow parser used by existing worker contract tests."""
    return _document_data_from_process_result(result)[1]


def _reap_process(process) -> bool:
    process.join(timeout=1)
    if process.is_alive():
        process.kill()
        process.join(timeout=2)
    return not process.is_alive()


def _start_extraction_process(process) -> None:
    if not hasattr(signal, "pthread_sigmask"):
        process.start()
        return

    previous_mask = signal.pthread_sigmask(signal.SIG_BLOCK, WORKER_SHUTDOWN_SIGNALS)
    try:
        process.start()
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)


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
    shutdown_requested: Callable[[], bool] | None = None,
    embedding_provider: EmbeddingProvider | None = None,
    vector_store: VectorStore | None = None,
) -> bool:
    if shutdown_requested is not None and shutdown_requested():
        return False
    if storage is None:
        storage = get_storage()
    if lease_seconds is None:
        lease_seconds = settings.processing_job_lease_seconds
    if worker_id is None:
        worker_id = _default_worker_id()

    with session_factory() as session:
        if shutdown_requested is not None and shutdown_requested():
            return False
        job = claim_next_job(
            session,
            worker_id,
            storage.provider,
            lease_seconds,
        )
        prompt_context = (
            resolve_prompt_context(
                session,
                course=session.get(Course, job.course_id),
                document_ids=[job.document_id],
            )
            if job is not None
            else None
        )
    if job is None:
        return False
    processing_started = time.monotonic()

    stop = threading.Event()
    claim_lost = threading.Event()
    heartbeat = threading.Thread(
        target=_heartbeat_loop,
        args=(session_factory, job, lease_seconds, stop, claim_lost),
        daemon=True,
    )
    heartbeat.start()
    try:

        def persist_stage(stage: str) -> None:
            with session_factory() as session:
                updated = update_job_stage(
                    session,
                    job.id,
                    job.claim_token,
                    stage,
                )
            if not updated:
                claim_lost.set()
                raise DocumentProcessingError(
                    "STATUS_UPDATE_CONFLICT",
                    "The document processing claim changed unexpectedly.",
                    retryable=True,
                )

        def persist_extraction(
            pages: list[PageData],
            remaining_seconds: float,
        ) -> None:
            try:
                with session_factory() as session:
                    updated = replace_document_pages(
                        session,
                        job.id,
                        job.claim_token,
                        pages,
                        operation_timeout_seconds=remaining_seconds,
                    )
            except Exception:
                logger.exception("Failed to persist raw pages for job %s", job.id)
                raise DocumentProcessingError(
                    "EXTRACTION_PERSISTENCE_FAILED",
                    "The extracted document content could not be recorded.",
                    retryable=True,
                    failed_stage="extracting_text",
                ) from None
            if not updated:
                claim_lost.set()
                raise DocumentProcessingError(
                    "EXTRACTION_PERSISTENCE_CONFLICT",
                    "The extracted document content could not be recorded.",
                    retryable=True,
                    failed_stage="extracting_text",
                )

        pages, chunks = _extract_with_timeout(
            storage,
            job,
            settings.processing_job_attempt_timeout_seconds,
            stage_callback=persist_stage,
            extraction_callback=persist_extraction,
            prompt_context=prompt_context,
        )
        persist_stage(EMBEDDING_STAGE)
        embeddings = embed_document_chunks(
            [chunk.text for chunk in chunks],
            provider=embedding_provider,
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
        emit_emf_metrics(
            {"JobsRetried" if exc.retryable else "JobsFailed": 1},
            dimensions={"Service": "worker", "Environment": settings.app_env},
        )
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
        emit_emf_metrics(
            {"JobsRetried": 1},
            dimensions={"Service": "worker", "Environment": settings.app_env},
        )
        return True

    if not _stop_heartbeat(stop, heartbeat, claim_lost) or claim_lost.is_set():
        return True
    try:
        with session_factory() as session:
            completed = complete_job(
                session,
                job.id,
                job.claim_token,
                chunks,
                pages,
                embeddings=embeddings,
                vector_store=vector_store,
            )
    except VectorStoreError as exc:
        # The vector store is classified, so the job requeues instead of waiting
        # for the lease to expire.
        logger.warning("Vector persistence failed for job %s", job.id)
        try:
            _record_failure(session_factory, job, classify_embedding_error(exc))
        except Exception:
            logger.exception("Failed to record vector error for job %s", job.id)
        return True
    except Exception:
        # Leave the fenced running state intact; periodic recovery safely retries it.
        logger.exception("Failed to finalize processing job %s", job.id)
        return True
    if not completed:
        logger.info("Processing claim was lost before job %s completed", job.id)
    else:
        emit_emf_metrics(
            {
                "JobsSucceeded": 1,
                "ProcessingDurationMs": round(
                    (time.monotonic() - processing_started) * 1000, 3
                ),
            },
            dimensions={"Service": "worker", "Environment": settings.app_env},
            units={"ProcessingDurationMs": "Milliseconds"},
        )
    return True


def check_worker_ready(
    *,
    session_factory: SessionFactory = SessionLocal,
    storage: Storage | None = None,
) -> None:
    if storage is None:
        storage = get_storage()
    with session_factory() as session:
        check_readiness(session, storage)


def run_worker(
    *,
    once: bool,
    worker_id: str | None = None,
    stop_event: StopEvent | None = None,
    session_factory: SessionFactory = SessionLocal,
    storage: Storage | None = None,
) -> None:
    stop = stop_event or threading.Event()
    if stop.is_set():
        return
    if storage is None:
        storage = get_storage()
    check_worker_ready(session_factory=session_factory, storage=storage)
    if stop.is_set():
        return

    worker_id = worker_id or _default_worker_id()
    recovery_interval = min(
        30.0,
        max(0.1, settings.processing_job_lease_seconds / 2),
    )
    next_recovery = 0.0

    logger.info("Document worker %s started", worker_id)
    try:
        while True:
            if stop.is_set():
                logger.info(
                    "Shutdown requested; document worker %s will not claim another job",
                    worker_id,
                )
                break
            monotonic_now = time.monotonic()
            if monotonic_now >= next_recovery:
                recovery_saturated = False
                try:
                    recovered_total = 0
                    for _ in range(MAX_RECOVERY_BATCHES_PER_PASS):
                        if stop.is_set():
                            break
                        with session_factory() as session:
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
                        logger.info(
                            "Recovered %s expired processing jobs", recovered_total
                        )
                    with session_factory() as session:
                        queue = processing_queue_metrics(session)
                    emit_emf_metrics(
                        {
                            "QueuedJobs": queue.queued,
                            "RunningJobs": queue.running,
                            "FailedJobs": queue.failed,
                            "OldestQueuedAgeSeconds": queue.oldest_queued_age_seconds,
                            "RecoveredJobs": recovered_total,
                        },
                        dimensions={
                            "Service": "worker",
                            "Environment": settings.app_env,
                        },
                        units={"OldestQueuedAgeSeconds": "Seconds"},
                    )
                except Exception:
                    logger.exception("Failed to recover expired processing jobs")
                next_recovery = (
                    monotonic_now
                    if recovery_saturated
                    else monotonic_now + recovery_interval
                )

            if stop.is_set():
                break
            try:
                processed = process_next_job(
                    session_factory=session_factory,
                    storage=storage,
                    worker_id=worker_id,
                    shutdown_requested=stop.is_set,
                )
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
    finally:
        logger.info("Document worker %s stopped", worker_id)


def _install_shutdown_handlers(stop_event: _SignalStopEvent) -> None:
    def request_shutdown(_signum: int, _frame) -> None:
        stop_event.requested = True

    signal.signal(signal.SIGTERM, request_shutdown)
    signal.signal(signal.SIGINT, request_shutdown)


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Process durable document jobs")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="process at most one job")
    mode.add_argument(
        "--check",
        action="store_true",
        help="check dependencies without claiming work",
    )
    parser.add_argument("--worker-id", help="stable identifier shown in job leases")
    args = parser.parse_args(argv)
    configure_logging(service="worker", environment=settings.app_env)
    if args.check:
        try:
            check_worker_ready()
        except ReadinessError as exc:
            logger.error("Document worker readiness check failed: %s", exc)
            raise SystemExit(1) from None
        logger.info("Document worker readiness check succeeded")
        return

    stop_event = _SignalStopEvent()
    _install_shutdown_handlers(stop_event)
    try:
        run_worker(once=args.once, worker_id=args.worker_id, stop_event=stop_event)
    except ReadinessError as exc:
        logger.error("Document worker readiness check failed: %s", exc)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
