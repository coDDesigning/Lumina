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
    EMBEDDING_DIMENSIONS,
    ChunkEmbedding,
    JOB_STATUS_FAILED,
    JOB_STATUS_QUEUED,
    JOB_STATUS_RUNNING,
    Course,
    DocumentChunk,
    DocumentPage,
    DocumentVisual,
    ProcessingJob,
    Role,
    UploadedDocument,
    User,
)
from backend.app.readiness import ReadinessError, check_readiness
from services.processing_jobs import (
    ChunkData,
    PageData,
    VisualData,
    claim_next_job,
    complete_job,
    enqueue_document_job,
    fail_job,
    replace_document_pages,
    update_job_stage,
)
from services.vector_store import PgVectorStore
from storage.local import LocalStorage

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ALEMBIC_CONFIG = PROJECT_ROOT / "alembic.ini"
EXPECTED_POSTGRESQL_MAJOR = 17
EXPECTED_POSTGRESQL_VERSION_NUMBER = 170008
BASE_REVISION = "97d9fd86a3ba"
PAGES_REVISION = "c4e6a8f1b203"
VISUAL_REVISION = "f7a3c9d2e541"
CHUNK_RANGES_REVISION = "a8c4e2f7b913"
HARDENING_REVISION = "a1c5e7f9b203"
HEAD_REVISION = "d7f3a2c48e15"

pytestmark = pytest.mark.skipif(
    not settings.is_hosted,
    reason="requires the live PostgreSQL CI service",
)


def _invoke_alembic(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(ALEMBIC_CONFIG), *arguments],
        cwd=PROJECT_ROOT,
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def _run_alembic(*arguments: str) -> None:
    completed = _invoke_alembic(*arguments)
    assert completed.returncode == 0, (
        f"Alembic {' '.join(arguments)} failed\n"
        f"stdout:\n{completed.stdout}\n"
        f"stderr:\n{completed.stderr}"
    )


def _generated_output_attribution_columns() -> set[str]:
    engine = create_database_engine(settings.database_url)
    try:
        return {
            column["name"]
            for column in inspect(engine).get_columns("generated_outputs")
        } & {"user_id", "model_used"}
    finally:
        engine.dispose()


def _assert_generated_output_attribution_present() -> None:
    engine = create_database_engine(settings.database_url)
    try:
        inspector = inspect(engine)
        columns = {
            column["name"]: column
            for column in inspector.get_columns("generated_outputs")
        }
        assert {"user_id", "model_used"} <= set(columns)
        assert columns["user_id"]["nullable"]
        assert columns["model_used"]["nullable"]
        assert {
            index["name"] for index in inspector.get_indexes("generated_outputs")
        } >= {"ix_generated_outputs_user_id"}
        foreign_keys = {
            constraint["name"]: constraint
            for constraint in inspector.get_foreign_keys("generated_outputs")
        }
        user_foreign_key = foreign_keys["fk_generated_outputs_user_id_users"]
        assert user_foreign_key["referred_table"] == "users"
        assert user_foreign_key["options"]["ondelete"] == "SET NULL"
    finally:
        engine.dispose()


def _assert_hardening_preflight_is_atomic() -> None:
    engine = create_database_engine(settings.database_url)
    try:
        with engine.begin() as connection:
            result = connection.execute(
                text(
                    "UPDATE processing_jobs AS j SET job_type = 'unknown' "
                    "FROM uploaded_documents AS d "
                    "WHERE j.document_id = d.id "
                    "AND d.storage_key = 'postgresql-migration/pending'"
                )
            )
            assert result.rowcount == 1

        completed = _invoke_alembic("upgrade", HEAD_REVISION)
        assert completed.returncode != 0
        assert "Unknown processing job types require manual correction" in (
            completed.stderr
        )

        with engine.connect() as connection:
            assert (
                connection.scalar(text("SELECT version_num FROM alembic_version"))
                == CHUNK_RANGES_REVISION
            )
            assert (
                connection.scalar(
                    text(
                        "SELECT to_regclass("
                        "'uq_uploaded_documents_storage_provider_storage_key')"
                    )
                )
                is None
            )
            assert (
                connection.scalar(
                    text(
                        "SELECT COUNT(*) FROM pg_constraint WHERE conname IN ("
                        "'ck_document_chunks_chunk_index_nonnegative', "
                        "'ck_processing_jobs_job_type_valid', "
                        "'ck_quiz_questions_question_index_nonnegative')"
                    )
                )
                == 0
            )

        with engine.begin() as connection:
            result = connection.execute(
                text(
                    "UPDATE processing_jobs AS j SET job_type = 'extract_document' "
                    "FROM uploaded_documents AS d "
                    "WHERE j.document_id = d.id "
                    "AND d.storage_key = 'postgresql-migration/pending'"
                )
            )
            assert result.rowcount == 1
    finally:
        engine.dispose()


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


