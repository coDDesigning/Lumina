import hashlib
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from backend.app.models import (
    EMBEDDING_DIMENSIONS,
    JOB_STATUS_FAILED,
    JOB_STATUS_QUEUED,
    JOB_STATUS_SUCCEEDED,
    Course,
    DocumentChunk,
    ProcessingJob,
    Role,
    UploadedDocument,
    User,
)
from services.document_embedding import (
    EMBEDDING_STAGE,
    embed_document_chunks,
)
from services.embeddings import (
    EmbeddingConnectionError,
    EmbeddingDimensionMismatchError,
    EmbeddingInvalidResponseError,
    EmbeddingRateLimitError,
    EmbeddingTimeoutError,
)
from services.document_extraction import DocumentProcessingError
from services.processing_jobs import (
    ChunkData,
    claim_next_job,
    complete_job,
    enqueue_document_job,
    update_job_stage,
)
from services.vector_store import PgVectorStore, VectorRecord, VectorStoreError
from storage.local import LocalStorage
from workers.document_processor import process_next_job

from services.embeddings import configured_embedding_identity

EMBEDDING_PROVIDER_NAME, EMBEDDING_MODEL_NAME = configured_embedding_identity()

pytestmark = pytest.mark.database_contract


@dataclass(frozen=True, slots=True)
class QueuedDocument:
    document_id: UUID
    job_id: int
    course_id: int
    storage: LocalStorage


def _vector(seed: float) -> list[float]:
    return [seed] * EMBEDDING_DIMENSIONS


class StubEmbeddingProvider:
    """Returns a distinct vector per call, or raises a scripted failure."""

    def __init__(self, error: Exception | None = None, fail_after: int = 0) -> None:
        self.error = error
        self.fail_after = fail_after
        self.calls: list[list[str]] = []

    def embed_documents(self, texts):
        self.calls.append(list(texts))
        if self.error is not None and len(self.calls) > self.fail_after:
            raise self.error
        return [_vector(float(index)) for index in range(len(texts))]

    def embed_query(self, text):
        return self.embed_documents([text])[0]


class WrongWidthProvider:
    def embed_documents(self, texts):
        return [[0.1] * 16 for _ in texts]

    def embed_query(self, text):
        return [0.1] * 16


def _queue_document(
    session_factory,
    tmp_path: Path,
    *,
    content: bytes = b"Durable processing notes for embeddings",
    email: str = "embedding-worker@example.com",
) -> QueuedDocument:
    storage = LocalStorage(tmp_path / "worker-uploads", namespace="worker")
    document_id = uuid4()

    with session_factory() as session:
        role = session.scalar(select(Role).where(Role.name == "user"))
        assert role is not None
        user = User(
            name="Embedding owner",
            email=email,
            password_hash="not-a-real-hash",
            role=role,
        )
        course = Course(owner=user, title="Embedding course", semester="Fall")
        session.add(course)
        session.flush()

        storage_key = storage.generate_key(course.id, document_id, "txt")
        storage.save(storage_key, BytesIO(content))
        document = UploadedDocument(
            id=document_id,
            original_file_name="notes.txt",
            file_type="txt",
            mime_type="text/plain",
            file_size=len(content),
            file_hash=hashlib.sha256(content).hexdigest(),
            uploader=user,
            course=course,
            storage_provider=storage.provider,
            storage_key=storage_key,
            status="uploaded",
        )
        session.add(document)
        session.flush()
        job = enqueue_document_job(session, document)
        session.commit()
        return QueuedDocument(
            document_id=document.id,
            job_id=job.id,
            course_id=course.id,
            storage=storage,
        )


def _run(session_factory, queued, provider, store) -> bool:
    return process_next_job(
        session_factory=session_factory,
        storage=queued.storage,
        worker_id="embedding-worker",
        embedding_provider=provider,
        vector_store=store,
    )


def _assert_succeeded(session_factory, queued) -> None:
    """Assert the job actually finished, naming the recorded cause when it did not.

    ``process_next_job`` answers True for a job whose failure it recorded as
    well as for one it completed, so a success path that only asserts its
    return value lets a transient failure resurface further down as an
    unexplained chunk or vector count.
    """
    with session_factory() as session:
        job = session.get(ProcessingJob, queued.job_id)
        document = session.get(UploadedDocument, queued.document_id)
        assert job is not None
        assert document is not None
        assert job.status == JOB_STATUS_SUCCEEDED, (
            f"job is {job.status} after stage {job.failed_stage}: "
            f"{job.last_error_code} {job.last_error_message}"
        )
        assert document.status == "ready"


