import hashlib
from io import BytesIO
from uuid import uuid4

import pytest
from sqlalchemy import select

from backend.app.models import (
    EMBEDDING_DIMENSIONS,
    Course,
    DocumentChunk,
    Role,
    UploadedDocument,
    User,
)
from services.course import CourseService
from services.document import DocumentService
from services.processing_jobs import enqueue_document_job
from services.vector_store import ChromaVectorStore, PgVectorStore, VectorRecord
from storage.local import LocalStorage
from workers import embedding_backfill
from workers.embedding_backfill import BackfillReport, run_backfill

pytestmark = pytest.mark.database_contract


def _vector(seed: float) -> list[float]:
    return [seed] * EMBEDDING_DIMENSIONS


class StubEmbeddingProvider:
    def __init__(self, fail_at: int | None = None) -> None:
        self.fail_at = fail_at
        self.embedded: list[str] = []

    def embed_documents(self, texts):
        vectors = []
        for text in texts:
            if self.fail_at is not None and len(self.embedded) >= self.fail_at:
                raise RuntimeError("embedding service is down")
            self.embedded.append(text)
            vectors.append(_vector(0.25))
        return vectors

    def embed_query(self, text):
        return self.embed_documents([text])[0]


@pytest.fixture(params=["pgvector", "chroma"])
def store(request, tmp_path):
    if request.param == "pgvector":
        return PgVectorStore()
    return ChromaVectorStore(persist_directory=str(tmp_path / "chroma"))


def _seed(
    session,
    storage: LocalStorage,
    *,
    email: str,
    chunk_count: int,
    course: Course | None = None,
    status: str = "ready",
):
    if course is None:
        role = session.scalar(select(Role).where(Role.name == "user"))
        assert role is not None
        user = User(
            name="Lifecycle user",
            email=email,
            password_hash="not-a-real-hash",
            role=role,
        )
        course = Course(owner=user, title="Lifecycle course")
        session.add_all((user, course))
        session.flush()

    document_id = uuid4()
    content = f"content-{document_id}".encode("utf-8")
    storage_key = storage.generate_key(course.id, document_id, "txt")
    storage.save(storage_key, BytesIO(content))
    document = UploadedDocument(
        id=document_id,
        original_file_name="notes.txt",
        file_type="txt",
        mime_type="text/plain",
        file_size=len(content),
        file_hash=hashlib.sha256(content).hexdigest(),
        uploader=course.owner,
        course=course,
        storage_provider=storage.provider,
        storage_key=storage_key,
        status=status,
    )
    chunks = [
        DocumentChunk(
            document=document,
            course=course,
            chunk_index=index,
            text=f"Chunk {index} of {document_id}",
        )
        for index in range(chunk_count)
    ]
    session.add_all([document, *chunks])
    session.flush()
    job = enqueue_document_job(session, document)
    job.status = "succeeded"
    job.finished_at = job.available_at
    session.flush()
    return course, document, chunks


def _store_vectors(store, session, document, chunks) -> None:
    store.replace_document_vectors(
        session,
        document_id=document.id,
        course_id=document.course_id,
        records=[
            VectorRecord(
                chunk_id=chunk.id,
                document_id=document.id,
                course_id=document.course_id,
                chunk_index=chunk.chunk_index,
                embedding=_vector(0.5),
            )
            for chunk in chunks
        ],
        embedding_provider="ollama",
        embedding_model="nomic-embed-text",
    )


def test_document_deletion_removes_every_vector(
    store, session_factory, db_session, tmp_path
) -> None:
    storage = LocalStorage(tmp_path / "uploads", namespace="lifecycle")
    course, document, chunks = _seed(
        db_session, storage, email="delete-doc@example.com", chunk_count=4
    )
    _store_vectors(store, db_session, document, chunks)
    db_session.commit()
    assert store.count_document_vectors(db_session, document.id) == 4

    DocumentService.delete_document(
        db_session,
        storage,
        document.id,
        course.id,
        vector_store=store,
    )

    assert store.count_document_vectors(db_session, document.id) == 0
    assert db_session.get(UploadedDocument, document.id) is None


