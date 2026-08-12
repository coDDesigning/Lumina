"""Durable, fenced state transitions for document processing jobs."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from sqlalchemy import case, delete, func, select, update
from sqlalchemy.orm import Session

from backend.app.config import settings
from backend.app.database import begin_serialized_write
from backend.app.models import (
    DOCUMENT_PROCESSING_STAGES,
    JOB_STATUS_FAILED,
    JOB_STATUS_QUEUED,
    JOB_STATUS_RUNNING,
    JOB_STATUS_SUCCEEDED,
    JOB_TYPE_EXTRACT_DOCUMENT,
    Course,
    DocumentChunk,
    ProcessingJob,
    UploadedDocument,
)


@dataclass(frozen=True, slots=True)
class ChunkData:
    text: str
    page_number: int | None = None


@dataclass(frozen=True, slots=True)
class ClaimedJob:
    id: int
    document_id: UUID
    course_id: int
    claim_token: str
    attempt_count: int
    max_attempts: int
    storage_provider: str
    storage_key: str
    file_hash: str
    file_type: str
    file_size: int


class ProcessingJobStateError(RuntimeError):
    """A requested transition is not valid for the current durable state."""


_EXPECTED_STAGES = {
    "validating": ("validating",),
    "extracting_text": ("validating", "extracting_text"),
    "running_ocr": ("extracting_text", "running_ocr"),
    "understanding_images": (
        "extracting_text",
        "running_ocr",
        "understanding_images",
    ),
    "cleaning_text": (
        "extracting_text",
        "running_ocr",
        "understanding_images",
        "cleaning_text",
    ),
    "chunking": ("cleaning_text", "chunking"),
}


def _supplied_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Job timestamps must be timezone-aware")
    return value.astimezone(timezone.utc)


def _database_now(session: Session, supplied: datetime | None = None) -> datetime:
    if supplied is not None:
        return _supplied_utc(supplied)

    if session.get_bind().dialect.name == "postgresql":
        value = session.scalar(select(func.clock_timestamp()))
    else:
        value = session.scalar(select(func.current_timestamp()))
    if value is None:
        raise RuntimeError("Database did not return its current timestamp")
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _start_transition(session: Session) -> None:
    if session.in_transaction():
        session.rollback()
    begin_serialized_write(session)


def _clear_lease(job: ProcessingJob) -> None:
    job.lease_owner = None
    job.claim_token = None
    job.claimed_at = None
    job.heartbeat_at = None
    job.lease_expires_at = None


def _public_error_message(message: str) -> str:
    normalized = " ".join(message.split())
    return (normalized or "Document processing failed.")[:500]


def enqueue_document_job(
    session: Session,
    document: UploadedDocument,
    *,
    max_attempts: int | None = None,
    now: datetime | None = None,
) -> ProcessingJob:
    """Add an extraction job to the caller's document transaction."""
    if max_attempts is None:
        max_attempts = settings.processing_job_max_attempts
    if max_attempts <= 0:
        raise ValueError("max_attempts must be positive")

    available_at = _database_now(session, now)
    job = ProcessingJob(
        document=document,
        course_id=document.course_id,
        job_type=JOB_TYPE_EXTRACT_DOCUMENT,
        status=JOB_STATUS_QUEUED,
        attempt_count=0,
        max_attempts=max_attempts,
        available_at=available_at,
    )
    session.add(job)
    session.flush()
    return job


