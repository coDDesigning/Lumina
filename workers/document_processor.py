"""Separate process entry point for durable document extraction."""

import argparse
import logging
import multiprocessing
import os
import signal
import socket
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from typing import Protocol
from uuid import uuid4

from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from backend.app.config import settings
from backend.app.database import SessionLocal
from backend.app.models import Course, JOB_TYPE_DESCRIBE_VISUALS
from backend.app.observability import (
    bind_operation_context,
    bind_request_id,
    configure_logging,
    emit_emf_metrics,
    reset_operation_context,
    reset_request_id,
)
from backend.app.readiness import ReadinessError, check_readiness
from schemas.ai_usage import GenerationType
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
from services.exam_question_extraction import extract_past_exam_questions
from services.ai_usage_logger import AiUsageLogger
from services.document_pipeline import (
    _MAX_VISUAL_DESCRIPTION_CHARACTERS,
    VisualDescription,
    VisualType,
)
from services.image_understanding import (
    ImageUnderstandingUsage,
    get_image_understanding_provider,
)
from services.processing_jobs import (
    ClaimedJob,
    ClaimedProfileJob,
    ChunkData,
    PageData,
    claim_next_describe_job,
    claim_next_job,
    claim_next_profile_describe_job,
    claim_next_profile_job,
    complete_describe_job,
    complete_profile_describe_job,
    enqueue_describe_visuals_job_if_deferred,
    enqueue_profile_describe_visuals_job_if_deferred,
    complete_job,
    complete_profile_job,
    fail_describe_job,
    fail_job,
    fail_profile_describe_job,
    fail_profile_job,
    heartbeat_job,
    heartbeat_profile_job,
    processing_queue_metrics,
    recover_expired_jobs,
    replace_document_pages,
    replace_profile_document_pages,
    record_profile_visual_description,
    record_visual_description,
    stored_profile_visual_descriptions,
    stored_visual_descriptions,
    sweep_documents_needing_visual_description,
    sweep_profile_documents_needing_visual_description,
    update_job_stage,
    update_profile_job_stage,
)
from services.embeddings import EmbeddingProvider
from services.vector_store import VectorStore, VectorStoreError
from storage.base import Storage
from storage.dependencies import get_storage
from services.document_lock import release_expired_generation_locks
from workers.ai_usage_cleanup import run_cleanup as run_ai_usage_cleanup
from workers.course_purge import run_account_purge, run_document_purge, run_purge
from workers.embedding_backfill import run_backfill

logger = logging.getLogger(__name__)


def _processing_job_type(job: ClaimedJob | ClaimedProfileJob) -> str:
    return (
        "profile_document_processing"
        if isinstance(job, ClaimedProfileJob)
        else "course_document_processing"
    )


def _job_log_fields(job: ClaimedJob | ClaimedProfileJob) -> dict[str, object]:
    fields: dict[str, object] = {
        "job_id": job.id,
        "job_type": _processing_job_type(job),
        "document_id": str(job.document_id),
        "attempt_number": job.attempt_count,
    }
    if isinstance(job, ClaimedJob):
        fields["course_id"] = job.course_id
    if job.user_id is not None:
        fields["user_id"] = job.user_id
    return fields


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


def _is_statement_or_lock_timeout(exc: OperationalError) -> bool:
    """Detect PostgreSQL cancelling a statement for exceeding its timeout budget.

    Distinguishing this from other ``OperationalError``s turns an anonymous
    "Failed to finalize" into an actionable signal that the transaction-local
    timeout (see ``operation_timeout_seconds`` in ``services.processing_jobs``)
    was too tight for this job, rather than a genuine database failure.
    """
    message = str(getattr(exc, "orig", None) or exc).lower()
    return "canceling statement due to statement timeout" in message or (
        "canceling statement due to lock timeout" in message
    )


def _heartbeat_loop(
    session_factory: SessionFactory,
    job: ClaimedJob | ClaimedProfileJob,
    lease_seconds: int,
    stop: threading.Event,
    claim_lost: threading.Event,
) -> None:
    interval = min(30.0, max(0.05, lease_seconds / 3))
    consecutive_failures = 0
    while not stop.is_set():
        try:
            with session_factory() as session:
                if isinstance(job, ClaimedProfileJob):
                    current = heartbeat_profile_job(
                        session, job.id, job.claim_token, lease_seconds
                    )
                else:
                    current = heartbeat_job(
                        session, job.id, job.claim_token, lease_seconds
                    )
        except Exception:
            consecutive_failures += 1
            if consecutive_failures == 1:
                logger.exception(
                    "Failed to heartbeat processing job %s",
                    job.id,
                    extra={
                        "event": "processing_heartbeat_failed",
                        **_job_log_fields(job),
                    },
                )
            else:
                logger.warning(
                    "Still failing to heartbeat processing job %s (%s in a row)",
                    job.id,
                    consecutive_failures,
                    extra={
                        "event": "processing_heartbeat_failed",
                        **_job_log_fields(job),
                    },
                )
            if stop.wait(interval):
                return
            continue
        if consecutive_failures:
            logger.warning(
                "Heartbeat for processing job %s recovered after %s failures",
                job.id,
                consecutive_failures,
                extra={
                    "event": "processing_heartbeat_recovered",
                    **_job_log_fields(job),
                },
            )
            consecutive_failures = 0
        if not current:
            claim_lost.set()
            return
        if stop.wait(interval):
            return