def test_course_hard_deletion_removes_every_vector_in_the_course(
    store, db_session, tmp_path
) -> None:
    storage = LocalStorage(tmp_path / "uploads", namespace="lifecycle")
    course, first, first_chunks = _seed(
        db_session, storage, email="delete-course@example.com", chunk_count=3
    )
    _, second, second_chunks = _seed(
        db_session, storage, email="unused@example.com", chunk_count=2, course=course
    )
    _store_vectors(store, db_session, first, first_chunks)
    _store_vectors(store, db_session, second, second_chunks)
    db_session.commit()
    assert store.count_course_vectors(db_session, course.id) == 5

    CourseService.hard_delete_course(db_session, course.id, storage, vector_store=store)

    assert store.count_course_vectors(db_session, course.id) == 0
    assert db_session.get(Course, course.id) is None


def test_backfill_skips_a_purge_pending_course(
    store, session_factory, db_session, tmp_path
) -> None:
    """The tombstone filter keeps a backfill from re-embedding a course being purged.

    A purge removes a course's vectors and only then deletes its row. A backfill
    running in that window must not write them back, or deleted material would
    stay semantically searchable.
    """
    storage = LocalStorage(tmp_path / "uploads", namespace="lifecycle")
    course, document, _ = _seed(
        db_session, storage, email="purge-pending-backfill@example.com", chunk_count=3
    )
    course.is_deleted = True
    db_session.commit()

    provider = StubEmbeddingProvider()
    report = run_backfill(
        session_factory=session_factory,
        vector_store=store,
        embedding_provider=provider,
    )

    assert report.documents_examined == 0
    assert provider.embedded == []
    assert store.count_document_vectors(db_session, document.id) == 0


def test_backfill_creates_missing_vectors_once(
    store, session_factory, db_session, tmp_path
) -> None:
    storage = LocalStorage(tmp_path / "uploads", namespace="lifecycle")
    _, document, chunks = _seed(
        db_session, storage, email="backfill-once@example.com", chunk_count=5
    )
    db_session.commit()
    assert store.count_document_vectors(db_session, document.id) == 0

    provider = StubEmbeddingProvider()
    first = run_backfill(
        session_factory=session_factory,
        vector_store=store,
        embedding_provider=provider,
    )
    assert first.vectors_written == 5
    assert store.count_document_vectors(db_session, document.id) == 5

    second = run_backfill(
        session_factory=session_factory,
        vector_store=store,
        embedding_provider=provider,
    )
    third = run_backfill(
        session_factory=session_factory,
        vector_store=store,
        embedding_provider=provider,
    )

    assert second.vectors_written == 0
    assert third.vectors_written == 0
    assert store.count_document_vectors(db_session, document.id) == 5
    assert len(provider.embedded) == 5


def test_backfill_only_embeds_the_missing_chunks(
    store, session_factory, db_session, tmp_path
) -> None:
    storage = LocalStorage(tmp_path / "uploads", namespace="lifecycle")
    _, document, chunks = _seed(
        db_session, storage, email="backfill-partial@example.com", chunk_count=5
    )
    _store_vectors(store, db_session, document, chunks[:2])
    db_session.commit()
    assert store.count_document_vectors(db_session, document.id) == 2

    provider = StubEmbeddingProvider()
    report = run_backfill(
        session_factory=session_factory,
        vector_store=store,
        embedding_provider=provider,
    )

    assert report.vectors_written == 3
    assert len(provider.embedded) == 3
    assert store.count_document_vectors(db_session, document.id) == 5
    assert store.chunk_ids_with_vectors(db_session, document.id) == {
        chunk.id for chunk in chunks
    }