def claim_next_job(
    session: Session,
    worker_id: str,
    storage_provider: str,
    lease_seconds: int,
    *,
    now: datetime | None = None,
) -> ClaimedJob | None:
    worker_id = worker_id.strip()
    if not worker_id:
        raise ValueError("worker_id must not be empty")
    if not storage_provider.strip():
        raise ValueError("storage_provider must not be empty")
    if lease_seconds <= 0:
        raise ValueError("lease_seconds must be positive")

    _start_transition(session)
    eligibility_time = _database_now(session, now)
    dialect_name = session.get_bind().dialect.name

    statement = (
        select(ProcessingJob.id)
        .join(UploadedDocument, UploadedDocument.id == ProcessingJob.document_id)
        .join(Course, Course.id == ProcessingJob.course_id)
        .where(
            ProcessingJob.job_type == JOB_TYPE_EXTRACT_DOCUMENT,
            ProcessingJob.status == JOB_STATUS_QUEUED,
            ProcessingJob.available_at <= eligibility_time,
            ProcessingJob.attempt_count < ProcessingJob.max_attempts,
            UploadedDocument.status == "uploaded",
            UploadedDocument.storage_provider == storage_provider,
            Course.is_deleted.is_(False),
        )
        .order_by(ProcessingJob.available_at, ProcessingJob.id)
        .limit(1)
    )
    if dialect_name == "postgresql":
        statement = statement.with_for_update(of=ProcessingJob, skip_locked=True)

    job_id = session.scalar(statement)
    if job_id is None:
        session.rollback()
        return None

    detail_statement = (
        select(
            ProcessingJob.id,
            ProcessingJob.document_id,
            ProcessingJob.course_id,
            ProcessingJob.attempt_count,
            ProcessingJob.max_attempts,
            UploadedDocument.storage_provider,
            UploadedDocument.storage_key,
            UploadedDocument.file_hash,
            UploadedDocument.file_type,
            UploadedDocument.file_size,
        )
        .join(UploadedDocument, UploadedDocument.id == ProcessingJob.document_id)
        .where(
            ProcessingJob.id == job_id,
            UploadedDocument.status == "uploaded",
        )
    )
    if dialect_name == "postgresql":
        detail_statement = detail_statement.with_for_update(of=UploadedDocument)
    row = session.execute(detail_statement).one_or_none()
    if row is None:
        session.rollback()
        return None

    claimed_at = _database_now(session, now)
    lease_expires_at = claimed_at + timedelta(seconds=lease_seconds)
    claim_token = str(uuid4())
    result = session.execute(
        update(ProcessingJob)
        .where(
            ProcessingJob.id == job_id,
            ProcessingJob.status == JOB_STATUS_QUEUED,
            ProcessingJob.available_at <= claimed_at,
            ProcessingJob.attempt_count < ProcessingJob.max_attempts,
        )
        .values(
            status=JOB_STATUS_RUNNING,
            attempt_count=ProcessingJob.attempt_count + 1,
            started_at=case(
                (ProcessingJob.started_at.is_(None), claimed_at),
                else_=ProcessingJob.started_at,
            ),
            claimed_at=claimed_at,
            heartbeat_at=claimed_at,
            lease_expires_at=lease_expires_at,
            lease_owner=worker_id[:255],
            claim_token=claim_token,
            finished_at=None,
            last_error_code=None,
            last_error_message=None,
            processing_stage="validating",
            failed_stage=None,
            updated_at=claimed_at,
        )
    )
    if result.rowcount != 1:
        session.rollback()
        return None

    document_result = session.execute(
        update(UploadedDocument)
        .where(
            UploadedDocument.id == row.document_id,
            UploadedDocument.course_id == row.course_id,
            UploadedDocument.status == "uploaded",
        )
        .values(status="processing", processing_error=None, updated_at=claimed_at)
    )
    if document_result.rowcount != 1:
        session.rollback()
        return None

    session.commit()
    return ClaimedJob(
        id=row.id,
        document_id=row.document_id,
        course_id=row.course_id,
        claim_token=claim_token,
        attempt_count=row.attempt_count + 1,
        max_attempts=row.max_attempts,
        storage_provider=row.storage_provider,
        storage_key=row.storage_key,
        file_hash=row.file_hash,
        file_type=row.file_type,
        file_size=row.file_size,
    )