def _seed_page_revision() -> None:
    engine = create_database_engine(settings.database_url)
    try:
        with engine.begin() as connection:
            result = connection.execute(
                text(
                    "INSERT INTO document_pages "
                    "(document_id, course_id, content_index, page_number, text, "
                    "extraction_method, has_images, needs_ocr) "
                    "SELECT id, course_id, 0, 1, 'Legacy PostgreSQL page', "
                    "'native', true, true FROM uploaded_documents "
                    "WHERE storage_key = 'postgresql-migration/completed'"
                )
            )
            assert result.rowcount == 1
    finally:
        engine.dispose()


def _assert_visual_enrichment_backfill() -> None:
    engine = create_database_engine(settings.database_url)
    try:
        with engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT p.raw_text, p.text, p.raw_extraction_method, "
                    "p.extraction_method, p.raw_needs_ocr, "
                    "p.has_visual_content, p.ocr_status, "
                    "p.visual_analysis_status FROM document_pages AS p "
                    "JOIN uploaded_documents AS d ON d.id = p.document_id "
                    "WHERE d.storage_key = 'postgresql-migration/completed'"
                )
            ).one()
        assert row == (
            "Legacy PostgreSQL page",
            "Legacy PostgreSQL page",
            "native",
            "native",
            True,
            False,
            "pending",
            "not_applicable",
        )
    finally:
        engine.dispose()


def _enrich_migrated_page() -> None:
    engine = create_database_engine(settings.database_url)
    try:
        with engine.begin() as connection:
            result = connection.execute(
                text(
                    "UPDATE document_pages SET text = 'OCR PostgreSQL page', "
                    "extraction_method = 'ocr', needs_ocr = false, "
                    "ocr_status = 'succeeded' WHERE document_id = "
                    "(SELECT id FROM uploaded_documents WHERE "
                    "storage_key = 'postgresql-migration/completed')"
                )
            )
            assert result.rowcount == 1
    finally:
        engine.dispose()


