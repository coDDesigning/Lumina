from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, inspect, select, text
from sqlalchemy.orm import Session, selectinload, sessionmaker

from backend.app.base import Base
from backend.app.config import settings
from backend.app.database_engine import create_database_engine
from backend.app.models import (
    JOB_STATUS_QUEUED,
    JOB_STATUS_RUNNING,
    Course,
    DocumentChunk,
    DocumentPage,
    ProcessingJob,
    Role,
    UploadedDocument,
    User,
)
from backend.app.readiness import check_readiness
from services.processing_jobs import (
    PageData,
    claim_next_job,
    enqueue_document_job,
    replace_document_pages,
    update_job_stage,
)
from storage.local import LocalStorage

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ALEMBIC_CONFIG = PROJECT_ROOT / "alembic.ini"
EXPECTED_POSTGRESQL_MAJOR = 17
EXPECTED_POSTGRESQL_VERSION_NUMBER = 170006
BASE_REVISION = "97d9fd86a3ba"
HEAD_REVISION = "c4e6a8f1b203"

pytestmark = pytest.mark.skipif(
    not settings.is_hosted,
    reason="requires the live PostgreSQL CI service",
)


def _run_alembic(*arguments: str) -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(ALEMBIC_CONFIG), *arguments],
        cwd=PROJECT_ROOT,
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert completed.returncode == 0, (
        f"Alembic {' '.join(arguments)} failed\n"
        f"stdout:\n{completed.stdout}\n"
        f"stderr:\n{completed.stderr}"
    )


def _seed_base_revision() -> None:
    engine = create_database_engine(settings.database_url)
    try:
        with engine.begin() as connection:
            role_id = connection.scalar(
                text("SELECT id FROM roles WHERE name = 'user'")
            )
            user_id = connection.scalar(
                text(
                    "INSERT INTO users (name, email, password_hash, role_id) "
                    "VALUES ('Migration user', 'migration@example.com', 'hash', "
                    ":role_id) RETURNING id"
                ),
                {"role_id": role_id},
            )
            active_course_id = connection.scalar(
                text(
                    "INSERT INTO courses (title, instructor, owner_id) "
                    "VALUES ('Active migration course', 'Instructor', :owner_id) "
                    "RETURNING id"
                ),
                {"owner_id": user_id},
            )
            deleted_course_id = connection.scalar(
                text(
                    "INSERT INTO courses "
                    "(title, instructor, owner_id, is_deleted) "
                    "VALUES ('Deleted migration course', 'Instructor', :owner_id, "
                    "true) RETURNING id"
                ),
                {"owner_id": user_id},
            )

            document_ids: dict[str, UUID] = {}
            for index, (name, status, course_id) in enumerate(
                (
                    ("completed", "completed", active_course_id),
                    ("deleted", "pending", deleted_course_id),
                    ("pending", "pending", active_course_id),
                    ("processing", "processing", active_course_id),
                )
            ):
                document_id = uuid4()
                document_ids[name] = document_id
                connection.execute(
                    text(
                        "INSERT INTO uploaded_documents "
                        "(id, original_file_name, file_type, mime_type, file_size, "
                        "file_hash, user_id, course_id, storage_provider, "
                        "storage_key, status) VALUES "
                        "(:id, :name, 'txt', 'text/plain', 7, :file_hash, "
                        ":user_id, :course_id, 'local:postgresql-ci', "
                        ":storage_key, :status)"
                    ),
                    {
                        "id": document_id,
                        "name": f"{name}.txt",
                        "file_hash": f"{index:064x}",
                        "user_id": user_id,
                        "course_id": course_id,
                        "storage_key": f"postgresql-migration/{name}",
                        "status": status,
                    },
                )

            connection.execute(
                text(
                    "INSERT INTO document_chunks "
                    "(document_id, course_id, chunk_index, text) "
                    "VALUES (:document_id, :course_id, 0, 'Completed chunk')"
                ),
                {
                    "document_id": document_ids["completed"],
                    "course_id": active_course_id,
                },
            )
    finally:
        engine.dispose()


