"""Durable, fenced state transitions for document processing jobs."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from math import ceil, isfinite
from uuid import UUID, uuid4

from sqlalchemy import case, delete, func, select, update
from sqlalchemy.orm import Session

from backend.app.config import settings
from backend.app.database import begin_serialized_write
from backend.app.observability import get_request_id
from backend.app.models import (
    DOCUMENT_PROCESSING_STAGES,
    EMBEDDING_DIMENSIONS,
    JOB_STATUS_FAILED,
    JOB_STATUS_QUEUED,
    JOB_STATUS_RUNNING,
    JOB_STATUS_SUCCEEDED,
    JOB_TYPE_EXTRACT_DOCUMENT,
    Course,
    DocumentChunk,
    DocumentPage,
    DocumentVisual,
    ProcessingJob,
    ProfileDocument,
    ProfileDocumentChunk,
    ProfileDocumentPage,
    ProfileDocumentVisual,
    ProfileProcessingJob,
    UploadedDocument,
)
from services.embeddings import configured_embedding_identity
from services.vector_store import (
    VectorRecord,
    VectorStore,
    VectorStoreError,
    get_vector_store,
)

# The document states a lease recovery may still rewrite. Anything else -- a
# ``deleting`` tombstone above all -- belongs to whoever put the document there.
RECOVERABLE_DOCUMENT_STATUSES = frozenset({"uploaded", "processing"})


@dataclass(frozen=True, slots=True)
class ChunkData:
    text: str
    page_number: int | None = None
    end_page_number: int | None = None


@dataclass(frozen=True, slots=True)
class VisualData:
    visual_index: int
    visual_type: str
    source: str
    bbox: tuple[float, float, float, float]
    description: str | None = None
    analysis_status: str = "pending"
    error_code: str | None = None


@dataclass(frozen=True, slots=True)
class PageData:
    content_index: int
    text: str
    page_number: int | None
    extraction_method: str | None
    has_images: bool
    needs_ocr: bool
    raw_text: str | None = None
    raw_extraction_method: str | None = None
    has_visual_content: bool = False
    raw_needs_ocr: bool | None = None
    ocr_status: str | None = None
    visual_analysis_status: str = "not_applicable"
    visuals: tuple[VisualData, ...] = ()


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
    correlation_id: str | None = None


@dataclass(frozen=True, slots=True)
class ClaimedProfileJob:
    id: int
    document_id: UUID
    user_id: int
    claim_token: str
    attempt_count: int
    max_attempts: int
    storage_provider: str
    storage_key: str
    file_hash: str
    file_type: str
    file_size: int
    correlation_id: str | None = None


@dataclass(frozen=True, slots=True)
class QueueMetrics:
    queued: int
    running: int
    failed: int
    oldest_queued_age_seconds: float


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
    "generating_embeddings": ("chunking", "generating_embeddings"),
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


def _start_transition(
    session: Session,
    *,
    sqlite_busy_timeout_milliseconds: int | None = None,
) -> None:
    if session.in_transaction():
        session.rollback()
    if (
        session.get_bind().dialect.name == "sqlite"
        and sqlite_busy_timeout_milliseconds is not None
    ):
        session.connection().exec_driver_sql(
            f"PRAGMA busy_timeout={sqlite_busy_timeout_milliseconds}"
        )
    begin_serialized_write(session)


def _operation_timeout_milliseconds(
    operation_timeout_seconds: float | None,
) -> int | None:
    if operation_timeout_seconds is None:
        return None
    if (
        isinstance(operation_timeout_seconds, bool)
        or not isinstance(operation_timeout_seconds, (int, float))
        or not isfinite(operation_timeout_seconds)
        or operation_timeout_seconds <= 0
    ):
        raise ValueError("Operation timeout must be a positive finite number")
    return max(1, ceil(operation_timeout_seconds * 1000))


def _start_transition_with_operation_timeout(
    session: Session,
    operation_timeout_seconds: float | None,
) -> None:
    """Widen this transaction's lock/statement budget beyond the shared engine default.

    The shared engine caps every PostgreSQL statement at 5s
    (``backend/app/database_engine.py``), which is correct for request-path
    queries but too short for a worker finalizing a large document or purging
    a large course in one transaction. Workers pass the time remaining on
    their claim lease here so the override tracks how much budget they
    actually still have, instead of hard-coding a bigger constant.
    """
    dialect_name = session.get_bind().dialect.name
    timeout_milliseconds = _operation_timeout_milliseconds(operation_timeout_seconds)
    _start_transition(session, sqlite_busy_timeout_milliseconds=timeout_milliseconds)
    if dialect_name == "postgresql" and timeout_milliseconds is not None:
        timeout = f"{timeout_milliseconds}ms"
        session.scalar(select(func.set_config("lock_timeout", timeout, True)))
        session.scalar(select(func.set_config("statement_timeout", timeout, True)))


def _clear_lease(job: ProcessingJob) -> None:
    job.lease_owner = None
    job.claim_token = None
    job.claimed_at = None
    job.heartbeat_at = None
    job.lease_expires_at = None


def _public_error_message(message: str) -> str:
    normalized = " ".join(message.replace("\x00", "").split())
    return (normalized or "Document processing failed.")[:500]


def enqueue_document_job(
    session: Session,
    document: UploadedDocument,
    *,
    correlation_id: str | None = None,
    max_attempts: int | None = None,
    now: datetime | None = None,
) -> ProcessingJob:
    """Add an extraction job to the caller's document transaction."""
    if max_attempts is None:
        max_attempts = settings.processing_job_max_attempts
    if max_attempts <= 0:
        raise ValueError("max_attempts must be positive")

    if correlation_id is None:
        correlation_id = get_request_id()

    available_at = _database_now(session, now)
    job = ProcessingJob(
        document=document,
        course_id=document.course_id,
        job_type=JOB_TYPE_EXTRACT_DOCUMENT,
        correlation_id=correlation_id,
        status=JOB_STATUS_QUEUED,
        attempt_count=0,
        max_attempts=max_attempts,
        available_at=available_at,
    )
    session.add(job)
    session.flush()
    return job