def test_stage_is_registered_before_ready() -> None:
    from backend.app.models import DOCUMENT_PROCESSING_STAGES

    assert DOCUMENT_PROCESSING_STAGES[-1] == EMBEDDING_STAGE
    assert DOCUMENT_PROCESSING_STAGES.index(
        "chunking"
    ) < DOCUMENT_PROCESSING_STAGES.index(EMBEDDING_STAGE)


def test_processing_stores_one_vector_per_chunk_with_full_metadata(
    session_factory, tmp_path
) -> None:
    queued = _queue_document(session_factory, tmp_path)
    store = PgVectorStore()

    assert _run(session_factory, queued, StubEmbeddingProvider(), store)

    with session_factory() as session:
        document = session.get(UploadedDocument, queued.document_id)
        assert document is not None
        assert document.status == "ready"

        job = session.get(ProcessingJob, queued.job_id)
        assert job is not None
        assert job.status == JOB_STATUS_SUCCEEDED
        assert job.processing_stage is None
        assert job.failed_stage is None

        chunks = list(
            session.scalars(
                select(DocumentChunk)
                .where(DocumentChunk.document_id == queued.document_id)
                .order_by(DocumentChunk.chunk_index)
            )
        )
        assert chunks

        assert store.count_document_vectors(session, queued.document_id) == len(chunks)
        assert store.chunk_ids_with_vectors(
            session, queued.document_id, embedding_model=EMBEDDING_MODEL_NAME
        ) == {chunk.id for chunk in chunks}

        for chunk in chunks:
            record = chunk.embedding_record
            assert record is not None
            assert record.document_id == queued.document_id
            assert record.course_id == queued.course_id
            assert record.chunk_index == chunk.chunk_index
            assert record.dimensions == EMBEDDING_DIMENSIONS
            assert record.embedding_provider
            assert record.embedding_model


def test_document_passes_through_the_embedding_stage(session_factory, tmp_path) -> None:
    queued = _queue_document(session_factory, tmp_path)
    seen: list[str] = []

    class RecordingProvider(StubEmbeddingProvider):
        def embed_documents(self, texts):
            with session_factory() as session:
                job = session.get(ProcessingJob, queued.job_id)
                seen.append(job.processing_stage)
            return super().embed_documents(texts)

    assert _run(session_factory, queued, RecordingProvider(), PgVectorStore())

    _assert_succeeded(session_factory, queued)
    assert seen == [EMBEDDING_STAGE]


@pytest.mark.parametrize(
    ("error", "expected_code"),
    [
        (EmbeddingTimeoutError(), "EMBEDDING_TIMEOUT"),
        (EmbeddingConnectionError(), "EMBEDDING_PROVIDER_UNAVAILABLE"),
        (EmbeddingRateLimitError(), "EMBEDDING_RATE_LIMITED"),
    ],
)
def test_retryable_embedding_failure_requeues_without_marking_ready(
    session_factory, tmp_path, error, expected_code
) -> None:
    queued = _queue_document(session_factory, tmp_path)
    store = PgVectorStore()

    assert _run(session_factory, queued, StubEmbeddingProvider(error=error), store)

    with session_factory() as session:
        document = session.get(UploadedDocument, queued.document_id)
        assert document.status != "ready"

        job = session.get(ProcessingJob, queued.job_id)
        assert job.status == JOB_STATUS_QUEUED
        assert job.last_error_code == expected_code
        assert job.attempt_count == 1

        assert store.count_document_vectors(session, queued.document_id) == 0
        assert (
            session.scalars(
                select(DocumentChunk).where(
                    DocumentChunk.document_id == queued.document_id
                )
            ).first()
            is None
        )