def _assert_processing_backfill(*, deleted_error_code: str) -> None:
    engine = create_database_engine(settings.database_url)
    try:
        with engine.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT d.storage_key, d.status, j.status, j.attempt_count, "
                    "j.last_error_code FROM uploaded_documents AS d "
                    "JOIN processing_jobs AS j ON j.document_id = d.id "
                    "WHERE d.storage_key LIKE 'postgresql-migration/%' "
                    "ORDER BY d.storage_key"
                )
            ).all()
        assert rows == [
            (
                "postgresql-migration/completed",
                "ready",
                "succeeded",
                1,
                None,
            ),
            (
                "postgresql-migration/deleted",
                "failed",
                "failed",
                3,
                deleted_error_code,
            ),
            ("postgresql-migration/pending", "uploaded", "queued", 0, None),
            ("postgresql-migration/processing", "uploaded", "queued", 0, None),
        ]
    finally:
        engine.dispose()


def _mark_migrated_job_running() -> None:
    engine = create_database_engine(settings.database_url)
    try:
        with engine.begin() as connection:
            job_result = connection.execute(
                text(
                    "UPDATE processing_jobs AS j SET status = 'running', "
                    "attempt_count = 1, started_at = CURRENT_TIMESTAMP, "
                    "claimed_at = CURRENT_TIMESTAMP, "
                    "heartbeat_at = CURRENT_TIMESTAMP, "
                    "lease_expires_at = CURRENT_TIMESTAMP + INTERVAL '60 seconds', "
                    "lease_owner = 'migration-worker', "
                    "claim_token = '00000000-0000-0000-0000-000000000000', "
                    "updated_at = CURRENT_TIMESTAMP "
                    "FROM uploaded_documents AS d "
                    "WHERE j.document_id = d.id "
                    "AND d.storage_key = 'postgresql-migration/pending'"
                )
            )
            document_result = connection.execute(
                text(
                    "UPDATE uploaded_documents SET status = 'processing' "
                    "WHERE storage_key = 'postgresql-migration/pending'"
                )
            )
            assert job_result.rowcount == 1
            assert document_result.rowcount == 1
    finally:
        engine.dispose()


@pytest.fixture(scope="module")
def postgresql_engine() -> Iterator[Engine]:
    assert settings.is_hosted

    guard_engine = create_database_engine(settings.database_url)
    try:
        with guard_engine.connect() as connection:
            assert connection.scalar(text("SELECT current_database()")) == "lumina_ci"
        assert inspect(guard_engine).get_table_names() == []
    finally:
        guard_engine.dispose()

    _run_alembic("upgrade", BASE_REVISION)
    _seed_base_revision()
    _run_alembic("upgrade", HEAD_REVISION)
    _assert_processing_backfill(deleted_error_code="COURSE_DELETED")
    _mark_migrated_job_running()
    _run_alembic("downgrade", BASE_REVISION)

    base_engine = create_database_engine(settings.database_url)
    try:
        with base_engine.connect() as connection:
            status = connection.scalar(
                text(
                    "SELECT status FROM uploaded_documents "
                    "WHERE storage_key = 'postgresql-migration/pending'"
                )
            )
        assert status == "pending"
        assert "processing_jobs" not in inspect(base_engine).get_table_names()
        assert "page_number" not in {
            column["name"]
            for column in inspect(base_engine).get_columns("document_chunks")
        }
    finally:
        base_engine.dispose()

    _run_alembic("upgrade", HEAD_REVISION)
    _assert_processing_backfill(deleted_error_code="LEGACY_PROCESSING_FAILED")
    _run_alembic("current", "--check-heads")
    _run_alembic("check")
    _run_alembic("downgrade", "base")

    downgraded_engine = create_database_engine(settings.database_url)
    try:
        assert set(inspect(downgraded_engine).get_table_names()) == {"alembic_version"}
    finally:
        downgraded_engine.dispose()

    _run_alembic("upgrade", HEAD_REVISION)
    _run_alembic("current", "--check-heads")
    _run_alembic("check")

    engine = create_database_engine(settings.database_url)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture(scope="module")