def _assert_downgraded_page_is_raw() -> None:
    engine = create_database_engine(settings.database_url)
    try:
        with engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT p.text, p.extraction_method FROM document_pages AS p "
                    "JOIN uploaded_documents AS d ON d.id = p.document_id "
                    "WHERE d.storage_key = 'postgresql-migration/completed'"
                )
            ).one()
        assert row == ("Legacy PostgreSQL page", "native")
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
    _run_alembic("upgrade", PAGES_REVISION)
    _assert_processing_backfill(deleted_error_code="COURSE_DELETED")
    _seed_page_revision()
    _run_alembic("upgrade", VISUAL_REVISION)
    _run_alembic("upgrade", CHUNK_RANGES_REVISION)
    _assert_hardening_preflight_is_atomic()
    _run_alembic("upgrade", HEAD_REVISION)
    _assert_visual_enrichment_backfill()
    _assert_generated_output_attribution_present()
    _run_alembic("downgrade", HARDENING_REVISION)
    assert _generated_output_attribution_columns() == set()
    _run_alembic("upgrade", HEAD_REVISION)
    _assert_generated_output_attribution_present()
    _enrich_migrated_page()
    _run_alembic("downgrade", PAGES_REVISION)
    _assert_downgraded_page_is_raw()

    pages_engine = create_database_engine(settings.database_url)
    try:
        pages_inspector = inspect(pages_engine)
        assert "document_visuals" not in pages_inspector.get_table_names()
        assert {
            "raw_text",
            "raw_extraction_method",
            "raw_needs_ocr",
            "has_visual_content",
            "ocr_status",
            "visual_analysis_status",
        }.isdisjoint(
            column["name"] for column in pages_inspector.get_columns("document_pages")
        )
    finally:
        pages_engine.dispose()

    _run_alembic("upgrade", HEAD_REVISION)
    _assert_visual_enrichment_backfill()
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
    file_type: str = "txt",
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
        semester="Fall",
        exam_date="2026",
    )
    session.add(course)
    session.flush()

    document_ids: list[UUID] = []
    job_ids: list[int] = []
    for index in range(count):
        document_id = uuid4()
        document = UploadedDocument(
            id=document_id,
            original_file_name=f"notes-{index}.{file_type}",
            file_type=file_type,
            mime_type="application/pdf" if file_type == "pdf" else "text/plain",
            file_size=7,
            file_hash=f"{index:064x}",
            uploader=user,
            course=course,
            storage_provider="local:postgresql-ci",
            storage_key=f"postgresql/{document_id}.{file_type}",
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
        revision = connection.scalar(text("SELECT version_num FROM alembic_version"))
    assert version_number // 10_000 == EXPECTED_POSTGRESQL_MAJOR
    assert version_number == EXPECTED_POSTGRESQL_VERSION_NUMBER
    assert revision == HEAD_REVISION

    inspector = inspect(postgresql_engine)
    assert set(Base.metadata.tables) <= set(inspector.get_table_names())
    assert {index["name"] for index in inspector.get_indexes("uploaded_documents")} >= {
        "uq_uploaded_documents_storage_provider_storage_key"
    }
    assert {
        constraint["name"]
        for constraint in inspector.get_check_constraints("document_chunks")
    } >= {
        "ck_document_chunks_page_range_valid",
        "ck_document_chunks_chunk_index_nonnegative",
    }
    assert {
        constraint["name"]
        for constraint in inspector.get_check_constraints("processing_jobs")
    } >= {
        "ck_processing_jobs_job_type_valid",
        "ck_processing_jobs_failed_error_code_nonblank",
        "ck_processing_jobs_running_lease_owner_nonblank",
        "ck_processing_jobs_running_claim_token_length",
    }
    assert {
        constraint["name"]
        for constraint in inspector.get_check_constraints("quiz_questions")
    } >= {
        "ck_quiz_questions_question_index_nonnegative",
        "ck_quiz_questions_correct_option_index_nonnegative",
    }
    page_columns = {
        column["name"]: column for column in inspector.get_columns("document_pages")
    }
    assert {
        "raw_text",
        "raw_extraction_method",
        "raw_needs_ocr",
        "has_visual_content",
        "ocr_status",
        "visual_analysis_status",
    } <= set(page_columns)
    assert not page_columns["raw_text"]["nullable"]
    chunk_columns = {
        column["name"]: column for column in inspector.get_columns("document_chunks")
    }
    assert "end_page_number" in chunk_columns
    _assert_generated_output_attribution_present()
    assert {
        constraint["name"]
        for constraint in inspector.get_check_constraints("document_pages")
    } >= {
        "ck_document_pages_raw_extraction_method_valid",
        "ck_document_pages_extraction_method_valid",
        "ck_document_pages_ocr_candidate_valid",
        "ck_document_pages_ocr_status_valid",
        "ck_document_pages_visual_analysis_status_valid",
    }

    visual_columns = {
        column["name"] for column in inspector.get_columns("document_visuals")
    }
    assert visual_columns >= {
        "page_id",
        "visual_index",
        "visual_type",
        "source",
        "bbox_x0",
        "bbox_y0",
        "bbox_x1",
        "bbox_y1",
        "description",
        "analysis_status",
        "error_code",
    }
    assert {
        constraint["name"]
        for constraint in inspector.get_check_constraints("document_visuals")
    } >= {
        "ck_document_visuals_visual_index_nonnegative",
        "ck_document_visuals_visual_type_valid",
        "ck_document_visuals_source_valid",
        "ck_document_visuals_bbox_valid",
        "ck_document_visuals_analysis_status_valid",
        "ck_document_visuals_description_status_valid",
        "ck_document_visuals_failed_error_code_required",
    }
    assert {
        constraint["name"]
        for constraint in inspector.get_unique_constraints("document_visuals")
    } >= {"uq_visual_page_index"}
    visual_foreign_keys = {
        constraint["name"]: constraint
        for constraint in inspector.get_foreign_keys("document_visuals")
    }
    visual_page_foreign_key = visual_foreign_keys[
        "fk_document_visuals_page_id_document_pages"
    ]
    assert visual_page_foreign_key["referred_table"] == "document_pages"
    assert visual_page_foreign_key["options"]["ondelete"] == "CASCADE"
    assert {index["name"] for index in inspector.get_indexes("document_visuals")} >= {
        "ix_document_visuals_page_id"
    }

    storage = LocalStorage(tmp_path_factory.mktemp("postgresql-readiness"))
    with postgresql_sessions() as session:
        assert set(session.scalars(select(Role.name))) >= {"admin", "user"}
        check_readiness(session, storage)
    with postgresql_sessions() as session:
        session.execute(text("SET TRANSACTION READ ONLY"))
        with pytest.raises(ReadinessError):
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
            page_number=1,
            raw_text="Raw PostgreSQL cascade",
            text="OCR PostgreSQL cascade",
            raw_extraction_method="decoded",
            extraction_method="ocr",
            has_images=True,
            needs_ocr=True,
            ocr_status="succeeded",
            has_visual_content=True,
            visual_analysis_status="completed",
            visuals=[
                DocumentVisual(
                    visual_index=0,
                    visual_type="diagram",
                    source="drawing",
                    bbox_x0=1.0,
                    bbox_y0=2.0,
                    bbox_x1=10.0,
                    bbox_y1=20.0,
                    description="PostgreSQL diagram",
                    analysis_status="succeeded",
                )
            ],
        )
        session.add_all((chunk, page))
        session.commit()
        chunk_id = chunk.id
        page_id = page.id
        visual_id = page.visuals[0].id

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
        persisted_page = session.scalar(
            select(DocumentPage)
            .options(selectinload(DocumentPage.visuals))
            .where(DocumentPage.id == page_id)
        )
        assert persisted_page is not None
        assert persisted_page.raw_text == "Raw PostgreSQL cascade"
        assert persisted_page.text == "OCR PostgreSQL cascade"
        assert persisted_page.raw_extraction_method == "decoded"
        assert persisted_page.extraction_method == "ocr"
        assert persisted_page.ocr_status == "succeeded"
        assert persisted_page.visual_analysis_status == "completed"
        assert len(persisted_page.visuals) == 1
        assert persisted_page.visuals[0].description == "PostgreSQL diagram"

        session.delete(user)
        session.commit()

    with postgresql_sessions() as session:
        assert session.get(User, user_id) is None
        assert session.get(UploadedDocument, document_ids[0]) is None
        assert session.get(DocumentChunk, chunk_id) is None
        assert session.get(DocumentPage, page_id) is None
        assert session.get(DocumentVisual, visual_id) is None
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
        user_id, document_ids, _job_ids = _queue_documents(
            session,
            count=1,
            file_type="pdf",
        )
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
                    text="Raw\x00 PostgreSQL extraction",
                    page_number=1,
                    extraction_method="decoded",
                    has_images=False,
                    needs_ocr=False,
                    raw_text="Raw\x00 PostgreSQL extraction",
                    raw_extraction_method="decoded",
                    has_visual_content=True,
                    ocr_status="not_required",
                    visual_analysis_status="pending",
                    visuals=(
                        VisualData(
                            visual_index=0,
                            visual_type="chart",
                            source="image",
                            bbox=(1.0, 2.0, 10.0, 20.0),
                            analysis_status="pending",
                        ),
                    ),
                )
            ],
        )
    with postgresql_sessions() as session:
        assert update_job_stage(
            session,
            claim.id,
            claim.claim_token,
            "cleaning_text",
        )
    with postgresql_sessions() as session:
        assert not complete_job(
            session,
            claim.id,
            claim.claim_token,
            [ChunkData(text="Too early", page_number=1, end_page_number=1)],
            embeddings=[[0.1] * EMBEDDING_DIMENSIONS],
            vector_store=PgVectorStore(),
        )
    with postgresql_sessions() as session:
        job = session.get(ProcessingJob, claim.id)
        assert job is not None
        assert job.processing_stage == "cleaning_text"
        assert (
            session.scalar(
                select(DocumentChunk.id).where(
                    DocumentChunk.document_id == document_ids[0]
                )
            )
            is None
        )
        assert update_job_stage(
            session,
            claim.id,
            claim.claim_token,
            "chunking",
        )
        assert update_job_stage(
            session,
            claim.id,
            claim.claim_token,
            "generating_embeddings",
        )
    with postgresql_sessions() as session:
        page = session.scalar(
            select(DocumentPage)
            .options(selectinload(DocumentPage.visuals))
            .where(DocumentPage.document_id == document_ids[0])
        )
        assert page is not None
        assert page.raw_text == "Raw PostgreSQL extraction"
        assert page.text == "Raw PostgreSQL extraction"
        assert page.raw_extraction_method == "decoded"
        assert page.extraction_method == "decoded"
        assert page.ocr_status == "not_required"
        assert page.visual_analysis_status == "pending"
        assert len(page.visuals) == 1
        assert page.visuals[0].description is None
    enriched_page = PageData(
        content_index=0,
        text="Enriched\x00 PostgreSQL extraction",
        page_number=1,
        extraction_method="ocr",
        has_images=False,
        needs_ocr=False,
        raw_text="Raw\x00 PostgreSQL extraction",
        raw_extraction_method="decoded",
        has_visual_content=True,
        ocr_status="succeeded",
        visual_analysis_status="partial",
        visuals=(
            VisualData(
                visual_index=0,
                visual_type="chart",
                source="image",
                bbox=(1.0, 2.0, 10.0, 20.0),
                description="PostgreSQL\x00 chart",
                analysis_status="succeeded",
            ),
            VisualData(
                visual_index=1,
                visual_type="figure",
                source="image",
                bbox=(11.0, 2.0, 20.0, 20.0),
                analysis_status="failed",
                error_code=" " * 100 + "POSTGRESQL\x00_" + "X" * 100,
            ),
        ),
    )
    with postgresql_sessions() as session:
        assert complete_job(
            session,
            claim.id,
            claim.claim_token,
            [
                ChunkData(
                    text="Enriched\x00 PostgreSQL extraction",
                    page_number=1,
                    end_page_number=1,
                )
            ],
            [enriched_page],
            embeddings=[[0.1] * EMBEDDING_DIMENSIONS],
            vector_store=PgVectorStore(),
        )
    with postgresql_sessions() as session:
        enriched = session.scalar(
            select(DocumentPage)
            .options(selectinload(DocumentPage.visuals))
            .where(DocumentPage.document_id == document_ids[0])
        )
        assert enriched is not None
        assert enriched.raw_text == "Raw PostgreSQL extraction"
        assert enriched.text == "Enriched PostgreSQL extraction"
        assert enriched.extraction_method == "ocr"
        assert enriched.ocr_status == "succeeded"
        assert enriched.visual_analysis_status == "partial"
        assert enriched.visuals[0].description == "PostgreSQL chart"
        assert enriched.visuals[1].error_code == ("POSTGRESQL_" + "X" * 100)[:100]
        chunk = session.scalar(
            select(DocumentChunk).where(DocumentChunk.document_id == document_ids[0])
        )
        assert chunk is not None
        assert chunk.text == "Enriched PostgreSQL extraction"
        assert (chunk.page_number, chunk.end_page_number) == (1, 1)

    with postgresql_sessions() as session:
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