@pytest.mark.parametrize(
    ("provider", "expected_code"),
    [
        (WrongWidthProvider(), "EMBEDDING_DIMENSION_MISMATCH"),
        (
            StubEmbeddingProvider(error=EmbeddingInvalidResponseError()),
            "EMBEDDING_INVALID_RESPONSE",
        ),
    ],
)
def test_permanent_embedding_failure_is_not_retried(
    session_factory, tmp_path, provider, expected_code
) -> None:
    queued = _queue_document(session_factory, tmp_path)

    assert _run(session_factory, queued, provider, PgVectorStore())

    with session_factory() as session:
        document = session.get(UploadedDocument, queued.document_id)
        assert document.status == "failed"

        job = session.get(ProcessingJob, queued.job_id)
        assert job.status == JOB_STATUS_FAILED
        assert job.failed_stage == EMBEDDING_STAGE
        assert job.last_error_code == expected_code


def test_retry_after_a_transient_failure_leaves_exactly_one_vector_per_chunk(
    session_factory, tmp_path
) -> None:
    queued = _queue_document(session_factory, tmp_path)
    store = PgVectorStore()

    assert _run(
        session_factory,
        queued,
        StubEmbeddingProvider(error=EmbeddingTimeoutError()),
        store,
    )
    with session_factory() as session:
        job = session.get(ProcessingJob, queued.job_id)
        assert job.status == JOB_STATUS_QUEUED
        job.available_at = job.created_at
        session.commit()

    assert _run(session_factory, queued, StubEmbeddingProvider(), store)

    with session_factory() as session:
        document = session.get(UploadedDocument, queued.document_id)
        assert document.status == "ready"

        chunks = list(
            session.scalars(
                select(DocumentChunk).where(
                    DocumentChunk.document_id == queued.document_id
                )
            )
        )
        assert store.count_document_vectors(session, queued.document_id) == len(chunks)


def test_vector_store_failure_fails_the_job_without_marking_ready(
    session_factory, tmp_path
) -> None:
    queued = _queue_document(session_factory, tmp_path)

    class BrokenStore(PgVectorStore):
        def replace_document_vectors(self, *args, **kwargs):
            raise VectorStoreError("store is down")

    assert _run(session_factory, queued, StubEmbeddingProvider(), BrokenStore())

    with session_factory() as session:
        document = session.get(UploadedDocument, queued.document_id)
        assert document.status != "ready"
        job = session.get(ProcessingJob, queued.job_id)
        assert job.last_error_code == "VECTOR_PERSISTENCE_FAILED"
        assert job.status == JOB_STATUS_QUEUED


def test_completion_requires_the_embedding_stage(session_factory, tmp_path) -> None:
    """A job still sitting in chunking has not embedded anything yet."""
    queued = _queue_document(session_factory, tmp_path)
    storage = queued.storage

    with session_factory() as session:
        claim = claim_next_job(session, "stage-guard", storage.provider, 60)
    assert claim is not None
    for stage in ("extracting_text", "cleaning_text", "chunking"):
        with session_factory() as session:
            assert update_job_stage(session, claim.id, claim.claim_token, stage)

    with session_factory() as session:
        assert not complete_job(
            session,
            claim.id,
            claim.claim_token,
            [ChunkData("Some text")],
            embeddings=[_vector(0.1)],
            vector_store=PgVectorStore(),
        )


def test_completion_rejects_a_vector_count_that_misses_a_chunk(
    session_factory, tmp_path
) -> None:
    queued = _queue_document(session_factory, tmp_path)
    storage = queued.storage

    with session_factory() as session:
        claim = claim_next_job(session, "count-guard", storage.provider, 60)
    assert claim is not None
    for stage in ("extracting_text", "cleaning_text", "chunking", EMBEDDING_STAGE):
        with session_factory() as session:
            assert update_job_stage(session, claim.id, claim.claim_token, stage)

    with session_factory() as session:
        with pytest.raises(ValueError, match="one embedding"):
            complete_job(
                session,
                claim.id,
                claim.claim_token,
                [ChunkData("First"), ChunkData("Second")],
                embeddings=[_vector(0.1)],
                vector_store=PgVectorStore(),
            )