def postgresql_sessions(
    postgresql_engine: Engine,
) -> sessionmaker[Session]:
    return sessionmaker(
        bind=postgresql_engine,
        class_=Session,
        expire_on_commit=False,
        autoflush=False,
    )


def _queue_documents(
    session: Session,
    *,
    count: int,
    now: datetime | None = None,
) -> tuple[int, list[UUID], list[int]]:
    role = session.scalar(select(Role).where(Role.name == "user"))
    assert role is not None
    suffix = uuid4().hex
    user = User(
        name="PostgreSQL worker",
        email=f"postgres-{suffix}@example.com",
        password_hash="not-a-real-hash",
        role=role,
    )
    course = Course(
        owner=user,
        title="PostgreSQL course",
        description=None,
        instructor="PostgreSQL worker",
        price=0,
    )
    session.add(course)
    session.flush()

    document_ids: list[UUID] = []
    job_ids: list[int] = []
    for index in range(count):
        document_id = uuid4()
        document = UploadedDocument(
            id=document_id,
            original_file_name=f"notes-{index}.txt",
            file_type="txt",
            mime_type="text/plain",
            file_size=7,
            file_hash=f"{index:064x}",
            uploader=user,
            course=course,
            storage_provider="local:postgresql-ci",
            storage_key=f"postgresql/{document_id}.txt",
            status="uploaded",
        )
        session.add(document)
        session.flush()
        job = enqueue_document_job(session, document, max_attempts=3, now=now)
        document_ids.append(document.id)
        job_ids.append(job.id)

    session.commit()
    return user.id, document_ids, job_ids