def test_postgresql_terminal_failure_sanitizes_error_text(
    postgresql_sessions: sessionmaker[Session],
) -> None:
    with postgresql_sessions() as session:
        user_id, document_ids, _job_ids = _queue_documents(session, count=1)
    with postgresql_sessions() as session:
        claim = claim_next_job(
            session,
            "postgresql-failure-worker",
            "local:postgresql-ci",
            60,
        )
    assert claim is not None

    with postgresql_sessions() as session:
        assert (
            fail_job(
                session,
                claim.id,
                claim.claim_token,
                error_code="PROVIDER\x00_FAILURE",
                error_message="Provider\x00 failed",
                retryable=False,
            )
            == JOB_STATUS_FAILED
        )
    with postgresql_sessions() as session:
        job = session.get(ProcessingJob, claim.id)
        document = session.get(UploadedDocument, document_ids[0])
        user = session.get(User, user_id)
        assert job is not None
        assert document is not None
        assert user is not None
        assert job.last_error_code == "PROVIDER_FAILURE"
        assert job.last_error_message == "Provider failed"
        assert document.processing_error == "Provider failed"
        session.delete(user)
        session.commit()


def test_postgresql_provisions_pgvector_and_its_index(
    postgresql_sessions: sessionmaker[Session],
) -> None:
    """Similarity search needs the extension, a real vector column, and an index."""
    with postgresql_sessions() as session:
        assert session.scalar(
            text("SELECT count(*) FROM pg_extension WHERE extname = 'vector'")
        )

        column_type = session.scalar(
            text(
                "SELECT udt_name FROM information_schema.columns "
                "WHERE table_name = 'chunk_embeddings' AND column_name = 'embedding'"
            )
        )
        assert column_type == "vector"

        index_definition = session.scalar(
            text(
                "SELECT indexdef FROM pg_indexes "
                "WHERE tablename = 'chunk_embeddings' "
                "AND indexname = 'ix_chunk_embeddings_embedding_hnsw'"
            )
        )
        assert index_definition is not None
        assert "hnsw" in index_definition
        assert "vector_cosine_ops" in index_definition