def _record_failure(
    session_factory: SessionFactory,
    job: ClaimedJob | ClaimedProfileJob,
    error: DocumentProcessingError,
    active_stage: str = "extracting_text",
) -> str | None:
    exponent = min(6, max(0, job.attempt_count - 1))
    retry_delay = min(60.0, 2.0**exponent)
    with session_factory() as session:
        if isinstance(job, ClaimedProfileJob):
            profile_failer = (
                fail_profile_describe_job
                if job.job_type == JOB_TYPE_DESCRIBE_VISUALS
                else fail_profile_job
            )
            resulting_status = profile_failer(
                session,
                job.id,
                job.claim_token,
                error_code=error.code,
                error_message=str(error),
                retryable=error.retryable,
                retry_delay_seconds=retry_delay,
            )
        elif job.job_type == JOB_TYPE_DESCRIBE_VISUALS:
            resulting_status = fail_describe_job(
                session,
                job.id,
                job.claim_token,
                error_code=error.code,
                error_message=str(error),
                retryable=error.retryable,
                retry_delay_seconds=retry_delay,
            )
        else:
            resulting_status = fail_job(
                session,
                job.id,
                job.claim_token,
                error_code=error.code,
                error_message=str(error),
                retryable=error.retryable,
                retry_delay_seconds=retry_delay,
            )
    stage = getattr(error, "failed_stage", None) or active_stage or "extracting_text"
    if (
        resulting_status == "failed"
        or not error.retryable
        or job.attempt_count >= job.max_attempts
    ):
        scope_info = (
            f"user {job.user_id}"
            if isinstance(job, ClaimedProfileJob)
            else f"course {job.course_id}"
        )
        extra_fields = {
            "job_id": job.id,
            "document_id": str(job.document_id),
            "failed_stage": stage,
            "error_code": error.code,
            "runbook": "docs/runbooks/stuck_document.md",
        }
        if isinstance(job, ClaimedJob):
            extra_fields["course_id"] = job.course_id
        else:
            extra_fields["user_id"] = job.user_id
        logger.error(
            "Permanent document processing failure for job %s (document %s, %s): %s",
            job.id,
            job.document_id,
            scope_info,
            error.code,
            extra={"event": "permanent_document_failure", **extra_fields},
        )
    if resulting_status is not None:
        attempt_fields = {
            "job_id": job.id,
            "job_type": _processing_job_type(job),
            "job_status": resulting_status,
            "attempt_number": job.attempt_count,
            "document_id": str(job.document_id),
            "course_id": job.course_id if isinstance(job, ClaimedJob) else None,
            "user_id": job.user_id,
            "failed_stage": stage,
            "error_code": error.code,
        }
        if resulting_status == "queued":
            logger.warning(
                "Document processing attempt ended; job %s will be retried",
                job.id,
                extra={"event": "processing_job_retried", **attempt_fields},
            )
        else:
            logger.warning(
                "Document processing attempt ended; job %s failed",
                job.id,
                extra={"event": "processing_job_failed", **attempt_fields},
            )
    return resulting_status


def _record_image_usage(
    session_factory: SessionFactory,
    job: ClaimedJob | ClaimedProfileJob,
    usage: ImageUnderstandingUsage,
) -> None:
    user_id = job.user_id
    if user_id is None:
        logger.warning(
            "Skipping image understanding usage without an owner",
            extra={"event": "image_understanding_usage_owner_missing"},
        )
        return
    try:
        with session_factory() as session:
            AiUsageLogger.log_usage(
                session,
                user_id=user_id,
                course_id=job.course_id if isinstance(job, ClaimedJob) else None,
                generation_type=GenerationType.IMAGE_UNDERSTANDING,
                provider=usage.provider,
                model=usage.model,
                prompt_tokens=usage.prompt_tokens,
                completion_tokens=usage.completion_tokens,
                total_tokens=usage.total_tokens,
                latency_ms=usage.latency_ms,
                success=usage.success,
                error_category=usage.error_category,
            )
            session.commit()
    except Exception:
        logger.warning(
            "Failed to persist image understanding usage",
            extra={"event": "image_understanding_usage_persist_failed"},
        )


class _ResumingImageUnderstandingProvider:
    """Charges the model only for visuals no earlier attempt already described."""

    def __init__(self, delegate, resume_cache, connection) -> None:
        self._delegate = delegate
        self._resume_cache = resume_cache
        self._connection = connection

    @property
    def enabled(self) -> bool:
        return self._delegate.enabled

    def describe_visual(
        self,
        visual_png: bytes,
        *,
        page_number: int,
        visual_index: int,
        suggested_type: VisualType,
    ) -> VisualDescription | None:
        cached = self._resume_cache.get((page_number, visual_index))
        if cached is not None:
            visual_type, description = cached
            return VisualDescription(
                visual_type=VisualType(visual_type),
                description=description,
            )

        result = self._delegate.describe_visual(
            visual_png,
            page_number=page_number,
            visual_index=visual_index,
            suggested_type=suggested_type,
        )
        if result is None:
            return None
        self._connection.send(
            (
                "visual",
                page_number,
                visual_index,
                str(result.visual_type),
                result.description,
            )
        )
        return result