def processing_queue_metrics(
    session: Session,
    *,
    now: datetime | None = None,
) -> QueueMetrics:
    """Return one bounded aggregate snapshot for worker metrics."""
    current = _database_now(session, now)
    row = session.execute(
        select(
            func.coalesce(
                func.sum(case((ProcessingJob.status == JOB_STATUS_QUEUED, 1), else_=0)),
                0,
            ),
            func.coalesce(
                func.sum(
                    case((ProcessingJob.status == JOB_STATUS_RUNNING, 1), else_=0)
                ),
                0,
            ),
            func.coalesce(
                func.sum(case((ProcessingJob.status == JOB_STATUS_FAILED, 1), else_=0)),
                0,
            ),
            func.min(
                case(
                    (
                        ProcessingJob.status == JOB_STATUS_QUEUED,
                        ProcessingJob.available_at,
                    ),
                    else_=None,
                )
            ),
        )
    ).one()
    p_row = session.execute(
        select(
            func.coalesce(
                func.sum(
                    case((ProfileProcessingJob.status == JOB_STATUS_QUEUED, 1), else_=0)
                ),
                0,
            ),
            func.coalesce(
                func.sum(
                    case(
                        (ProfileProcessingJob.status == JOB_STATUS_RUNNING, 1), else_=0
                    )
                ),
                0,
            ),
            func.coalesce(
                func.sum(
                    case((ProfileProcessingJob.status == JOB_STATUS_FAILED, 1), else_=0)
                ),
                0,
            ),
            func.min(
                case(
                    (
                        ProfileProcessingJob.status == JOB_STATUS_QUEUED,
                        ProfileProcessingJob.available_at,
                    ),
                    else_=None,
                )
            ),
        )
    ).one()
    oldest_list = [t for t in (row[3], p_row[3]) if t is not None]
    oldest = min(oldest_list) if oldest_list else None
    if oldest is not None and oldest.tzinfo is None:
        oldest = oldest.replace(tzinfo=timezone.utc)
    age = 0.0 if oldest is None else max(0.0, (current - oldest).total_seconds())
    return QueueMetrics(
        queued=int(row[0]) + int(p_row[0]),
        running=int(row[1]) + int(p_row[1]),
        failed=int(row[2]) + int(p_row[2]),
        oldest_queued_age_seconds=age,
    )


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
            ProcessingJob.correlation_id,
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
        correlation_id=row.correlation_id,
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


def replace_document_pages(
    session: Session,
    job_id: int,
    claim_token: str,
    pages: list[PageData],
    *,
    now: datetime | None = None,
    operation_timeout_seconds: float | None = None,
) -> bool:
    """Claim-fence an atomic replacement of canonical raw extraction units."""
    if not pages:
        raise ValueError("A raw extraction must contain at least one page")
    if [page.content_index for page in pages] != list(range(len(pages))):
        raise ValueError("Document page content indexes must be contiguous")
    for page in pages:
        _validate_page_data(page, raw=True)
    if sum(len(page.text) for page in pages) > settings.max_extracted_characters:
        raise ValueError("Document pages exceed the configured text limit")

    dialect_name = session.get_bind().dialect.name
    _start_transition_with_operation_timeout(session, operation_timeout_seconds)
    course_id = session.scalar(
        select(ProcessingJob.course_id).where(ProcessingJob.id == job_id)
    )
    if course_id is None:
        session.rollback()
        return False

    course_statement = select(Course).where(Course.id == course_id)
    if dialect_name == "postgresql":
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
    if dialect_name == "postgresql":
        statement = statement.with_for_update(of=(ProcessingJob, UploadedDocument))
    row = session.execute(statement).one_or_none()
    if row is None:
        session.rollback()
        return False

    job, document = row
    recorded_at = _database_now(session, now)
    if (
        job.status != JOB_STATUS_RUNNING
        or job.claim_token != claim_token
        or job.lease_expires_at is None
        or job.lease_expires_at <= recorded_at
        or job.processing_stage != "extracting_text"
        or document.status != "processing"
    ):
        session.rollback()
        return False

    session.execute(
        delete(DocumentPage).where(DocumentPage.document_id == job.document_id)
    )
    session.add_all(
        DocumentPage(
            document_id=job.document_id,
            course_id=job.course_id,
            content_index=page.content_index,
            page_number=page.page_number,
            raw_text=(
                page.raw_text if page.raw_text is not None else page.text
            ).replace("\x00", ""),
            text=page.text.replace("\x00", ""),
            raw_extraction_method=(
                page.raw_extraction_method
                if page.raw_extraction_method is not None
                else page.extraction_method
            ),
            extraction_method=page.extraction_method,
            has_images=page.has_images,
            needs_ocr=page.needs_ocr,
            raw_needs_ocr=(
                page.raw_needs_ocr if page.raw_needs_ocr is not None else page.needs_ocr
            ),
            ocr_status=page.ocr_status
            or ("pending" if page.needs_ocr else "not_required"),
            has_visual_content=page.has_visual_content,
            visual_analysis_status=page.visual_analysis_status,
            visuals=[_document_visual(visual) for visual in page.visuals],
        )
        for page in pages
    )
    session.flush()
    session.commit()
    return True