def test_postgresql_schema_readiness_and_role_seeds(
    postgresql_engine: Engine,
    postgresql_sessions: sessionmaker[Session],
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    assert postgresql_engine.dialect.name == "postgresql"
    with postgresql_engine.connect() as connection:
        version_number = int(connection.scalar(text("SHOW server_version_num")))
    assert version_number // 10_000 == EXPECTED_POSTGRESQL_MAJOR
    assert version_number == EXPECTED_POSTGRESQL_VERSION_NUMBER
    assert set(Base.metadata.tables) <= set(
        inspect(postgresql_engine).get_table_names()
    )

    storage = LocalStorage(tmp_path_factory.mktemp("postgresql-readiness"))
    with postgresql_sessions() as session:
        assert set(session.scalars(select(Role.name))) >= {"admin", "user"}
        check_readiness(session, storage)


def test_postgresql_uuid_timestamps_and_loaded_cascades(
    postgresql_sessions: sessionmaker[Session],
) -> None:
    supplied_time = datetime(
        2026,
        8,
        11,
        23,
        30,
        tzinfo=timezone(timedelta(hours=5, minutes=30)),
    )
    expected_time = supplied_time.astimezone(timezone.utc)
    with postgresql_sessions() as session:
        user_id, document_ids, job_ids = _queue_documents(
            session,
            count=1,
            now=supplied_time,
        )
        document = session.get(UploadedDocument, document_ids[0])
        assert document is not None
        chunk = DocumentChunk(
            document=document,
            course=document.course,
            chunk_index=0,
            page_number=None,
            text="PostgreSQL cascade",
        )
        page = DocumentPage(
            document=document,
            course=document.course,
            content_index=0,
            page_number=None,
            text="Raw PostgreSQL cascade",
            extraction_method="decoded",
            has_images=False,
            needs_ocr=False,
        )
        session.add_all((chunk, page))
        session.commit()
        chunk_id = chunk.id
        page_id = page.id

    with postgresql_sessions() as session:
        document = session.get(UploadedDocument, document_ids[0])
        job = session.get(ProcessingJob, job_ids[0])
        user = session.scalar(
            select(User)
            .options(selectinload(User.uploaded_documents))
            .where(User.id == user_id)
        )
        assert document is not None
        assert job is not None
        assert user is not None
        assert isinstance(document.id, UUID)
        assert document.created_at.tzinfo is not None
        assert job.available_at == expected_time
        assert job.available_at.utcoffset() == timedelta(0)
        assert [item.id for item in user.uploaded_documents] == document_ids

        session.delete(user)
        session.commit()

    with postgresql_sessions() as session:
        assert session.get(User, user_id) is None
        assert session.get(UploadedDocument, document_ids[0]) is None
        assert session.get(DocumentChunk, chunk_id) is None
        assert session.get(DocumentPage, page_id) is None
        assert session.get(ProcessingJob, job_ids[0]) is None


def test_postgresql_claim_skips_locked_job(
    postgresql_sessions: sessionmaker[Session],
) -> None:
    with postgresql_sessions() as session:
        user_id, _document_ids, job_ids = _queue_documents(session, count=2)

    with postgresql_sessions() as lock_session:
        locked_job_id = lock_session.scalar(
            select(ProcessingJob.id)
            .where(ProcessingJob.id == job_ids[0])
            .with_for_update()
        )
        assert locked_job_id == job_ids[0]

        with postgresql_sessions() as claim_session:
            claimed = claim_next_job(
                claim_session,
                "postgresql-worker",
                "local:postgresql-ci",
                60,
            )

        assert claimed is not None
        assert claimed.id == job_ids[1]
        lock_session.rollback()

    with postgresql_sessions() as session:
        statuses = {
            job_id: status
            for job_id, status in session.execute(
                select(ProcessingJob.id, ProcessingJob.status).where(
                    ProcessingJob.id.in_(job_ids)
                )
            ).all()
        }
        assert statuses == {
            job_ids[0]: JOB_STATUS_QUEUED,
            job_ids[1]: JOB_STATUS_RUNNING,
        }
        user = session.get(User, user_id)
        assert user is not None
        session.delete(user)
        session.commit()


def test_postgresql_raw_page_replacement_is_claim_fenced(
    postgresql_sessions: sessionmaker[Session],
) -> None:
    with postgresql_sessions() as session:
        user_id, document_ids, _job_ids = _queue_documents(session, count=1)
    with postgresql_sessions() as session:
        claim = claim_next_job(
            session,
            "postgresql-page-worker",
            "local:postgresql-ci",
            60,
        )
    assert claim is not None
    with postgresql_sessions() as session:
        assert update_job_stage(
            session,
            claim.id,
            claim.claim_token,
            "extracting_text",
        )
    with postgresql_sessions() as session:
        assert replace_document_pages(
            session,
            claim.id,
            claim.claim_token,
            [
                PageData(
                    content_index=0,
                    text="Raw PostgreSQL extraction",
                    page_number=None,
                    extraction_method="decoded",
                    has_images=False,
                    needs_ocr=False,
                )
            ],
        )
    with postgresql_sessions() as session:
        page = session.scalar(
            select(DocumentPage).where(DocumentPage.document_id == document_ids[0])
        )
        assert page is not None
        assert page.text == "Raw PostgreSQL extraction"
        assert not replace_document_pages(
            session,
            claim.id,
            "stale-claim",
            [
                PageData(
                    content_index=0,
                    text="Stale extraction",
                    page_number=None,
                    extraction_method="decoded",
                    has_images=False,
                    needs_ocr=False,
                )
            ],
        )
    with postgresql_sessions() as session:
        user = session.get(User, user_id)
        assert user is not None
        session.delete(user)
        session.commit()