def heartbeat_job(
    session: Session,
    job_id: int,
    claim_token: str,
    lease_seconds: int,
    *,
    now: datetime | None = None,
) -> bool:
    if lease_seconds <= 0:
        raise ValueError("lease_seconds must be positive")

    _start_transition(session)
    heartbeat_at = _database_now(session, now)
    result = session.execute(
        update(ProcessingJob)
        .where(
            ProcessingJob.id == job_id,
            ProcessingJob.status == JOB_STATUS_RUNNING,
            ProcessingJob.claim_token == claim_token,
            ProcessingJob.lease_expires_at > heartbeat_at,
        )
        .values(
            heartbeat_at=heartbeat_at,
            lease_expires_at=heartbeat_at + timedelta(seconds=lease_seconds),
            updated_at=heartbeat_at,
        )
    )
    if result.rowcount != 1:
        session.rollback()
        return False
    session.commit()
    return True


def update_job_stage(
    session: Session,
    job_id: int,
    claim_token: str,
    stage: str,
    *,
    now: datetime | None = None,
) -> bool:
    if stage not in DOCUMENT_PROCESSING_STAGES:
        raise ValueError(f"Unsupported document processing stage: {stage}")

    _start_transition(session)
    updated_at = _database_now(session, now)
    result = session.execute(
        update(ProcessingJob)
        .where(
            ProcessingJob.id == job_id,
            ProcessingJob.status == JOB_STATUS_RUNNING,
            ProcessingJob.claim_token == claim_token,
            ProcessingJob.lease_expires_at > updated_at,
            ProcessingJob.processing_stage.in_(_EXPECTED_STAGES[stage]),
        )
        .values(processing_stage=stage, updated_at=updated_at)
    )
    if result.rowcount != 1:
        session.rollback()
        return False
    session.commit()
    return True


def complete_job(
    session: Session,
    job_id: int,
    claim_token: str,
    chunks: list[ChunkData],
    *,
    now: datetime | None = None,
) -> bool:
    if not chunks:
        raise ValueError("A completed document must contain at least one chunk")
    if len(chunks) > settings.max_document_chunks:
        raise ValueError("Document chunk count exceeds the configured limit")
    for chunk in chunks:
        if not isinstance(chunk.text, str) or not chunk.text:
            raise ValueError("Document chunks must contain text")
        if chunk.page_number is not None and chunk.page_number < 1:
            raise ValueError("Document chunk page numbers must be positive")

    _start_transition(session)
    course_id = session.scalar(
        select(ProcessingJob.course_id).where(ProcessingJob.id == job_id)
    )
    if course_id is None:
        session.rollback()
        return False

    course_statement = select(Course).where(Course.id == course_id)
    if session.get_bind().dialect.name == "postgresql":
        course_statement = course_statement.with_for_update(of=Course)
    course = session.scalar(course_statement)
    if course is None or course.is_deleted:
        session.rollback()
        return False

    statement = (
        select(ProcessingJob, UploadedDocument)
        .join(UploadedDocument, UploadedDocument.id == ProcessingJob.document_id)
        .where(ProcessingJob.id == job_id)
    )
    if session.get_bind().dialect.name == "postgresql":
        statement = statement.with_for_update(of=(ProcessingJob, UploadedDocument))
    row = session.execute(statement).one_or_none()
    if row is None:
        session.rollback()
        return False
    job, document = row
    finished_at = _database_now(session, now)
    if (
        job.status != JOB_STATUS_RUNNING
        or job.claim_token != claim_token
        or job.lease_expires_at is None
        or job.lease_expires_at <= finished_at
        or document.status != "processing"
    ):
        session.rollback()
        return False

    job.status = JOB_STATUS_SUCCEEDED
    job.finished_at = finished_at
    job.last_error_code = None
    job.last_error_message = None
    job.processing_stage = None
    job.failed_stage = None
    job.updated_at = finished_at
    _clear_lease(job)

    session.execute(
        delete(DocumentChunk).where(DocumentChunk.document_id == job.document_id)
    )
    session.add_all(
        DocumentChunk(
            document_id=job.document_id,
            course_id=job.course_id,
            chunk_index=index,
            page_number=chunk.page_number,
            text=chunk.text,
        )
        for index, chunk in enumerate(chunks)
    )
    document.status = "ready"
    document.processing_error = None
    document.updated_at = finished_at
    session.flush()
    session.commit()
    return True


