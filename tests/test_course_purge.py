"""The retention purge finishes course deletions that nobody else did.

A tombstone is the whole selection predicate, so these cases care about two
things: that the worker touches nothing else, and that running it twice is the
same as running it once.
"""

import hashlib
from io import BytesIO
from uuid import uuid4

import pytest
from sqlalchemy import select

from backend.app.models import (
    EMBEDDING_DIMENSIONS,
    Course,
    DocumentChunk,
    ProfileKnowledge,
    Role,
    UploadedDocument,
    User,
)
from services.course import CourseDeletionError, CourseService
from services.processing_jobs import enqueue_document_job
from services.vector_store import (
    ChromaVectorStore,
    PgVectorStore,
    VectorRecord,
)
from storage.base import StorageError
from storage.local import LocalStorage
from workers import course_purge
from workers.course_purge import PurgeReport, run_purge

pytestmark = pytest.mark.database_contract


@pytest.fixture(params=["pgvector", "chroma"])
def store(request, tmp_path):
    if request.param == "pgvector":
        return PgVectorStore()
    return ChromaVectorStore(persist_directory=str(tmp_path / "chroma"))


@pytest.fixture
def storage(tmp_path):
    return LocalStorage(tmp_path / "uploads", namespace="purge")


def _seed_course(session, storage, store, *, email: str, tombstoned: bool) -> int:
    role = session.scalar(select(Role).where(Role.name == "user"))
    assert role is not None
    user = User(
        name="Purge user",
        email=email,
        password_hash="not-a-real-hash",
        role=role,
    )
    course = Course(owner=user, title=f"Course for {email}", is_deleted=tombstoned)
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
        uploader=user,
        course=course,
        storage_provider=storage.provider,
        storage_key=storage_key,
        status="ready",
    )
    chunk = DocumentChunk(
        document=document,
        course=course,
        chunk_index=0,
        text="Purge chunk",
    )
    knowledge = ProfileKnowledge(
        user=user,
        topic="Retention",
        detail="Survives the purge.",
    )
    session.add_all((document, chunk, knowledge))
    session.flush()
    job = enqueue_document_job(session, document)
    job.status = "succeeded"
    job.finished_at = job.available_at
    session.flush()

    store.replace_document_vectors(
        session,
        document_id=document.id,
        course_id=course.id,
        records=[
            VectorRecord(
                chunk_id=chunk.id,
                document_id=document.id,
                course_id=course.id,
                chunk_index=0,
                embedding=[0.25] * EMBEDDING_DIMENSIONS,
            )
        ],
        embedding_provider="ollama",
        embedding_model="nomic-embed-text",
    )
    session.commit()
    return course.id


def _stored_files(storage) -> list[str]:
    root = storage.root
    if not root.exists():
        return []
    return sorted(
        str(path.relative_to(root)) for path in root.rglob("*") if path.is_file()
    )


def _purge(session_factory, storage, store, **kwargs) -> PurgeReport:
    return run_purge(
        session_factory=session_factory,
        storage=storage,
        vector_store=store,
        **kwargs,
    )


def test_purge_erases_every_tombstoned_course(session_factory, storage, store) -> None:
    with session_factory() as session:
        first = _seed_course(
            session, storage, store, email="purge-one@example.com", tombstoned=True
        )
        second = _seed_course(
            session, storage, store, email="purge-two@example.com", tombstoned=True
        )
        active = _seed_course(
            session, storage, store, email="purge-active@example.com", tombstoned=False
        )

    report = _purge(session_factory, storage, store)

    assert report.courses_examined == 2
    assert report.courses_purged == 2
    assert report.courses_failed == 0
    with session_factory() as session:
        assert session.get(Course, first) is None
        assert session.get(Course, second) is None
        assert session.get(Course, active) is not None
        assert store.count_course_vectors(session, first) == 0
        assert store.count_course_vectors(session, second) == 0
        assert store.count_course_vectors(session, active) == 1
        assert session.scalar(select(ProfileKnowledge.id).limit(1)) is not None
    assert len(_stored_files(storage)) == 1


def test_purge_is_idempotent(session_factory, storage, store) -> None:
    with session_factory() as session:
        course_id = _seed_course(
            session, storage, store, email="purge-twice@example.com", tombstoned=True
        )

    first = _purge(session_factory, storage, store)
    second = _purge(session_factory, storage, store)

    assert first.courses_purged == 1
    assert second == PurgeReport()
    assert _stored_files(storage) == []
    with session_factory() as session:
        assert session.get(Course, course_id) is None