def test_reprocessing_replaces_stale_vectors(session_factory, tmp_path) -> None:
    queued = _queue_document(
        session_factory,
        tmp_path,
        content=b"First revision text that produces content",
    )
    store = PgVectorStore()
    assert _run(session_factory, queued, StubEmbeddingProvider(), store)
    _assert_succeeded(session_factory, queued)

    with session_factory() as session:
        original_chunk_ids = store.chunk_ids_with_vectors(
            session, queued.document_id, embedding_model=EMBEDDING_MODEL_NAME
        )
        assert original_chunk_ids

        # A vector left behind by an older chunk set must not survive reprocessing.
        stale_chunk = DocumentChunk(
            document_id=queued.document_id,
            course_id=queued.course_id,
            chunk_index=900,
            text="Stale chunk from an earlier revision",
        )
        session.add(stale_chunk)
        session.flush()
        store.replace_document_vectors(
            session,
            document_id=queued.document_id,
            course_id=queued.course_id,
            records=[
                VectorRecord(
                    chunk_id=chunk_id,
                    document_id=queued.document_id,
                    course_id=queued.course_id,
                    chunk_index=index,
                    embedding=_vector(0.5),
                )
                for index, chunk_id in enumerate(
                    sorted(original_chunk_ids) + [stale_chunk.id]
                )
            ],
            embedding_provider="ollama",
            embedding_model="nomic-embed-text",
        )
        stale_vector_count = store.count_document_vectors(session, queued.document_id)
        assert stale_vector_count == len(original_chunk_ids) + 1

        job = session.get(ProcessingJob, queued.job_id)
        job.status = JOB_STATUS_QUEUED
        job.attempt_count = 0
        job.finished_at = None
        job.processing_stage = None
        job.last_error_code = None
        job.last_error_message = None
        document = session.get(UploadedDocument, queued.document_id)
        document.status = "uploaded"
        session.commit()

    assert _run(session_factory, queued, StubEmbeddingProvider(), store)
    _assert_succeeded(session_factory, queued)

    with session_factory() as session:
        chunks = list(
            session.scalars(
                select(DocumentChunk).where(
                    DocumentChunk.document_id == queued.document_id
                )
            )
        )
        current_ids = {chunk.id for chunk in chunks}
        stored_ids = store.chunk_ids_with_vectors(
            session, queued.document_id, embedding_model=EMBEDDING_MODEL_NAME
        )

        assert stored_ids == current_ids
        assert store.count_document_vectors(session, queued.document_id) == len(chunks)
        assert store.count_document_vectors(session, queued.document_id) < (
            stale_vector_count
        )


def test_embed_document_chunks_translates_provider_failures() -> None:
    provider = StubEmbeddingProvider(error=EmbeddingTimeoutError())

    with pytest.raises(DocumentProcessingError) as excinfo:
        embed_document_chunks(["a"], provider=provider)

    assert excinfo.value.code == "EMBEDDING_TIMEOUT"
    assert excinfo.value.retryable is True
    assert excinfo.value.failed_stage == EMBEDDING_STAGE


def test_embed_document_chunks_marks_dimension_mismatch_permanent() -> None:
    provider = StubEmbeddingProvider(error=EmbeddingDimensionMismatchError())

    with pytest.raises(DocumentProcessingError) as excinfo:
        embed_document_chunks(["a"], provider=provider)

    assert excinfo.value.code == "EMBEDDING_DIMENSION_MISMATCH"
    assert excinfo.value.retryable is False


def test_embed_document_chunks_validates_the_returned_count() -> None:
    class ShortProvider:
        def embed_documents(self, texts):
            return [_vector(0.1)]

        def embed_query(self, text):
            return _vector(0.1)

    with pytest.raises(DocumentProcessingError) as excinfo:
        embed_document_chunks(["a", "b"], provider=ShortProvider())

    assert excinfo.value.code == "EMBEDDING_INVALID_RESPONSE"
    assert excinfo.value.retryable is False


def test_embed_document_chunks_rejects_empty_or_whitespace_chunks() -> None:
    provider = StubEmbeddingProvider()

    for invalid_chunk in ["", "   ", "\n\t\r "]:
        with pytest.raises(DocumentProcessingError) as excinfo:
            embed_document_chunks([invalid_chunk], provider=provider)

        assert excinfo.value.code == "EMBEDDING_INVALID_RESPONSE"
        assert excinfo.value.retryable is False
        assert excinfo.value.failed_stage == EMBEDDING_STAGE

    # Ensure provider was never called with empty strings
    assert provider.calls == []