def test_postgresql_chunk_embeddings_round_trip_and_rank_by_cosine(
    postgresql_sessions: sessionmaker[Session],
) -> None:
    """The stored vector must behave as a vector, not as opaque bytes."""
    document_id = uuid4()
    with postgresql_sessions() as session:
        role = session.scalar(select(Role).where(Role.name == "user"))
        assert role is not None
        user = User(
            name="Vector owner",
            email="pgvector-owner@example.com",
            password_hash="not-a-real-hash",
            role=role,
        )
        course = Course(title="Vector course", owner=user)
        document = UploadedDocument(
            id=document_id,
            original_file_name="vectors.txt",
            file_type="txt",
            mime_type="text/plain",
            file_size=32,
            file_hash=document_id.hex * 2,
            uploader=user,
            course=course,
            storage_provider="local",
            storage_key=f"local/{document_id}.txt",
            status="ready",
        )
        near = DocumentChunk(
            document=document, course=course, chunk_index=0, text="near"
        )
        far = DocumentChunk(document=document, course=course, chunk_index=1, text="far")
        session.add_all((user, course, document, near, far))
        session.flush()

        near_vector = [1.0] + [0.0] * (EMBEDDING_DIMENSIONS - 1)
        far_vector = [0.0] * (EMBEDDING_DIMENSIONS - 1) + [1.0]
        session.add_all(
            (
                ChunkEmbedding(
                    chunk_id=near.id,
                    document_id=document_id,
                    course_id=course.id,
                    chunk_index=0,
                    embedding=near_vector,
                    embedding_provider="ollama",
                    embedding_model="nomic-embed-text",
                    dimensions=EMBEDDING_DIMENSIONS,
                ),
                ChunkEmbedding(
                    chunk_id=far.id,
                    document_id=document_id,
                    course_id=course.id,
                    chunk_index=1,
                    embedding=far_vector,
                    embedding_provider="ollama",
                    embedding_model="nomic-embed-text",
                    dimensions=EMBEDDING_DIMENSIONS,
                ),
            )
        )
        other_owner = User(
            name="Other owner",
            email="pgvector-other@example.com",
            password_hash="not-a-real-hash",
            role=role,
        )
        other_course = Course(title="Other vector course", owner=other_owner)
        other_document = UploadedDocument(
            id=uuid4(),
            original_file_name="other.txt",
            file_type="txt",
            mime_type="text/plain",
            file_size=32,
            file_hash=uuid4().hex * 2,
            uploader=other_owner,
            course=other_course,
            storage_provider="local",
            storage_key=f"local/{uuid4()}.txt",
            status="ready",
        )
        other_chunk = DocumentChunk(
            document=other_document,
            course=other_course,
            chunk_index=0,
            text="other near",
        )
        session.add_all((other_owner, other_course, other_document, other_chunk))
        session.flush()
        session.add(
            ChunkEmbedding(
                chunk_id=other_chunk.id,
                document_id=other_document.id,
                course_id=other_course.id,
                chunk_index=0,
                embedding=near_vector,
                embedding_provider="ollama",
                embedding_model="nomic-embed-text",
                dimensions=EMBEDDING_DIMENSIONS,
            )
        )
        session.commit()
        user_id = user.id
        course_id = course.id
        other_owner_id = other_owner.id

    with postgresql_sessions() as session:
        stored = session.scalar(
            select(ChunkEmbedding).where(ChunkEmbedding.document_id == document_id)
        )
        assert stored is not None
        assert len(stored.embedding) == EMBEDDING_DIMENSIONS
        assert stored.embedding[0] == pytest.approx(1.0)

        store = PgVectorStore()
        ranked = store.search(
            session,
            course_id=course_id,
            query_embedding=[1.0] + [0.0] * (EMBEDDING_DIMENSIONS - 1),
            limit=2,
        )
        assert [result.chunk_index for result in ranked] == [0, 1]
        assert ranked[0].similarity == pytest.approx(1.0)
        assert all(result.course_id == course_id for result in ranked)

    with postgresql_sessions() as session:
        user = session.get(User, user_id)
        assert user is not None
        session.delete(user)
        other_owner = session.get(User, other_owner_id)
        assert other_owner is not None
        session.delete(other_owner)
        session.commit()
        assert (
            session.scalar(
                select(ChunkEmbedding).where(ChunkEmbedding.document_id == document_id)
            )
            is None
        )