def fail_job(
    session: Session,
    job_id: int,
    claim_token: str,
    *,
    error_code: str,
    error_message: str,
    retryable: bool,
    retry_delay_seconds: float = 0,
    now: datetime | None = None,
) -> str | None:
    if not error_code.strip():
        raise ValueError("error_code must not be empty")

    _start_transition(session)
    statement = (
        select(ProcessingJob, UploadedDocument)
        .join(UploadedDocument, UploadedDocument.id == ProcessingJob.document_id)
        .where(ProcessingJob.id == job_id)
    )
    if session.get_bind().dialect.name == "postgresql":
        statement = statement.with_for_update(of=(ProcessingJob, UploadedDocument))
    row = session.execute(statement).one_or_none()
    if row is None:
        session.rollback()
        return None
    job, document = row
    failed_at = _database_now(session, now)
    if (
        job.status != JOB_STATUS_RUNNING
        or job.claim_token != claim_token
        or job.lease_expires_at is None
        or job.lease_expires_at <= failed_at
    ):
        session.rollback()
        return None

    should_retry = retryable and job.attempt_count < job.max_attempts
    message = _public_error_message(error_message)
    job.status = JOB_STATUS_QUEUED if should_retry else JOB_STATUS_FAILED
    job.available_at = (
        failed_at + timedelta(seconds=max(0.0, retry_delay_seconds))
        if should_retry
        else failed_at
    )
    job.finished_at = None if should_retry else failed_at
    job.last_error_code = error_code.strip()[:100]
    job.last_error_message = message
    job.failed_stage = None if should_retry else job.processing_stage
    job.processing_stage = None
    job.updated_at = failed_at
    _clear_lease(job)

    document.status = "uploaded" if should_retry else "failed"
    document.processing_error = None if should_retry else message
    document.updated_at = failed_at
    session.commit()
    return job.status


def recover_expired_jobs(
    session: Session,
    *,
    now: datetime | None = None,
    limit: int = 100,
) -> int:
    if limit <= 0:
        raise ValueError("limit must be positive")

    _start_transition(session)
    recovered_at = _database_now(session, now)
    statement = (
        select(ProcessingJob, UploadedDocument, Course)
        .join(UploadedDocument, UploadedDocument.id == ProcessingJob.document_id)
        .join(Course, Course.id == ProcessingJob.course_id)
        .where(
            ProcessingJob.status == JOB_STATUS_RUNNING,
            ProcessingJob.lease_expires_at <= recovered_at,
        )
        .order_by(ProcessingJob.id)
        .limit(limit)
    )
    if session.get_bind().dialect.name == "postgresql":
        statement = statement.with_for_update(of=ProcessingJob, skip_locked=True)
    rows = session.execute(statement).all()
    if not rows:
        session.rollback()
        return 0

    document_updates: list[tuple[UploadedDocument, bool, str]] = []
    for job, document, course in rows:
        should_retry = not course.is_deleted and job.attempt_count < job.max_attempts
        message = (
            "The course was deleted before document processing completed."
            if course.is_deleted
            else "The worker lease expired before completion."
        )
        job.status = JOB_STATUS_QUEUED if should_retry else JOB_STATUS_FAILED
        job.available_at = recovered_at
        job.finished_at = None if should_retry else recovered_at
        job.last_error_code = "COURSE_DELETED" if course.is_deleted else "LEASE_EXPIRED"
        job.last_error_message = message
        job.failed_stage = None if should_retry else job.processing_stage
        job.processing_stage = None
        job.updated_at = recovered_at
        _clear_lease(job)
        document_updates.append((document, should_retry, message))

    session.flush()
    for document, should_retry, message in document_updates:
        document.status = "uploaded" if should_retry else "failed"
        document.processing_error = None if should_retry else message
        document.updated_at = recovered_at

    session.commit()
    return len(rows)