def _describe_visuals_process(
    connection,
    storage: Storage,
    job: ClaimedJob | ClaimedProfileJob,
    prompt_context: PromptContext | None = None,
    resume_cache: dict[tuple[int, int], tuple[str, str]] | None = None,
) -> None:
    configure_logging(
        service="worker",
        environment=settings.app_env,
        persistence_path=(
            settings.operational_log_path
            if settings.operational_log_persistence_enabled
            else None
        ),
        retention_days=settings.operational_log_retention_days,
        max_records=settings.operational_log_max_records,
    )
    if job.correlation_id is not None:
        bind_request_id(job.correlation_id)
    bind_operation_context(
        operation_id=(
            f"processing_job:describe:{'profile' if isinstance(job, ClaimedProfileJob) else 'course'}:{job.id}"
        ),
        parent_operation_id=job.parent_operation_id,
        job_id=job.id,
        job_type="course_document_visual_description",
        attempt_number=job.attempt_count,
    )
    for shutdown_signal in WORKER_SHUTDOWN_SIGNALS:
        signal.signal(shutdown_signal, signal.SIG_IGN)
    if hasattr(signal, "pthread_sigmask"):
        signal.pthread_sigmask(signal.SIG_UNBLOCK, WORKER_SHUTDOWN_SIGNALS)
    try:

        def report_stage(stage) -> None:
            connection.send(("stage", stage.value))

        def report_image_usage(usage: ImageUnderstandingUsage) -> None:
            connection.send(("image_usage", usage))

        provider = _ResumingImageUnderstandingProvider(
            get_image_understanding_provider(
                prompt_context=prompt_context,
                usage_callback=report_image_usage,
            ),
            resume_cache or {},
            connection,
        )
        result = extract_document(
            storage,
            storage_provider=job.storage_provider,
            storage_key=job.storage_key,
            expected_hash=job.file_hash,
            expected_size=job.file_size,
            file_type=job.file_type,
            stage_callback=report_stage,
            prompt_context=prompt_context,
            image_provider=provider,
            inline_visual_budget=None,
        )
        connection.send(("succeeded", result.pages, result.chunks))
    except DocumentProcessingError as exc:
        logger.info(
            "Visual description failed for job %s with code %s",
            job.id,
            exc.code,
            extra={
                "event": "processing_job_failed",
                "error_code": exc.code,
                **_job_log_fields(job),
            },
        )
        connection.send(("failed", exc.code, str(exc), exc.retryable))
    except Exception as exc:
        logger.exception(
            "Visual description failed unexpectedly for job %s",
            job.id,
            extra={
                "event": "processing_job_failed",
                "error_code": "UNEXPECTED_PROCESSING_ERROR",
                "job_id": job.id,
            },
        )
        connection.send(("unexpected", type(exc).__name__))
    finally:
        connection.close()


def _extraction_process(
    connection,
    storage: Storage,
    job: ClaimedJob | ClaimedProfileJob,
    prompt_context: PromptContext | None = None,
) -> None:
    # A "spawn" child re-imports this module as __mp_main__, so the __main__
    # guard never runs and configure_logging was never applied here: without
    # this call the pipeline's 19 logging sites fall back to logging.lastResort,
    # emitting raw multi-line tracebacks and un-redacted exception text with no
    # request_id, and dropping every INFO record (P2-025).
    configure_logging(
        service="worker",
        environment=settings.app_env,
        persistence_path=(
            settings.operational_log_path
            if settings.operational_log_persistence_enabled
            else None
        ),
        retention_days=settings.operational_log_retention_days,
        max_records=settings.operational_log_max_records,
    )
    if job.correlation_id is not None:
        bind_request_id(job.correlation_id)
    bind_operation_context(
        operation_id=(
            f"processing_job:{'profile' if isinstance(job, ClaimedProfileJob) else 'course'}:{job.id}"
        ),
        parent_operation_id=job.parent_operation_id,
        job_id=job.id,
        job_type=_processing_job_type(job),
        attempt_number=job.attempt_count,
    )
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

        def report_image_usage(usage: ImageUnderstandingUsage) -> None:
            connection.send(("image_usage", usage))

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
            image_usage_callback=report_image_usage,
            inline_visual_budget=settings.image_understanding_inline_max_visuals,
        )
        connection.send(("succeeded", result.pages, result.chunks))
    except DocumentProcessingError as exc:
        logger.info(
            "Document processing failed for job %s with code %s",
            job.id,
            exc.code,
            extra={
                "event": "processing_job_failed",
                "error_code": exc.code,
                **_job_log_fields(job),
            },
        )
        connection.send(("failed", exc.code, str(exc), exc.retryable))
    except Exception as exc:
        logger.exception(
            "Document processing failed unexpectedly for job %s",
            job.id,
            extra={
                "event": "processing_job_failed",
                "error_code": "UNEXPECTED_PROCESSING_ERROR",
                "job_id": job.id,
            },
        )
        connection.send(("unexpected", type(exc).__name__))
    finally:
        connection.close()