def test_purge_resumes_a_deletion_that_failed_on_storage(
    session_factory, storage, store, monkeypatch
) -> None:
    """A 500 from the API leaves exactly the state this worker is built to finish."""
    with session_factory() as session:
        course_id = _seed_course(
            session, storage, store, email="purge-resume@example.com", tombstoned=False
        )

    def fail_delete(_key: str) -> None:
        raise StorageError("simulated cleanup failure")

    monkeypatch.setattr(storage, "delete", fail_delete)
    with session_factory() as session:
        with pytest.raises(CourseDeletionError):
            CourseService.hard_delete_course(
                session, course_id, storage, vector_store=store
            )
    monkeypatch.undo()

    with session_factory() as session:
        course = session.get(Course, course_id)
        assert course is not None
        assert course.is_deleted is True
    assert len(_stored_files(storage)) == 1

    report = _purge(session_factory, storage, store)

    assert report.courses_purged == 1
    assert _stored_files(storage) == []
    with session_factory() as session:
        assert session.get(Course, course_id) is None
        assert store.count_course_vectors(session, course_id) == 0


def test_purge_continues_past_a_course_it_cannot_finish(
    session_factory, storage, store, monkeypatch
) -> None:
    with session_factory() as session:
        stuck = _seed_course(
            session, storage, store, email="purge-stuck@example.com", tombstoned=True
        )
        healthy = _seed_course(
            session, storage, store, email="purge-healthy@example.com", tombstoned=True
        )

    with session_factory() as session:
        stuck_key = session.scalar(
            select(UploadedDocument.storage_key).where(
                UploadedDocument.course_id == stuck
            )
        )
    original_delete = storage.delete

    def delete_unless_stuck(key: str) -> None:
        if key == stuck_key:
            raise StorageError("simulated cleanup failure")
        original_delete(key)

    monkeypatch.setattr(storage, "delete", delete_unless_stuck)
    report = _purge(session_factory, storage, store)

    assert report.courses_examined == 2
    assert report.courses_purged == 1
    assert report.courses_failed == 1
    with session_factory() as session:
        blocked = session.get(Course, stuck)
        assert blocked is not None
        assert blocked.is_deleted is True
        assert session.get(Course, healthy) is None

    monkeypatch.undo()
    retried = _purge(session_factory, storage, store)

    assert retried.courses_purged == 1
    assert retried.courses_failed == 0
    assert _stored_files(storage) == []
    with session_factory() as session:
        assert session.get(Course, stuck) is None


def test_purge_dry_run_changes_nothing(session_factory, storage, store) -> None:
    with session_factory() as session:
        course_id = _seed_course(
            session, storage, store, email="purge-dry@example.com", tombstoned=True
        )

    report = _purge(session_factory, storage, store, dry_run=True)

    assert report.courses_examined == 1
    assert report.courses_purged == 0
    assert len(_stored_files(storage)) == 1
    with session_factory() as session:
        course = session.get(Course, course_id)
        assert course is not None
        assert course.is_deleted is True
        assert store.count_course_vectors(session, course_id) == 1


def test_purge_can_target_one_course(session_factory, storage, store) -> None:
    with session_factory() as session:
        targeted = _seed_course(
            session, storage, store, email="purge-target@example.com", tombstoned=True
        )
        untouched = _seed_course(
            session, storage, store, email="purge-other@example.com", tombstoned=True
        )

    report = _purge(session_factory, storage, store, course_id=targeted)

    assert report.courses_examined == 1
    assert report.courses_purged == 1
    with session_factory() as session:
        assert session.get(Course, targeted) is None
        assert session.get(Course, untouched) is not None


def test_purge_command_reports_a_failed_course_through_its_exit_code(
    monkeypatch, capsys
) -> None:
    monkeypatch.setattr(
        course_purge,
        "run_purge",
        lambda **kwargs: PurgeReport(courses_examined=1, courses_failed=1),
    )

    with pytest.raises(SystemExit) as failure:
        course_purge.main([])

    assert failure.value.code == 1
    assert "failed=1" in capsys.readouterr().out


def test_purge_command_passes_its_arguments_through(monkeypatch, capsys) -> None:
    received: dict = {}

    def record(**kwargs) -> PurgeReport:
        received.update(kwargs)
        return PurgeReport()

    monkeypatch.setattr(course_purge, "run_purge", record)
    course_purge.main(["--course-id", "7", "--dry-run"])

    assert received == {"course_id": 7, "dry_run": True}
    assert "examined=0 purged=0 failed=0" in capsys.readouterr().out