def test_backfill_resumes_after_a_partial_failure(
    store, session_factory, db_session, tmp_path
) -> None:
    storage = LocalStorage(tmp_path / "uploads", namespace="lifecycle")
    _, first, _ = _seed(
        db_session, storage, email="backfill-resume@example.com", chunk_count=3
    )
    course = db_session.get(Course, first.course_id)
    _, second, _ = _seed(
        db_session, storage, email="unused@example.com", chunk_count=3, course=course
    )
    db_session.commit()

    failing = StubEmbeddingProvider(fail_at=3)
    with pytest.raises(RuntimeError):
        run_backfill(
            session_factory=session_factory,
            vector_store=store,
            embedding_provider=failing,
        )

    completed = (
        first if store.count_document_vectors(db_session, first.id) == 3 else second
    )
    remaining = second if completed is first else first
    assert store.count_document_vectors(db_session, completed.id) == 3
    assert store.count_document_vectors(db_session, remaining.id) == 0

    recovered = StubEmbeddingProvider()
    report = run_backfill(
        session_factory=session_factory,
        vector_store=store,
        embedding_provider=recovered,
    )

    assert report.vectors_written == 3
    assert len(recovered.embedded) == 3
    assert store.count_document_vectors(db_session, first.id) == 3
    assert store.count_document_vectors(db_session, second.id) == 3


def test_backfill_prunes_vectors_whose_chunk_is_gone(
    store, session_factory, db_session, tmp_path
) -> None:
    storage = LocalStorage(tmp_path / "uploads", namespace="lifecycle")
    _, document, chunks = _seed(
        db_session, storage, email="backfill-prune@example.com", chunk_count=3
    )
    _store_vectors(store, db_session, document, chunks)
    db_session.commit()

    stale = chunks[-1]
    stale_id = stale.id
    db_session.delete(stale)
    db_session.commit()

    # A pgvector row cascades away with its chunk; a Chroma vector does not.
    if store.count_document_vectors(db_session, document.id) == 3:
        assert stale_id in store.chunk_ids_with_vectors(db_session, document.id)

    report = run_backfill(
        session_factory=session_factory,
        vector_store=store,
        embedding_provider=StubEmbeddingProvider(),
        prune_orphans=True,
    )

    assert store.count_document_vectors(db_session, document.id) == 2
    assert stale_id not in store.chunk_ids_with_vectors(db_session, document.id)
    assert report.vectors_written == 0


def test_backfill_dry_run_writes_nothing(
    store, session_factory, db_session, tmp_path
) -> None:
    storage = LocalStorage(tmp_path / "uploads", namespace="lifecycle")
    _, document, _ = _seed(
        db_session, storage, email="backfill-dry@example.com", chunk_count=4
    )
    db_session.commit()

    provider = StubEmbeddingProvider()
    report = run_backfill(
        session_factory=session_factory,
        vector_store=store,
        embedding_provider=provider,
        dry_run=True,
    )

    assert report.vectors_missing == 4
    assert report.vectors_written == 0
    assert provider.embedded == []
    assert store.count_document_vectors(db_session, document.id) == 0


def test_backfill_skips_documents_that_are_not_ready(
    store, session_factory, db_session, tmp_path
) -> None:
    storage = LocalStorage(tmp_path / "uploads", namespace="lifecycle")
    _, document, _ = _seed(
        db_session,
        storage,
        email="backfill-unready@example.com",
        chunk_count=3,
        status="processing",
    )
    db_session.commit()

    report = run_backfill(
        session_factory=session_factory,
        vector_store=store,
        embedding_provider=StubEmbeddingProvider(),
    )

    assert report.vectors_written == 0
    assert store.count_document_vectors(db_session, document.id) == 0


def test_backfill_can_target_one_course(
    store, session_factory, db_session, tmp_path
) -> None:
    storage = LocalStorage(tmp_path / "uploads", namespace="lifecycle")
    first_course, first, _ = _seed(
        db_session, storage, email="backfill-scope-a@example.com", chunk_count=2
    )
    _, second, _ = _seed(
        db_session, storage, email="backfill-scope-b@example.com", chunk_count=3
    )
    db_session.commit()

    report = run_backfill(
        session_factory=session_factory,
        vector_store=store,
        embedding_provider=StubEmbeddingProvider(),
        course_id=first_course.id,
    )

    assert report.vectors_written == 2
    assert store.count_document_vectors(db_session, first.id) == 2
    assert store.count_document_vectors(db_session, second.id) == 0