def _extract_with_timeout(
    storage: Storage,
    job: ClaimedJob | ClaimedProfileJob,
    timeout_seconds: int,
    stage_callback: Callable[[str], None] | None = None,
    extraction_callback: Callable[[list[PageData], float], None] | None = None,
    image_usage_callback: Callable[[ImageUnderstandingUsage], None] | None = None,
    *,
    prompt_context: PromptContext | None = None,
    visual_callback: Callable[[int, int, str, str], None] | None = None,
    resume_cache: dict[tuple[int, int], tuple[str, str]] | None = None,
):
    context = multiprocessing.get_context("spawn")
    parent_connection, child_connection = context.Pipe(duplex=True)
    if resume_cache is None:
        target = _extraction_process
        process_args = (child_connection, storage, job, prompt_context)
    else:
        target = _describe_visuals_process
        process_args = (child_connection, storage, job, prompt_context, resume_cache)
    process = context.Process(
        target=target,
        args=process_args,
        daemon=False,
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
                    if result[0] == "visual":
                        if (
                            len(result) != 5
                            or not isinstance(result[1], int)
                            or not isinstance(result[2], int)
                            or not isinstance(result[3], str)
                            or not isinstance(result[4], str)
                            or not result[4].strip()
                            or len(result[4]) > _MAX_VISUAL_DESCRIPTION_CHARACTERS
                        ):
                            break
                        if visual_callback is not None:
                            visual_callback(result[1], result[2], result[3], result[4])
                        result = None
                        continue
                    if result[0] == "image_usage":
                        if len(result) != 2 or not isinstance(
                            result[1], ImageUnderstandingUsage
                        ):
                            break
                        if image_usage_callback is not None:
                            image_usage_callback(result[1])
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
    if result[0] == "unexpected":
        logger.error(
            "A document processing subprocess ended unexpectedly",
            extra={
                "event": "processing_job_failed",
                "error_code": "UNEXPECTED_PROCESSING_ERROR",
                "exception_type": (
                    result[1]
                    if len(result) == 2 and isinstance(result[1], str)
                    else None
                ),
            },
        )
        raise DocumentProcessingError(
            "UNEXPECTED_PROCESSING_ERROR",
            "Document processing failed unexpectedly.",
            retryable=True,
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
        logger.error(
            "Heartbeat thread did not stop before the processing job was finalized",
            extra={"event": "processing_heartbeat_stop_timeout"},
        )
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
    claim_describe: bool = True,
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
            max_active_per_user=settings.processing_job_max_active_per_user,
        )
        if job is None:
            job = claim_next_profile_job(
                session,
                worker_id,
                storage.provider,
                lease_seconds,
                max_active_per_user=settings.processing_job_max_active_per_user,
            )
        if job is None and claim_describe:
            job = claim_next_describe_job(
                session,
                worker_id,
                storage.provider,
                lease_seconds,
            )
        if job is None and claim_describe:
            job = claim_next_profile_describe_job(
                session,
                worker_id,
                storage.provider,
                lease_seconds,
            )
        if job is not None:
            if isinstance(job, ClaimedJob):
                prompt_context = resolve_prompt_context(
                    session,
                    course=session.get(Course, job.course_id),
                    document_ids=[job.document_id],
                )
            else:
                prompt_context = resolve_prompt_context(
                    session,
                    user_id=job.user_id,
                )
        else:
            prompt_context = None
    if job is None:
        return False
    request_token = bind_request_id(job.correlation_id)
    operation_token = bind_operation_context(
        operation_id=(
            f"processing_job:{'profile' if isinstance(job, ClaimedProfileJob) else 'course'}:{job.id}"
        ),
        parent_operation_id=job.parent_operation_id,
        job_id=job.id,
        job_type=_processing_job_type(job),
        attempt_number=job.attempt_count,
    )
    try:
        processing_started = time.monotonic()
        logger.info(
            "Document processing job claimed",
            extra={
                "event": "processing_job_claimed",
                "document_id": str(job.document_id),
                "course_id": job.course_id if isinstance(job, ClaimedJob) else None,
                "user_id": job.user_id,
                "job_status": "running",
                "worker_id": worker_id,
            },
        )

        describing_visuals = job.job_type == JOB_TYPE_DESCRIBE_VISUALS
        stop = threading.Event()
        claim_lost = threading.Event()
        current_stage = "reading_file"
        heartbeat = threading.Thread(
            target=_heartbeat_loop,
            args=(session_factory, job, lease_seconds, stop, claim_lost),
            daemon=True,
        )
        heartbeat.start()
        try:

            def persist_stage(stage: str) -> None:
                nonlocal current_stage
                current_stage = stage
                with session_factory() as session:
                    if isinstance(job, ClaimedProfileJob):
                        updated = update_profile_job_stage(
                            session,
                            job.id,
                            job.claim_token,
                            stage,
                        )
                    else:
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
                logger.info(
                    "Document processing stage started",
                    extra={
                        "event": "processing_stage_started",
                        "stage": stage,
                        "document_id": str(job.document_id),
                    },
                )

            def persist_visual(
                page_number: int,
                visual_index: int,
                visual_type: str,
                description: str,
            ) -> None:
                with session_factory() as session:
                    recorder = (
                        record_profile_visual_description
                        if isinstance(job, ClaimedProfileJob)
                        else record_visual_description
                    )
                    recorded = recorder(
                        session,
                        job.id,
                        job.claim_token,
                        page_number=page_number,
                        visual_index=visual_index,
                        visual_type=visual_type,
                        description=description,
                    )
                if not recorded:
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
                        if isinstance(job, ClaimedProfileJob):
                            updated = replace_profile_document_pages(
                                session,
                                job.id,
                                job.claim_token,
                                pages,
                                operation_timeout_seconds=remaining_seconds,
                            )
                        else:
                            updated = replace_document_pages(
                                session,
                                job.id,
                                job.claim_token,
                                pages,
                                operation_timeout_seconds=remaining_seconds,
                            )
                except Exception:
                    logger.exception(
                        "Failed to persist raw pages for job %s",
                        job.id,
                        extra={
                            "event": "processing_pages_persist_failed",
                            "stage": "extracting_text",
                            **_job_log_fields(job),
                        },
                    )
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

            if describing_visuals:
                with session_factory() as session:
                    if isinstance(job, ClaimedProfileJob):
                        resume_cache = stored_profile_visual_descriptions(
                            session, job.document_id
                        )
                    else:
                        resume_cache = stored_visual_descriptions(
                            session, job.document_id
                        )
                pages, chunks = _extract_with_timeout(
                    storage,
                    job,
                    settings.describe_visuals_attempt_timeout_seconds,
                    stage_callback=persist_stage,
                    image_usage_callback=lambda usage: _record_image_usage(
                        session_factory, job, usage
                    ),
                    prompt_context=prompt_context,
                    visual_callback=persist_visual,
                    resume_cache=resume_cache,
                )
            else:
                pages, chunks = _extract_with_timeout(
                    storage,
                    job,
                    settings.processing_job_attempt_timeout_seconds,
                    stage_callback=persist_stage,
                    extraction_callback=persist_extraction,
                    image_usage_callback=lambda usage: _record_image_usage(
                        session_factory, job, usage
                    ),
                    prompt_context=prompt_context,
                )
            persist_stage(EMBEDDING_STAGE)
            embeddings = embed_document_chunks(
                [chunk.text for chunk in chunks],
                provider=embedding_provider,
            )
        except WorkerProcessFatalError:
            _stop_heartbeat(stop, heartbeat, claim_lost)
            logger.critical(
                "Extraction subprocess for job %s could not be reaped; exiting worker",
                job.id,
                extra={
                    "event": "processing_subprocess_reap_failed",
                    "worker_id": worker_id,
                    **_job_log_fields(job),
                },
            )
            raise
        except DocumentProcessingError as exc:
            heartbeat_stopped = _stop_heartbeat(stop, heartbeat, claim_lost)
            stage = getattr(exc, "failed_stage", None) or current_stage
            if heartbeat_stopped and not claim_lost.is_set():
                try:
                    _record_failure(session_factory, job, exc, active_stage=stage)
                except Exception:
                    logger.exception(
                        "Failed to record processing error for job %s",
                        job.id,
                        extra={
                            "event": "processing_failure_record_failed",
                            "failed_stage": stage,
                            "error_code": exc.code,
                            **_job_log_fields(job),
                        },
                    )
            emit_emf_metrics(
                {"JobsRetried" if exc.retryable else "JobsFailed": 1},
                dimensions={"Service": "worker", "Environment": settings.app_env},
            )
            emit_emf_metrics(
                {"StageRetried" if exc.retryable else "StageFailed": 1},
                dimensions={
                    "Service": "worker",
                    "Environment": settings.app_env,
                    "Stage": stage,
                },
            )
            return True
        except Exception:
            logger.exception(
                "Unexpected processing failure for job %s",
                job.id,
                extra={
                    "event": "processing_job_failed",
                    "error_code": "UNEXPECTED_PROCESSING_ERROR",
                    "stage": current_stage,
                    **_job_log_fields(job),
                },
            )
            heartbeat_stopped = _stop_heartbeat(stop, heartbeat, claim_lost)
            stage = current_stage
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
                        active_stage=stage,
                    )
                except Exception:
                    logger.exception(
                        "Failed to record processing error for job %s",
                        job.id,
                        extra={
                            "event": "processing_failure_record_failed",
                            "failed_stage": stage,
                            "error_code": "UNEXPECTED_PROCESSING_ERROR",
                            **_job_log_fields(job),
                        },
                    )
            emit_emf_metrics(
                {"JobsRetried": 1},
                dimensions={"Service": "worker", "Environment": settings.app_env},
            )
            emit_emf_metrics(
                {"StageRetried": 1},
                dimensions={
                    "Service": "worker",
                    "Environment": settings.app_env,
                    "Stage": stage,
                },
            )
            return True

        if not _stop_heartbeat(stop, heartbeat, claim_lost) or claim_lost.is_set():
            return True
        try:
            with session_factory() as session:
                if describing_visuals and isinstance(job, ClaimedProfileJob):
                    completed = complete_profile_describe_job(
                        session,
                        job.id,
                        job.claim_token,
                        chunks,
                        pages,
                        embeddings=embeddings,
                        vector_store=vector_store,
                        operation_timeout_seconds=lease_seconds,
                    )
                elif describing_visuals:
                    completed = complete_describe_job(
                        session,
                        job.id,
                        job.claim_token,
                        chunks,
                        pages,
                        embeddings=embeddings,
                        vector_store=vector_store,
                        operation_timeout_seconds=lease_seconds,
                    )
                elif isinstance(job, ClaimedProfileJob):
                    completed = complete_profile_job(
                        session,
                        job.id,
                        job.claim_token,
                        chunks,
                        embeddings,
                        pages=pages,
                        vector_store=vector_store,
                        operation_timeout_seconds=lease_seconds,
                    )
                else:
                    completed = complete_job(
                        session,
                        job.id,
                        job.claim_token,
                        chunks,
                        pages,
                        embeddings=embeddings,
                        vector_store=vector_store,
                        operation_timeout_seconds=lease_seconds,
                    )
        except VectorStoreError as exc:
            # The vector store is classified, so the job requeues instead of waiting
            # for the lease to expire.
            logger.warning(
                "Vector persistence failed while finalizing job %s",
                job.id,
                extra={
                    "event": "processing_vector_persist_failed",
                    "stage": EMBEDDING_STAGE,
                    **_job_log_fields(job),
                },
            )
            try:
                _record_failure(
                    session_factory,
                    job,
                    classify_embedding_error(exc),
                    active_stage=EMBEDDING_STAGE,
                )
            except Exception:
                logger.exception(
                    "Failed to record vector error for job %s",
                    job.id,
                    extra={
                        "event": "processing_failure_record_failed",
                        "failed_stage": EMBEDDING_STAGE,
                        **_job_log_fields(job),
                    },
                )
            emit_emf_metrics(
                {"JobsRetried": 1},
                dimensions={"Service": "worker", "Environment": settings.app_env},
            )
            emit_emf_metrics(
                {"StageRetried": 1},
                dimensions={
                    "Service": "worker",
                    "Environment": settings.app_env,
                    "Stage": EMBEDDING_STAGE,
                },
            )
            return True
        except OperationalError as exc:
            # Leave the fenced running state intact; periodic recovery safely retries it.
            if _is_statement_or_lock_timeout(exc):
                logger.error(
                    "Finalizing job %s exceeded its database statement/lock "
                    "timeout budget",
                    job.id,
                    extra={
                        "event": "processing_job_finalize_timeout",
                        "reason": "statement_timeout",
                        **_job_log_fields(job),
                    },
                )
            else:
                logger.exception(
                    "Failed to finalize processing job %s",
                    job.id,
                    extra={
                        "event": "processing_job_finalize_failed",
                        "reason": "database_error",
                        **_job_log_fields(job),
                    },
                )
            return True
        except ValueError:
            # A rejected payload is deterministic: recovery would replay the same
            # bytes into the same refusal forever, so the job is failed here
            # instead of being left running for the lease to expire.
            logger.exception(
                "Refused to finalize processing job %s because its payload was invalid",
                job.id,
                extra={
                    "event": "processing_job_finalize_failed",
                    "reason": "invalid_payload",
                    "error_code": "COMPLETION_PAYLOAD_INVALID",
                    **_job_log_fields(job),
                },
            )
            failure = DocumentProcessingError(
                "COMPLETION_PAYLOAD_INVALID",
                "The extracted document could not be recorded.",
                retryable=False,
                failed_stage=EMBEDDING_STAGE,
            )
            try:
                _record_failure(
                    session_factory, job, failure, active_stage=EMBEDDING_STAGE
                )
            except Exception:
                logger.exception(
                    "Failed to record completion refusal for job %s",
                    job.id,
                    extra={
                        "event": "processing_failure_record_failed",
                        "failed_stage": EMBEDDING_STAGE,
                        "error_code": "COMPLETION_PAYLOAD_INVALID",
                        **_job_log_fields(job),
                    },
                )
            emit_emf_metrics(
                {"JobsFailed": 1},
                dimensions={"Service": "worker", "Environment": settings.app_env},
            )
            return True
        except Exception:
            # Leave the fenced running state intact; periodic recovery safely retries it.
            logger.exception(
                "Failed to finalize processing job %s",
                job.id,
                extra={
                    "event": "processing_job_finalize_failed",
                    "reason": "unexpected_error",
                    **_job_log_fields(job),
                },
            )
            return True
        if not completed:
            logger.info(
                "Processing claim was lost before job %s completed",
                job.id,
                extra={
                    "event": "processing_claim_lost",
                    "reason": "lease_lost",
                    "worker_id": worker_id,
                    **_job_log_fields(job),
                },
            )
        else:
            # Past exam papers give up their questions here, after the document
            # is ready and its chunks exist. It is best-effort by design: a
            # paper whose questions could not be read is still indexed material,
            # and the failure belongs on the document rather than on the job.
            if not describing_visuals:
                if isinstance(job, ClaimedJob):
                    extract_past_exam_questions(session_factory, job.document_id)
                try:
                    with session_factory() as session:
                        if isinstance(job, ClaimedProfileJob):
                            enqueue_profile_describe_visuals_job_if_deferred(
                                session, job.document_id
                            )
                        else:
                            enqueue_describe_visuals_job_if_deferred(
                                session, job.document_id
                            )
                except Exception:
                    logger.exception(
                        "Failed to queue visual description for document %s",
                        job.document_id,
                        extra={
                            "event": "visual_description_enqueue_failed",
                            **_job_log_fields(job),
                        },
                    )
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
            logger.info(
                "Document processing job completed",
                extra={
                    "event": "processing_job_completed",
                    "document_id": str(job.document_id),
                    "course_id": job.course_id if isinstance(job, ClaimedJob) else None,
                    "user_id": job.user_id,
                    "job_status": "succeeded",
                    "duration_ms": round(
                        (time.monotonic() - processing_started) * 1000, 3
                    ),
                },
            )
        return True
    finally:
        reset_operation_context(operation_token)
        reset_request_id(request_token)