def test_purge_worker_runs_periodically_and_stops_cleanly(monkeypatch) -> None:
    runs = 0
    stop = course_purge._SignalStopEvent()

    def mock_run_purge(**kwargs):
        nonlocal runs
        runs += 1
        if runs >= 2:
            stop.requested = True
        return PurgeReport(courses_examined=1, courses_purged=1)

    monkeypatch.setattr(course_purge, "check_purge_ready", lambda **k: None)
    monkeypatch.setattr(course_purge, "run_purge", mock_run_purge)

    course_purge.run_purge_worker(
        interval_seconds=0.01,
        stop_event=stop,
        session_factory=lambda: None,
        storage=object(),
        vector_store=object(),
    )

    assert runs == 2


def test_purge_worker_once_mode(monkeypatch) -> None:
    runs = 0

    def mock_run_purge(**kwargs):
        nonlocal runs
        runs += 1
        return PurgeReport()

    monkeypatch.setattr(course_purge, "check_purge_ready", lambda **k: None)
    monkeypatch.setattr(course_purge, "run_purge", mock_run_purge)

    course_purge.run_purge_worker(
        interval_seconds=10.0,
        once=True,
        session_factory=lambda: None,
        storage=object(),
        vector_store=object(),
    )

    assert runs == 1


def test_purge_worker_survives_iteration_failure(monkeypatch) -> None:
    runs = 0
    stop = course_purge._SignalStopEvent()

    def mock_run_purge(**kwargs):
        nonlocal runs
        runs += 1
        if runs == 1:
            raise RuntimeError("transient db error")
        stop.requested = True
        return PurgeReport()

    monkeypatch.setattr(course_purge, "check_purge_ready", lambda **k: None)
    monkeypatch.setattr(course_purge, "run_purge", mock_run_purge)

    course_purge.run_purge_worker(
        interval_seconds=0.01,
        stop_event=stop,
        session_factory=lambda: None,
        storage=object(),
        vector_store=object(),
    )

    assert runs == 2


def test_purge_worker_readiness_failure(monkeypatch) -> None:
    def fail_readiness(**kwargs):
        raise course_purge.ReadinessError("storage unavailable")

    monkeypatch.setattr(course_purge, "check_purge_ready", fail_readiness)

    with pytest.raises(course_purge.ReadinessError):
        course_purge.run_purge_worker(
            interval_seconds=0.01,
            session_factory=lambda: None,
            storage=object(),
            vector_store=object(),
        )


def test_purge_check_cli(monkeypatch) -> None:
    called = False

    def mock_check():
        nonlocal called
        called = True

    monkeypatch.setattr(course_purge, "check_purge_ready", mock_check)
    course_purge.main(["--check"])
    assert called is True


def test_purge_check_cli_failure_exits_nonzero(monkeypatch) -> None:
    def fail_check():
        raise course_purge.ReadinessError("db down")

    monkeypatch.setattr(course_purge, "check_purge_ready", fail_check)
    with pytest.raises(SystemExit) as exc:
        course_purge.main(["--check"])
    assert exc.value.code == 1


def test_purge_worker_cli_with_interval(monkeypatch) -> None:
    called_with = {}

    def mock_worker(**kwargs):
        called_with.update(kwargs)

    monkeypatch.setattr(course_purge, "run_purge_worker", mock_worker)
    course_purge.main(["--interval-seconds", "300", "--once"])

    assert called_with["interval_seconds"] == 300.0
    assert called_with["once"] is True


def test_aged_tombstone_triggers_alert_and_metrics(
    session_factory, storage, store
) -> None:
    import logging
    from datetime import datetime, timedelta, timezone

    records: list[logging.LogRecord] = []

    class CapturingHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    logger = logging.getLogger("workers.course_purge")
    handler = CapturingHandler()
    logger.addHandler(handler)
    try:
        with session_factory() as session:
            course_id = _seed_course(
                session,
                storage,
                store,
                email="aged-tombstone@example.com",
                tombstoned=True,
            )
            course = session.get(Course, course_id)
            course.updated_at = datetime.now(timezone.utc) - timedelta(hours=3)
            session.commit()

        report = run_purge(
            session_factory=session_factory,
            storage=storage,
            vector_store=store,
            dry_run=True,
            aged_threshold_seconds=3600.0,
        )
    finally:
        logger.removeHandler(handler)

    assert report.courses_examined == 1
    assert report.aged_tombstones == 1
    assert report.oldest_tombstone_age_seconds >= 10000.0

    alert_logs = [
        r for r in records if getattr(r, "event", None) == "aged_tombstone_detected"
    ]
    assert len(alert_logs) == 1
    alert = alert_logs[0]
    assert alert.course_id == course_id
    assert alert.runbook == "docs/runbooks/stranded_tombstone.md"
    assert "aged-tombstone@example.com" not in alert.getMessage()