def retry_failed_job(
    session: Session,
    document_id: UUID,
    course_id: int,
    *,
    now: datetime | None = None,
) -> tuple[UploadedDocument, ProcessingJob] | None:
    _start_transition(session)
    course_statement = select(Course).where(
        Course.id == course_id, Course.is_deleted.is_(False)
    )
    if session.get_bind().dialect.name == "postgresql":
        course_statement = course_statement.with_for_update(of=Course)
    if session.scalar(course_statement) is None:
        session.rollback()
        return None

    statement = (
        select(UploadedDocument, ProcessingJob)
        .join(
            ProcessingJob,
            (ProcessingJob.document_id == UploadedDocument.id)
            & (ProcessingJob.course_id == UploadedDocument.course_id),
        )
        .where(
            UploadedDocument.id == document_id,
            UploadedDocument.course_id == course_id,
            UploadedDocument.status != "deleting",
            ProcessingJob.job_type == JOB_TYPE_EXTRACT_DOCUMENT,
        )
    )
    if session.get_bind().dialect.name == "postgresql":
        statement = statement.with_for_update(of=(UploadedDocument, ProcessingJob))
    row = session.execute(statement).one_or_none()
    if row is None:
        session.rollback()
        return None
    document, job = row
    if document.status != "failed" or job.status != JOB_STATUS_FAILED:
        session.rollback()
        raise ProcessingJobStateError("Only failed document jobs can be retried")

    available_at = _database_now(session, now)
    job.status = JOB_STATUS_QUEUED
    job.attempt_count = 0
    job.available_at = available_at
    job.started_at = None
    job.finished_at = None
    job.last_error_code = None
    job.last_error_message = None
    job.processing_stage = None
    job.failed_stage = None
    job.updated_at = available_at
    _clear_lease(job)
    document.status = "uploaded"
    document.processing_error = None
    document.updated_at = available_at
    session.commit()
    return document, job


def fence_course_jobs(
    session: Session,
    course_id: int,
    *,
    now: datetime | None = None,
) -> int:
    """Fence queued/running claims inside an existing course-delete transaction."""
    fenced_at = _database_now(session, now)
    statement = select(ProcessingJob).where(
        ProcessingJob.course_id == course_id,
        ProcessingJob.status.in_((JOB_STATUS_QUEUED, JOB_STATUS_RUNNING)),
    )
    if session.get_bind().dialect.name == "postgresql":
        statement = statement.with_for_update(of=ProcessingJob)
    jobs = session.scalars(statement).all()
    if not jobs:
        return 0

    message = "The course was deleted before document processing completed."
    document_ids: list[UUID] = []
    for job in jobs:
        document_ids.append(job.document_id)
        job.status = JOB_STATUS_FAILED
        job.available_at = fenced_at
        job.finished_at = fenced_at
        job.last_error_code = "COURSE_DELETED"
        job.last_error_message = message
        job.failed_stage = job.processing_stage
        job.processing_stage = None
        job.updated_at = fenced_at
        _clear_lease(job)
    # Keep the global lock order job -> document consistent with claims/recovery.
    session.flush()
    session.execute(
        update(UploadedDocument)
        .where(
            UploadedDocument.id.in_(document_ids),
            UploadedDocument.status.in_(("uploaded", "processing")),
        )
        .values(
            status="failed",
            processing_error=message,
            updated_at=fenced_at,
        )
    )
    session.flush()
    return len(jobs)