def check_worker_ready(
    *,
    session_factory: SessionFactory = SessionLocal,
    storage: Storage | None = None,
) -> None:
    if storage is None:
        storage = get_storage()
    with session_factory() as session:
        check_readiness(session, storage)


class _MaintenanceSchedule:
    def __init__(self) -> None:
        self.recovery_interval = min(
            30.0,
            max(0.1, settings.processing_job_lease_seconds / 2),
        )
        self.next_recovery = 0.0
        self.purge_interval = settings.course_purge_interval_seconds
        self.backfill_interval = settings.embedding_backfill_interval_seconds
        self.ai_usage_cleanup_interval = settings.ai_usage_cleanup_interval_seconds
        self.next_purge = 0.0 if self.purge_interval > 0 else float("inf")
        self.next_backfill = 0.0 if self.backfill_interval > 0 else float("inf")
        self.next_ai_usage_cleanup = (
            0.0 if self.ai_usage_cleanup_interval > 0 else float("inf")
        )
        self.visual_description_sweep_interval = (
            settings.visual_description_sweep_interval_seconds
        )
        self.next_visual_description_sweep = (
            0.0 if self.visual_description_sweep_interval > 0 else float("inf")
        )


class _CompositeStopEvent:
    def __init__(self, external: StopEvent) -> None:
        self._external = external
        self._internal = threading.Event()

    def set(self) -> None:
        self._internal.set()

    def is_set(self) -> bool:
        return self._internal.is_set() or self._external.is_set()

    def wait(self, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while not self.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            self._internal.wait(min(0.1, remaining))
        return self.is_set()


@contextmanager
def _maintenance_context(task: str) -> Iterator[None]:
    token = bind_operation_context(
        operation_id=f"maintenance:{task}", maintenance_task=task
    )
    try:
        yield
    finally:
        reset_operation_context(token)


def _maintenance_cycle(
    schedule: _MaintenanceSchedule,
    *,
    session_factory: SessionFactory,
    storage: Storage,
    stop: StopEvent,
) -> None:
    monotonic_now = time.monotonic()
    if monotonic_now >= schedule.next_recovery:
        recovery_saturated = False
        with _maintenance_context("processing_job_recovery"):
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
                        "Recovered %s expired processing jobs",
                        recovered_total,
                        extra={
                            "event": "processing_jobs_recovered",
                            "maintenance_task": "processing_job_recovery",
                        },
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
                logger.exception(
                    "Failed to recover expired processing jobs",
                    extra={
                        "event": "maintenance_task_failed",
                        "maintenance_task": "processing_job_recovery",
                    },
                )
        schedule.next_recovery = (
            monotonic_now
            if recovery_saturated
            else monotonic_now + schedule.recovery_interval
        )

    if stop.is_set():
        return

    if schedule.purge_interval > 0 and monotonic_now >= schedule.next_purge:
        with _maintenance_context("account_purge"):
            try:
                run_account_purge(
                    session_factory=session_factory,
                    storage=storage,
                    stop_event=stop,
                )
            except Exception:
                logger.exception(
                    "Periodic account purge reconciliation failed",
                    extra={
                        "event": "maintenance_task_failed",
                        "maintenance_task": "account_purge",
                    },
                )
        with _maintenance_context("course_purge"):
            try:
                run_purge(
                    session_factory=session_factory,
                    storage=storage,
                    stop_event=stop,
                )
            except Exception:
                logger.exception(
                    "Periodic course purge reconciliation failed",
                    extra={
                        "event": "maintenance_task_failed",
                        "maintenance_task": "course_purge",
                    },
                )
        with _maintenance_context("document_purge"):
            try:
                run_document_purge(
                    session_factory=session_factory,
                    storage=storage,
                    stop_event=stop,
                )
            except Exception:
                logger.exception(
                    "Periodic document purge reconciliation failed",
                    extra={
                        "event": "maintenance_task_failed",
                        "maintenance_task": "document_purge",
                    },
                )
        with _maintenance_context("generation_lock_release"):
            try:
                # A generation lock is released by the process that took it, so the
                # holds left behind are exactly the ones whose process is gone.
                with session_factory() as session:
                    expired_locks = release_expired_generation_locks(session)
                if expired_locks:
                    logger.warning(
                        "Released %s expired document generation locks",
                        expired_locks,
                        extra={
                            "event": "generation_locks_released",
                            "maintenance_task": "generation_lock_release",
                        },
                    )
            except Exception:
                logger.exception(
                    "Expired generation lock release failed",
                    extra={
                        "event": "maintenance_task_failed",
                        "maintenance_task": "generation_lock_release",
                    },
                )
        schedule.next_purge = monotonic_now + schedule.purge_interval

    if stop.is_set():
        return

    if schedule.backfill_interval > 0 and monotonic_now >= schedule.next_backfill:
        with _maintenance_context("embedding_backfill"):
            try:
                run_backfill(
                    session_factory=session_factory,
                    batch_size=settings.embedding_backfill_batch_size,
                    prune_orphans=settings.embedding_backfill_prune_orphans,
                    stop_event=stop,
                )
            except Exception:
                logger.exception(
                    "Periodic embedding backfill reconciliation failed",
                    extra={
                        "event": "maintenance_task_failed",
                        "maintenance_task": "embedding_backfill",
                    },
                )
        schedule.next_backfill = monotonic_now + schedule.backfill_interval

    if stop.is_set():
        return

    if (
        schedule.ai_usage_cleanup_interval > 0
        and monotonic_now >= schedule.next_ai_usage_cleanup
    ):
        with _maintenance_context("ai_usage_cleanup"):
            try:
                # Enforces AI_USAGE_RETENTION_DAYS on every deployment that runs a
                # worker, so per-user AI-usage telemetry does not accumulate without
                # bound (P2-024). The job is idempotent and bounded per batch.
                run_ai_usage_cleanup(session_factory=session_factory)
            except Exception:
                logger.exception(
                    "Periodic AI usage retention cleanup failed",
                    extra={
                        "event": "maintenance_task_failed",
                        "maintenance_task": "ai_usage_cleanup",
                    },
                )
        schedule.next_ai_usage_cleanup = (
            monotonic_now + schedule.ai_usage_cleanup_interval
        )

    if stop.is_set():
        return

    if (
        schedule.visual_description_sweep_interval > 0
        and monotonic_now >= schedule.next_visual_description_sweep
    ):
        with _maintenance_context("visual_description_sweep"):
            try:
                with session_factory() as session:
                    queued = sweep_documents_needing_visual_description(session)
                    queued += sweep_profile_documents_needing_visual_description(
                        session
                    )
                if queued:
                    logger.info(
                        "Queued %s documents for visual description",
                        queued,
                        extra={
                            "event": "visual_description_sweep",
                            "item_count": queued,
                        },
                    )
            except Exception:
                logger.exception(
                    "Periodic visual description sweep failed",
                    extra={
                        "event": "maintenance_task_failed",
                        "maintenance_task": "visual_description_sweep",
                    },
                )
        schedule.next_visual_description_sweep = (
            monotonic_now + schedule.visual_description_sweep_interval
        )


def _claim_once(
    *,
    session_factory: SessionFactory,
    storage: Storage,
    worker_id: str,
    stop: StopEvent,
    claim_describe: bool = False,
) -> bool:
    try:
        return process_next_job(
            session_factory=session_factory,
            storage=storage,
            worker_id=worker_id,
            shutdown_requested=stop.is_set,
            claim_describe=claim_describe,
        )
    except WorkerProcessFatalError:
        logger.critical(
            "Document worker %s requires a process recycle",
            worker_id,
            extra={"event": "document_worker_recycle_required", "worker_id": worker_id},
        )
        raise
    except Exception:
        logger.exception(
            "Document worker %s iteration failed",
            worker_id,
            extra={"event": "document_worker_iteration_failed", "worker_id": worker_id},
        )
        return False


def _run_worker_serially(
    *,
    once: bool,
    worker_id: str,
    session_factory: SessionFactory,
    storage: Storage,
    stop: StopEvent,
) -> None:
    schedule = _MaintenanceSchedule()
    while True:
        if stop.is_set():
            logger.info(
                "Shutdown requested; document worker %s will not claim another job",
                worker_id,
                extra={
                    "event": "document_worker_shutdown_requested",
                    "worker_id": worker_id,
                },
            )
            break
        _maintenance_cycle(
            schedule,
            session_factory=session_factory,
            storage=storage,
            stop=stop,
        )
        if stop.is_set():
            break
        processed = _claim_once(
            session_factory=session_factory,
            storage=storage,
            worker_id=worker_id,
            stop=stop,
        )
        if once:
            return
        if not processed:
            stop.wait(settings.processing_job_poll_seconds)


def _run_worker_slots(
    *,
    worker_id: str,
    concurrency: int,
    session_factory: SessionFactory,
    storage: Storage,
    stop: StopEvent,
) -> None:
    composite = _CompositeStopEvent(stop)
    fatal: list[BaseException] = []
    fatal_lock = threading.Lock()

    def coordinate() -> None:
        schedule = _MaintenanceSchedule()
        while not composite.is_set():
            _maintenance_cycle(
                schedule,
                session_factory=session_factory,
                storage=storage,
                stop=composite,
            )
            composite.wait(settings.processing_job_poll_seconds)

    def claim(slot_worker_id: str, claim_describe: bool) -> None:
        while not composite.is_set():
            try:
                processed = _claim_once(
                    session_factory=session_factory,
                    storage=storage,
                    worker_id=slot_worker_id,
                    stop=composite,
                    claim_describe=claim_describe,
                )
            except BaseException as exc:
                with fatal_lock:
                    fatal.append(exc)
                composite.set()
                return
            if not processed:
                composite.wait(settings.processing_job_poll_seconds)
        logger.info(
            "Shutdown requested; document worker %s will not claim another job",
            slot_worker_id,
            extra={
                "event": "document_worker_shutdown_requested",
                "worker_id": slot_worker_id,
            },
        )

    threads = [threading.Thread(target=coordinate, daemon=True)]
    threads.extend(
        threading.Thread(
            target=claim,
            args=(f"{worker_id}:slot-{index}", index > 0),
            daemon=True,
        )
        for index in range(concurrency)
    )
    for thread in threads:
        thread.start()
    try:
        while any(thread.is_alive() for thread in threads):
            if composite.is_set():
                break
            composite.wait(0.1)
    finally:
        composite.set()
        deadline = time.monotonic() + (
            max(
                settings.processing_job_attempt_timeout_seconds,
                settings.describe_visuals_attempt_timeout_seconds,
            )
            + HEARTBEAT_SHUTDOWN_SECONDS
        )
        for thread in threads:
            thread.join(timeout=max(0.0, deadline - time.monotonic()))

    with fatal_lock:
        if fatal:
            raise fatal[0]


def run_worker(
    *,
    once: bool,
    worker_id: str | None = None,
    stop_event: StopEvent | None = None,
    session_factory: SessionFactory = SessionLocal,
    storage: Storage | None = None,
    concurrency: int = 1,
) -> None:
    if concurrency < 1:
        raise ValueError("concurrency must be positive")
    stop = stop_event or threading.Event()
    if stop.is_set():
        return
    if storage is None:
        storage = get_storage()
    check_worker_ready(session_factory=session_factory, storage=storage)
    if stop.is_set():
        return

    worker_id = worker_id or _default_worker_id()
    if once:
        concurrency = 1

    logger.info(
        "Document worker %s started",
        worker_id,
        extra={"event": "document_worker_started", "worker_id": worker_id},
    )
    try:
        if concurrency == 1:
            _run_worker_serially(
                once=once,
                worker_id=worker_id,
                session_factory=session_factory,
                storage=storage,
                stop=stop,
            )
        else:
            logger.info(
                "Document worker %s running %s concurrent job slots",
                worker_id,
                concurrency,
                extra={
                    "event": "document_worker_slots_started",
                    "worker_id": worker_id,
                },
            )
            _run_worker_slots(
                worker_id=worker_id,
                concurrency=concurrency,
                session_factory=session_factory,
                storage=storage,
                stop=stop,
            )
    finally:
        logger.info(
            "Document worker %s stopped",
            worker_id,
            extra={"event": "document_worker_stopped", "worker_id": worker_id},
        )


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
    configure_logging(
        service="worker",
        environment=settings.app_env,
        persistence_path=(
            settings.operational_log_path
            if settings.operational_log_persistence_enabled
            else None
        ),
        retention_days=settings.operational_log_retention_days,
        max_records=settings.operational_log_max_records,
    )
    if args.check:
        try:
            check_worker_ready()
        except ReadinessError as exc:
            logger.error(
                "Document worker readiness check failed: %s",
                exc,
                extra={
                    "event": "worker_readiness_check_failed",
                    "failed_stage": exc.check,
                },
            )
            raise SystemExit(1) from None
        logger.info(
            "Document worker readiness check succeeded",
            extra={"event": "worker_readiness_check_succeeded"},
        )
        return

    stop_event = _SignalStopEvent()
    _install_shutdown_handlers(stop_event)
    try:
        run_worker(
            once=args.once,
            worker_id=args.worker_id,
            stop_event=stop_event,
            concurrency=settings.processing_job_concurrency,
        )
    except ReadinessError as exc:
        logger.error(
            "Document worker readiness check failed: %s",
            exc,
            extra={
                "event": "worker_readiness_check_failed",
                "failed_stage": exc.check,
            },
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