def test_backfill_report_counts_documents(
    store, session_factory, db_session, tmp_path
) -> None:
    storage = LocalStorage(tmp_path / "uploads", namespace="lifecycle")
    _seed(db_session, storage, email="backfill-report@example.com", chunk_count=2)
    db_session.commit()

    report = run_backfill(
        session_factory=session_factory,
        vector_store=store,
        embedding_provider=StubEmbeddingProvider(),
    )

    assert isinstance(report, BackfillReport)
    assert report.documents_examined == 1
    assert report.documents_updated == 1


def test_backfill_worker_runs_periodically_and_stops_cleanly(monkeypatch) -> None:
    runs = 0
    stop = embedding_backfill._SignalStopEvent()

    def mock_run_backfill(**kwargs):
        nonlocal runs
        runs += 1
        if runs >= 2:
            stop.requested = True
        return BackfillReport(documents_examined=1, documents_updated=1)

    monkeypatch.setattr(embedding_backfill, "check_backfill_ready", lambda **k: None)
    monkeypatch.setattr(embedding_backfill, "run_backfill", mock_run_backfill)

    embedding_backfill.run_backfill_worker(
        interval_seconds=0.01,
        stop_event=stop,
        session_factory=lambda: None,
        vector_store=object(),
        embedding_provider=object(),
    )

    assert runs == 2


def test_backfill_worker_once_mode(monkeypatch) -> None:
    runs = 0

    def mock_run_backfill(**kwargs):
        nonlocal runs
        runs += 1
        return BackfillReport()

    monkeypatch.setattr(embedding_backfill, "check_backfill_ready", lambda **k: None)
    monkeypatch.setattr(embedding_backfill, "run_backfill", mock_run_backfill)

    embedding_backfill.run_backfill_worker(
        interval_seconds=10.0,
        once=True,
        session_factory=lambda: None,
        vector_store=object(),
        embedding_provider=object(),
    )

    assert runs == 1


def test_backfill_worker_survives_iteration_failure(monkeypatch) -> None:
    runs = 0
    stop = embedding_backfill._SignalStopEvent()

    def mock_run_backfill(**kwargs):
        nonlocal runs
        runs += 1
        if runs == 1:
            raise RuntimeError("transient provider timeout")
        stop.requested = True
        return BackfillReport()

    monkeypatch.setattr(embedding_backfill, "check_backfill_ready", lambda **k: None)
    monkeypatch.setattr(embedding_backfill, "run_backfill", mock_run_backfill)

    embedding_backfill.run_backfill_worker(
        interval_seconds=0.01,
        stop_event=stop,
        session_factory=lambda: None,
        vector_store=object(),
        embedding_provider=object(),
    )

    assert runs == 2


def test_backfill_worker_readiness_failure(monkeypatch) -> None:
    def fail_readiness(**kwargs):
        raise embedding_backfill.ReadinessError("database unavailable")

    monkeypatch.setattr(embedding_backfill, "check_backfill_ready", fail_readiness)

    with pytest.raises(embedding_backfill.ReadinessError):
        embedding_backfill.run_backfill_worker(
            interval_seconds=0.01,
            session_factory=lambda: None,
            vector_store=object(),
            embedding_provider=object(),
        )


def test_backfill_check_cli(monkeypatch) -> None:
    called = False

    def mock_check():
        nonlocal called
        called = True

    monkeypatch.setattr(embedding_backfill, "check_backfill_ready", mock_check)
    embedding_backfill.main(["--check"])
    assert called is True


def test_backfill_check_cli_failure_exits_nonzero(monkeypatch) -> None:
    def fail_check():
        raise embedding_backfill.ReadinessError("storage unavailable")

    monkeypatch.setattr(embedding_backfill, "check_backfill_ready", fail_check)
    with pytest.raises(SystemExit) as exc:
        embedding_backfill.main(["--check"])
    assert exc.value.code == 1


def test_backfill_worker_cli_with_interval(monkeypatch) -> None:
    called_with = {}

    def mock_worker(**kwargs):
        called_with.update(kwargs)

    monkeypatch.setattr(embedding_backfill, "run_backfill_worker", mock_worker)
    embedding_backfill.main(["--interval-seconds", "600", "--once", "--batch-size", "32"])

    assert called_with["interval_seconds"] == 600.0
    assert called_with["once"] is True
    assert called_with["batch_size"] == 32