def complete_job(
    session: Session,
    job_id: int,
    claim_token: str,
    chunks: list[ChunkData],
    pages: list[PageData] | None = None,
    *,
    embeddings: list[list[float]],
    vector_store: VectorStore | None = None,
    now: datetime | None = None,
    operation_timeout_seconds: float | None = None,
) -> bool:
    if not chunks:
        raise ValueError("A completed document must contain at least one chunk")
    if len(embeddings) != len(chunks):
        raise ValueError("A completed document must carry one embedding per chunk")
    for vector in embeddings:
        if len(vector) != EMBEDDING_DIMENSIONS:
            raise ValueError(
                f"Document embeddings must contain {EMBEDDING_DIMENSIONS} values"
            )
    if len(chunks) > settings.max_document_chunks:
        raise ValueError("Document chunk count exceeds the configured limit")
    for chunk in chunks:
        if (
            not isinstance(chunk.text, str)
            or not chunk.text
            or not chunk.text.replace("\x00", "")
        ):
            raise ValueError("Document chunks must contain text")
        if (chunk.page_number is None) != (chunk.end_page_number is None):
            raise ValueError("Document chunk page ranges must be complete")
        if chunk.page_number is not None:
            if (
                type(chunk.page_number) is not int
                or type(chunk.end_page_number) is not int
            ):
                raise ValueError("Document chunk page ranges must contain integers")
            if chunk.page_number < 1 or chunk.end_page_number < chunk.page_number:
                raise ValueError(
                    "Document chunk page ranges must be positive and ordered"
                )
    if pages is not None:
        if not pages:
            raise ValueError("A completed document must contain at least one page")
        if [page.content_index for page in pages] != list(range(len(pages))):
            raise ValueError("Document page content indexes must be contiguous")
        for page in pages:
            _validate_page_data(page, raw=False)

    _start_transition_with_operation_timeout(session, operation_timeout_seconds)
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
    try:
        _validate_document_provenance(
            session,
            document.id,
            document.file_type,
            chunks,
            pages,
        )
    except ValueError:
        session.rollback()
        raise
    finished_at = _database_now(session, now)
    if (
        job.status != JOB_STATUS_RUNNING
        or job.claim_token != claim_token
        or job.lease_expires_at is None
        or job.lease_expires_at <= finished_at
        or job.processing_stage != "generating_embeddings"
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
    if pages is not None:
        session.execute(
            delete(DocumentPage).where(DocumentPage.document_id == job.document_id)
        )
        session.add_all(
            DocumentPage(
                document_id=job.document_id,
                course_id=job.course_id,
                content_index=page.content_index,
                page_number=page.page_number,
                raw_text=(page.raw_text or "").replace("\x00", ""),
                text=page.text.replace("\x00", ""),
                raw_extraction_method=page.raw_extraction_method,
                extraction_method=page.extraction_method,
                has_images=page.has_images,
                needs_ocr=page.needs_ocr,
                raw_needs_ocr=(
                    page.raw_needs_ocr
                    if page.raw_needs_ocr is not None
                    else page.needs_ocr
                ),
                ocr_status=page.ocr_status or "not_required",
                has_visual_content=page.has_visual_content,
                visual_analysis_status=page.visual_analysis_status,
                visuals=[_document_visual(visual) for visual in page.visuals],
            )
            for page in pages
        )
    session.add_all(
        DocumentChunk(
            document_id=job.document_id,
            course_id=job.course_id,
            chunk_index=index,
            page_number=chunk.page_number,
            end_page_number=chunk.end_page_number,
            text=chunk.text.replace("\x00", ""),
        )
        for index, chunk in enumerate(chunks)
    )
    session.flush()

    stored_chunks = list(
        session.scalars(
            select(DocumentChunk)
            .where(DocumentChunk.document_id == job.document_id)
            .order_by(DocumentChunk.chunk_index)
        ).all()
    )
    if len(stored_chunks) != len(chunks):
        session.rollback()
        raise RuntimeError("Persisted chunk count does not match the completed chunks")

    embedding_provider, embedding_model = configured_embedding_identity()
    store = vector_store if vector_store is not None else get_vector_store()
    try:
        store.replace_document_vectors(
            session,
            document_id=job.document_id,
            course_id=job.course_id,
            records=[
                VectorRecord(
                    chunk_id=stored.id,
                    document_id=job.document_id,
                    course_id=job.course_id,
                    chunk_index=stored.chunk_index,
                    embedding=embeddings[position],
                )
                for position, stored in enumerate(stored_chunks)
            ],
            embedding_provider=embedding_provider,
            embedding_model=embedding_model,
        )
    except VectorStoreError:
        session.rollback()
        raise

    document.status = "ready"
    document.processing_error = None
    document.updated_at = finished_at
    session.flush()
    session.commit()
    return True


def _validate_document_provenance(
    session: Session,
    document_id: UUID,
    file_type: str,
    chunks: list[ChunkData],
    pages: list[PageData] | None,
) -> None:
    page_numbers = (
        [page.page_number for page in pages]
        if pages is not None
        else list(
            session.scalars(
                select(DocumentPage.page_number)
                .where(DocumentPage.document_id == document_id)
                .order_by(DocumentPage.content_index)
            )
        )
    )
    if file_type != "pdf":
        if any(page_number is not None for page_number in page_numbers):
            raise ValueError("Non-PDF document pages cannot contain page numbers")
        if any(chunk.page_number is not None for chunk in chunks):
            raise ValueError("Non-PDF document chunks cannot contain page ranges")
        return

    if not page_numbers or page_numbers != list(range(1, len(page_numbers) + 1)):
        raise ValueError("PDF document page numbers must be contiguous and one-based")
    if any(chunk.page_number is None for chunk in chunks):
        raise ValueError("PDF document chunks must contain page ranges")
    page_number_set = set(page_numbers)
    if not page_numbers or any(
        chunk.page_number not in page_number_set
        or chunk.end_page_number not in page_number_set
        for chunk in chunks
    ):
        raise ValueError("PDF document chunk page ranges must reference document pages")


def _validate_page_data(page: PageData, *, raw: bool) -> None:
    if type(page.content_index) is not int or page.content_index < 0:
        raise ValueError("Document page content index must be a non-negative integer")
    if not isinstance(page.text, str):
        raise ValueError("Document page text must be a string")
    if not raw and not isinstance(page.raw_text, str):
        raise ValueError("Completed document page raw text must be a string")
    if page.raw_text is not None and not isinstance(page.raw_text, str):
        raise ValueError("Document page raw text must be a string")
    if page.page_number is not None and (
        type(page.page_number) is not int or page.page_number < 1
    ):
        raise ValueError("Document page numbers must be positive integers")
    allowed_methods = (
        {None, "native", "decoded"}
        if raw
        else {
            None,
            "native",
            "decoded",
            "ocr",
        }
    )
    if page.extraction_method not in allowed_methods:
        raise ValueError("Document page extraction method is unsupported")
    if page.raw_extraction_method not in {None, "native", "decoded"}:
        raise ValueError("Document page raw extraction method is unsupported")
    if (
        type(page.has_images) is not bool
        or type(page.needs_ocr) is not bool
        or type(page.has_visual_content) is not bool
    ):
        raise ValueError("Document page detection flags must be boolean")
    if page.raw_needs_ocr is not None and type(page.raw_needs_ocr) is not bool:
        raise ValueError("Document page raw OCR flag must be boolean")
    if page.raw_needs_ocr and (
        page.page_number is None or not (page.has_images or page.has_visual_content)
    ):
        raise ValueError("Raw OCR candidates must contain renderable physical content")
    if page.needs_ocr and (
        page.page_number is None or not (page.has_images or page.has_visual_content)
    ):
        raise ValueError("OCR candidates must contain renderable physical content")
    if page.ocr_status not in {None, "not_required", "pending", "succeeded", "no_text"}:
        raise ValueError("Document page OCR status is unsupported")
    if page.visual_analysis_status not in {
        "not_applicable",
        "pending",
        "not_configured",
        "completed",
        "partial",
        "failed",
    }:
        raise ValueError("Document page visual status is unsupported")
    if page.visuals and not page.has_visual_content:
        raise ValueError("Document page visual detection flag is inconsistent")
    for index, visual in enumerate(page.visuals):
        if visual.visual_index != index:
            raise ValueError("Document visual indexes must be contiguous")
        if visual.visual_type not in {
            "diagram",
            "table",
            "chart",
            "screenshot",
            "figure",
            "flowchart",
            "other",
        }:
            raise ValueError("Document visual type is unsupported")
        if visual.source not in {"image", "table", "drawing"}:
            raise ValueError("Document visual source is unsupported")
        if (
            not isinstance(visual.bbox, tuple)
            or len(visual.bbox) != 4
            or any(not _is_finite_coordinate(value) for value in visual.bbox)
            or visual.bbox[0] < 0
            or visual.bbox[1] < 0
            or visual.bbox[2] <= visual.bbox[0]
            or visual.bbox[3] <= visual.bbox[1]
        ):
            raise ValueError("Document visual bounding box is invalid")
        if visual.analysis_status not in {
            "pending",
            "not_configured",
            "succeeded",
            "skipped",
            "failed",
        }:
            raise ValueError("Document visual analysis status is unsupported")
        if visual.description is not None and (
            not isinstance(visual.description, str)
            or visual.analysis_status != "succeeded"
        ):
            raise ValueError("Document visual description is invalid")
        if visual.analysis_status == "failed" and (
            not visual.error_code or not visual.error_code.replace("\x00", "").strip()
        ):
            raise ValueError("Failed document visuals require an error code")


def _document_visual(visual: VisualData) -> DocumentVisual:
    return DocumentVisual(
        visual_index=visual.visual_index,
        visual_type=visual.visual_type,
        source=visual.source,
        bbox_x0=visual.bbox[0],
        bbox_y0=visual.bbox[1],
        bbox_x1=visual.bbox[2],
        bbox_y1=visual.bbox[3],
        description=(
            visual.description.replace("\x00", "")
            if visual.description is not None
            else None
        ),
        analysis_status=visual.analysis_status,
        error_code=(
            visual.error_code.replace("\x00", "").strip()[:100]
            if visual.error_code is not None
            else None
        ),
    )


def _is_finite_coordinate(value: object) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return isfinite(value)
    except OverflowError:
        return False


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
    sanitized_error_code = error_code.replace("\x00", "").strip()
    if not sanitized_error_code:
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
    # The document is fenced alongside the job, exactly as complete_job and
    # replace_document_pages fence it: a document tombstoned as ``deleting``
    # while this attempt ran must not be resurrected into ``uploaded`` or
    # ``failed``, because the tombstone is the only thing tying the two phases
    # of DocumentService.delete_document together.
    if (
        job.status != JOB_STATUS_RUNNING
        or job.claim_token != claim_token
        or job.lease_expires_at is None
        or job.lease_expires_at <= failed_at
        or document.status != "processing"
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
    job.last_error_code = sanitized_error_code[:100]
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

    profile_rows = []
    if len(rows) < limit:
        profile_statement = (
            select(ProfileProcessingJob, ProfileDocument)
            .join(
                ProfileDocument, ProfileDocument.id == ProfileProcessingJob.document_id
            )
            .where(
                ProfileProcessingJob.status == JOB_STATUS_RUNNING,
                ProfileProcessingJob.lease_expires_at <= recovered_at,
            )
            .order_by(ProfileProcessingJob.id)
            .limit(limit - len(rows))
        )
        if session.get_bind().dialect.name == "postgresql":
            profile_statement = profile_statement.with_for_update(
                of=ProfileProcessingJob, skip_locked=True
            )
        profile_rows = session.execute(profile_statement).all()

    if not rows and not profile_rows:
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

    profile_doc_updates: list[tuple[ProfileDocument, bool, str]] = []
    for p_job, p_document in profile_rows:
        p_should_retry = p_job.attempt_count < p_job.max_attempts
        p_message = "The worker lease expired before completion."
        p_job.status = JOB_STATUS_QUEUED if p_should_retry else JOB_STATUS_FAILED
        p_job.available_at = recovered_at
        p_job.finished_at = None if p_should_retry else recovered_at
        p_job.last_error_code = "LEASE_EXPIRED"
        p_job.last_error_message = p_message
        p_job.failed_stage = None if p_should_retry else p_job.processing_stage
        p_job.processing_stage = None
        p_job.updated_at = recovered_at
        _clear_profile_lease(p_job)
        profile_doc_updates.append((p_document, p_should_retry, p_message))

    session.flush()
    # Recovering the job never rewrites a document that has left the processing
    # states behind it: a ``deleting`` tombstone belongs to an in-flight or
    # stranded deletion, and the reconciler is what finishes that, not this.
    for document, should_retry, message in document_updates:
        if document.status not in RECOVERABLE_DOCUMENT_STATUSES:
            continue
        document.status = "uploaded" if should_retry else "failed"
        document.processing_error = None if should_retry else message
        document.updated_at = recovered_at

    for p_document, p_should_retry, p_message in profile_doc_updates:
        if p_document.status not in RECOVERABLE_DOCUMENT_STATUSES:
            continue
        p_document.status = "uploaded" if p_should_retry else "failed"
        p_document.processing_error = None if p_should_retry else p_message
        p_document.updated_at = recovered_at

    session.commit()
    return len(rows) + len(profile_rows)


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


def _clear_profile_lease(job: ProfileProcessingJob) -> None:
    job.lease_owner = None
    job.claim_token = None
    job.claimed_at = None
    job.heartbeat_at = None
    job.lease_expires_at = None


def _lock_profile_job_and_document(
    session: Session,
    job_id: int,
) -> tuple[ProfileProcessingJob | None, ProfileDocument | None]:
    job_statement = select(ProfileProcessingJob).where(
        ProfileProcessingJob.id == job_id
    )
    if session.get_bind().dialect.name == "postgresql":
        job_statement = job_statement.with_for_update(of=ProfileProcessingJob)
    job = session.scalar(job_statement)
    if job is None:
        return None, None

    document_statement = select(ProfileDocument).where(
        ProfileDocument.id == job.document_id
    )
    if session.get_bind().dialect.name == "postgresql":
        document_statement = document_statement.with_for_update(of=ProfileDocument)
    document = session.scalar(document_statement)
    if document is None:
        return None, None
    return job, document


def _profile_document_visual(visual: VisualData) -> ProfileDocumentVisual:
    return ProfileDocumentVisual(
        visual_index=visual.visual_index,
        visual_type=visual.visual_type,
        source=visual.source,
        bbox_x0=visual.bbox[0],
        bbox_y0=visual.bbox[1],
        bbox_x1=visual.bbox[2],
        bbox_y1=visual.bbox[3],
        description=visual.description,
        analysis_status=visual.analysis_status,
        error_code=visual.error_code,
    )


def _validate_profile_document_provenance(
    session: Session,
    document_id: UUID,
    file_type: str,
    chunks: list[ChunkData],
    pages: list[PageData] | None,
) -> None:
    page_numbers = (
        [page.page_number for page in pages]
        if pages is not None
        else list(
            session.scalars(
                select(ProfileDocumentPage.page_number)
                .where(ProfileDocumentPage.document_id == document_id)
                .order_by(ProfileDocumentPage.content_index)
            )
        )
    )
    if file_type != "pdf":
        if any(page_number is not None for page_number in page_numbers):
            raise ValueError("Non-PDF document pages cannot contain page numbers")
        if any(chunk.page_number is not None for chunk in chunks):
            raise ValueError("Non-PDF document chunks cannot contain page ranges")
        return

    if not page_numbers or page_numbers != list(range(1, len(page_numbers) + 1)):
        raise ValueError("PDF document page numbers must be contiguous and one-based")
    if any(chunk.page_number is None for chunk in chunks):
        raise ValueError("PDF document chunks must contain page ranges")
    page_number_set = set(page_numbers)
    if not page_numbers or any(
        chunk.page_number not in page_number_set
        or chunk.end_page_number not in page_number_set
        for chunk in chunks
    ):
        raise ValueError("PDF document chunk page ranges must reference document pages")


def enqueue_profile_document_job(
    session: Session,
    document: ProfileDocument,
    *,
    correlation_id: str | None = None,
    max_attempts: int | None = None,
    now: datetime | None = None,
) -> ProfileProcessingJob:
    """Add an extraction job to the caller's profile document transaction."""
    if max_attempts is None:
        max_attempts = settings.processing_job_max_attempts
    if max_attempts <= 0:
        raise ValueError("max_attempts must be positive")

    if correlation_id is None:
        correlation_id = get_request_id()

    available_at = _database_now(session, now)
    job = ProfileProcessingJob(
        document=document,
        user_id=document.user_id,
        job_type=JOB_TYPE_EXTRACT_DOCUMENT,
        correlation_id=correlation_id,
        status=JOB_STATUS_QUEUED,
        attempt_count=0,
        max_attempts=max_attempts,
        available_at=available_at,
    )
    session.add(job)
    session.flush()
    return job


def claim_next_profile_job(
    session: Session,
    worker_id: str,
    storage_provider: str,
    lease_seconds: int,
    *,
    now: datetime | None = None,
) -> ClaimedProfileJob | None:
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
        select(ProfileProcessingJob.id)
        .join(ProfileDocument, ProfileDocument.id == ProfileProcessingJob.document_id)
        .where(
            ProfileProcessingJob.job_type == JOB_TYPE_EXTRACT_DOCUMENT,
            ProfileProcessingJob.status == JOB_STATUS_QUEUED,
            ProfileProcessingJob.available_at <= eligibility_time,
            ProfileProcessingJob.attempt_count < ProfileProcessingJob.max_attempts,
            ProfileDocument.status == "uploaded",
            ProfileDocument.storage_provider == storage_provider,
        )
        .order_by(ProfileProcessingJob.available_at, ProfileProcessingJob.id)
        .limit(1)
    )
    if dialect_name == "postgresql":
        statement = statement.with_for_update(of=ProfileProcessingJob, skip_locked=True)

    job_id = session.scalar(statement)
    if job_id is None:
        session.rollback()
        return None

    detail_statement = (
        select(
            ProfileProcessingJob.id,
            ProfileProcessingJob.document_id,
            ProfileProcessingJob.user_id,
            ProfileProcessingJob.attempt_count,
            ProfileProcessingJob.max_attempts,
            ProfileDocument.storage_provider,
            ProfileDocument.storage_key,
            ProfileDocument.file_hash,
            ProfileDocument.file_type,
            ProfileDocument.file_size,
            ProfileProcessingJob.correlation_id,
        )
        .join(ProfileDocument, ProfileDocument.id == ProfileProcessingJob.document_id)
        .where(
            ProfileProcessingJob.id == job_id,
            ProfileDocument.status == "uploaded",
        )
    )
    if dialect_name == "postgresql":
        detail_statement = detail_statement.with_for_update(of=ProfileDocument)
    row = session.execute(detail_statement).one_or_none()
    if row is None:
        session.rollback()
        return None

    claimed_at = _database_now(session, now)
    lease_expires_at = claimed_at + timedelta(seconds=lease_seconds)
    claim_token = str(uuid4())
    result = session.execute(
        update(ProfileProcessingJob)
        .where(
            ProfileProcessingJob.id == job_id,
            ProfileProcessingJob.status == JOB_STATUS_QUEUED,
            ProfileProcessingJob.available_at <= claimed_at,
            ProfileProcessingJob.attempt_count < ProfileProcessingJob.max_attempts,
        )
        .values(
            status=JOB_STATUS_RUNNING,
            attempt_count=ProfileProcessingJob.attempt_count + 1,
            started_at=case(
                (ProfileProcessingJob.started_at.is_(None), claimed_at),
                else_=ProfileProcessingJob.started_at,
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

    session.execute(
        update(ProfileDocument)
        .where(
            ProfileDocument.id == row.document_id,
            ProfileDocument.status == "uploaded",
        )
        .values(status="processing", processing_error=None, updated_at=claimed_at)
    )
    session.commit()
    return ClaimedProfileJob(
        id=row.id,
        document_id=row.document_id,
        user_id=row.user_id,
        claim_token=claim_token,
        attempt_count=row.attempt_count + 1,
        max_attempts=row.max_attempts,
        storage_provider=row.storage_provider,
        storage_key=row.storage_key,
        file_hash=row.file_hash,
        file_type=row.file_type,
        file_size=row.file_size,
        correlation_id=row.correlation_id,
    )


def heartbeat_profile_job(
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
    job, document = _lock_profile_job_and_document(session, job_id)
    if job is None or document is None:
        session.rollback()
        return False

    heartbeat_at = _database_now(session, now)
    if (
        job.status != JOB_STATUS_RUNNING
        or job.claim_token != claim_token
        or job.lease_expires_at is None
        or job.lease_expires_at <= heartbeat_at
        or document.status != "processing"
    ):
        session.rollback()
        return False

    job.heartbeat_at = heartbeat_at
    job.lease_expires_at = heartbeat_at + timedelta(seconds=lease_seconds)
    job.updated_at = heartbeat_at
    session.commit()
    return True


def update_profile_job_stage(
    session: Session,
    job_id: int,
    claim_token: str,
    stage: str,
    *,
    now: datetime | None = None,
) -> bool:
    if stage not in DOCUMENT_PROCESSING_STAGES:
        raise ValueError(f"Unknown processing stage: '{stage}'")

    _start_transition(session)
    job, document = _lock_profile_job_and_document(session, job_id)
    if job is None or document is None:
        session.rollback()
        return False

    updated_at = _database_now(session, now)
    if (
        job.status != JOB_STATUS_RUNNING
        or job.claim_token != claim_token
        or job.lease_expires_at is None
        or job.lease_expires_at <= updated_at
        or document.status != "processing"
    ):
        session.rollback()
        return False

    job.processing_stage = stage
    job.updated_at = updated_at
    session.commit()
    return True


def replace_profile_document_pages(
    session: Session,
    job_id: int,
    claim_token: str,
    pages: list[PageData],
    *,
    now: datetime | None = None,
    operation_timeout_seconds: float | None = None,
) -> bool:
    _start_transition_with_operation_timeout(session, operation_timeout_seconds)
    job, document = _lock_profile_job_and_document(session, job_id)
    if job is None or document is None:
        session.rollback()
        return False

    updated_at = _database_now(session, now)
    if (
        job.status != JOB_STATUS_RUNNING
        or job.claim_token != claim_token
        or job.lease_expires_at is None
        or job.lease_expires_at <= updated_at
        or document.status != "processing"
    ):
        session.rollback()
        return False

    session.execute(
        delete(ProfileDocumentPage).where(
            ProfileDocumentPage.document_id == job.document_id
        )
    )
    session.add_all(
        ProfileDocumentPage(
            document_id=job.document_id,
            user_id=job.user_id,
            content_index=page.content_index,
            page_number=page.page_number,
            raw_text=(page.raw_text or "").replace("\x00", ""),
            text=page.text.replace("\x00", ""),
            raw_extraction_method=page.raw_extraction_method,
            extraction_method=page.extraction_method,
            has_images=page.has_images,
            needs_ocr=page.needs_ocr,
            raw_needs_ocr=(
                page.raw_needs_ocr if page.raw_needs_ocr is not None else page.needs_ocr
            ),
            ocr_status=page.ocr_status or "not_required",
            has_visual_content=page.has_visual_content,
            visual_analysis_status=page.visual_analysis_status,
            visuals=[_profile_document_visual(visual) for visual in page.visuals],
        )
        for page in pages
    )
    job.updated_at = updated_at
    session.commit()
    return True


def complete_profile_job(
    session: Session,
    job_id: int,
    claim_token: str,
    chunks: list[ChunkData],
    embeddings: list[list[float]],
    *,
    pages: list[PageData] | None = None,
    vector_store: VectorStore | None = None,
    now: datetime | None = None,
    operation_timeout_seconds: float | None = None,
) -> bool:
    if not chunks:
        raise ValueError("Job completion requires at least one chunk")
    if len(chunks) != len(embeddings):
        raise ValueError("Every chunk must have exactly one embedding")
    for position, embedding in enumerate(embeddings):
        if len(embedding) != EMBEDDING_DIMENSIONS:
            raise ValueError(
                f"Embedding at position {position} has {len(embedding)} dimensions; "
                f"expected {EMBEDDING_DIMENSIONS}"
            )
        if any(not isfinite(value) for value in embedding):
            raise ValueError(
                f"Embedding at position {position} contains a non-finite float"
            )

    _start_transition_with_operation_timeout(session, operation_timeout_seconds)
    job, document = _lock_profile_job_and_document(session, job_id)
    if job is None or document is None:
        session.rollback()
        return False

    try:
        _validate_profile_document_provenance(
            session,
            job.document_id,
            document.file_type,
            chunks,
            pages,
        )
    except ValueError:
        session.rollback()
        raise
    finished_at = _database_now(session, now)
    if (
        job.status != JOB_STATUS_RUNNING
        or job.claim_token != claim_token
        or job.lease_expires_at is None
        or job.lease_expires_at <= finished_at
        or job.processing_stage != "generating_embeddings"
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
    _clear_profile_lease(job)

    session.execute(
        delete(ProfileDocumentChunk).where(
            ProfileDocumentChunk.document_id == job.document_id
        )
    )
    if pages is not None:
        session.execute(
            delete(ProfileDocumentPage).where(
                ProfileDocumentPage.document_id == job.document_id
            )
        )
        session.add_all(
            ProfileDocumentPage(
                document_id=job.document_id,
                user_id=job.user_id,
                content_index=page.content_index,
                page_number=page.page_number,
                raw_text=(page.raw_text or "").replace("\x00", ""),
                text=page.text.replace("\x00", ""),
                raw_extraction_method=page.raw_extraction_method,
                extraction_method=page.extraction_method,
                has_images=page.has_images,
                needs_ocr=page.needs_ocr,
                raw_needs_ocr=(
                    page.raw_needs_ocr
                    if page.raw_needs_ocr is not None
                    else page.needs_ocr
                ),
                ocr_status=page.ocr_status or "not_required",
                has_visual_content=page.has_visual_content,
                visual_analysis_status=page.visual_analysis_status,
                visuals=[_profile_document_visual(visual) for visual in page.visuals],
            )
            for page in pages
        )
    session.add_all(
        ProfileDocumentChunk(
            document_id=job.document_id,
            user_id=job.user_id,
            chunk_index=index,
            page_number=chunk.page_number,
            end_page_number=chunk.end_page_number,
            text=chunk.text.replace("\x00", ""),
        )
        for index, chunk in enumerate(chunks)
    )
    session.flush()

    stored_chunks = list(
        session.scalars(
            select(ProfileDocumentChunk)
            .where(ProfileDocumentChunk.document_id == job.document_id)
            .order_by(ProfileDocumentChunk.chunk_index)
        ).all()
    )
    if len(stored_chunks) != len(chunks):
        session.rollback()
        raise RuntimeError("Persisted chunk count does not match the completed chunks")

    embedding_provider, embedding_model = configured_embedding_identity()
    store = vector_store if vector_store is not None else get_vector_store()
    try:
        store.replace_profile_document_vectors(
            session,
            document_id=job.document_id,
            user_id=job.user_id,
            records=[
                VectorRecord(
                    chunk_id=stored.id,
                    document_id=job.document_id,
                    course_id=job.user_id,
                    chunk_index=stored.chunk_index,
                    embedding=embeddings[position],
                )
                for position, stored in enumerate(stored_chunks)
            ],
            embedding_provider=embedding_provider,
            embedding_model=embedding_model,
        )
    except VectorStoreError:
        session.rollback()
        raise

    document.status = "ready"
    document.processing_error = None
    document.updated_at = finished_at
    session.flush()
    session.commit()
    return True


def fail_profile_job(
    session: Session,
    job_id: int,
    claim_token: str,
    error_code: str,
    error_message: str | None,
    *,
    failed_stage: str | None = None,
    retryable: bool = False,
    retry_delay_seconds: int = 60,
    now: datetime | None = None,
) -> bool:
    error_code = error_code.strip()
    if not error_code:
        raise ValueError("error_code must not be empty")
    if retry_delay_seconds < 0:
        raise ValueError("retry_delay_seconds must be non-negative")
    if failed_stage is not None and failed_stage not in DOCUMENT_PROCESSING_STAGES:
        raise ValueError(f"Unknown failed stage: '{failed_stage}'")

    _start_transition(session)
    job, document = _lock_profile_job_and_document(session, job_id)
    if job is None or document is None:
        session.rollback()
        return False

    failed_at = _database_now(session, now)
    if (
        job.status != JOB_STATUS_RUNNING
        or job.claim_token != claim_token
        or job.lease_expires_at is None
        or job.lease_expires_at <= failed_at
        or document.status != "processing"
    ):
        session.rollback()
        return False

    public_message = _public_error_message(error_message or error_code)
    stage = failed_stage or job.processing_stage
    can_retry = retryable and job.attempt_count < job.max_attempts
    job.last_error_code = error_code[:100]
    job.last_error_message = public_message
    job.failed_stage = None if can_retry else stage
    job.processing_stage = None
    job.updated_at = failed_at
    _clear_profile_lease(job)

    if can_retry:
        job.status = JOB_STATUS_QUEUED
        job.available_at = failed_at + timedelta(seconds=retry_delay_seconds)
        job.finished_at = None
        document.status = "uploaded"
        document.processing_error = None
        document.updated_at = failed_at
    else:
        job.status = JOB_STATUS_FAILED
        job.available_at = failed_at
        job.finished_at = failed_at
        document.status = "failed"
        document.processing_error = public_message
        document.updated_at = failed_at

    session.commit()
    return True


def retry_failed_profile_job(
    session: Session,
    document_id: UUID,
    user_id: int,
    *,
    now: datetime | None = None,
) -> tuple[ProfileDocument, ProfileProcessingJob]:
    _start_transition(session)
    job_statement = select(ProfileProcessingJob).where(
        ProfileProcessingJob.document_id == document_id,
        ProfileProcessingJob.user_id == user_id,
        ProfileProcessingJob.job_type == JOB_TYPE_EXTRACT_DOCUMENT,
    )
    if session.get_bind().dialect.name == "postgresql":
        job_statement = job_statement.with_for_update(of=ProfileProcessingJob)
    job = session.scalar(job_statement)
    if job is None:
        session.rollback()
        raise ProcessingJobStateError(
            "No processing job exists for this profile document"
        )

    document_statement = select(ProfileDocument).where(
        ProfileDocument.id == document_id,
        ProfileDocument.user_id == user_id,
    )
    if session.get_bind().dialect.name == "postgresql":
        document_statement = document_statement.with_for_update(of=ProfileDocument)
    document = session.scalar(document_statement)
    if document is None:
        session.rollback()
        raise ProcessingJobStateError("No matching profile document exists")

    available_at = _database_now(session, now)
    if document.status != "failed" or job.status != JOB_STATUS_FAILED:
        session.rollback()
        raise ProcessingJobStateError(
            "Only failed profile documents with terminal jobs can be manually retried"
        )

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
    _clear_profile_lease(job)
    document.status = "uploaded"
    document.processing_error = None
    document.updated_at = available_at
    session.commit()
    return document, job
