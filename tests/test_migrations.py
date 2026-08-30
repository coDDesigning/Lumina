import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ALEMBIC_CONFIG = PROJECT_ROOT / "alembic.ini"
ALEMBIC_DIRECTORY = PROJECT_ROOT / "alembic"
ALEMBIC_VERSIONS_DIRECTORY = ALEMBIC_DIRECTORY / "versions"
BASE_REVISION = "97d9fd86a3ba"
PROCESSING_REVISION = "b6d8f2a4c901"
STAGES_REVISION = "d2a7f0c91e35"
PAGES_REVISION = "c4e6a8f1b203"
VISUAL_REVISION = "f7a3c9d2e541"
CHUNK_RANGES_REVISION = "a8c4e2f7b913"
COURSE_FIELDS_REVISION = "a4fd52f56b91"
AI_USAGE_REVISION = "b7e2a9d1c3f4"
SYLLABUS_REVISION = "e5c1a7b39d64"
HARDENING_REVISION = "a1c5e7f9b203"
ATTRIBUTION_REVISION = "c9b3d5e08f27"
QUIZ_ATTEMPT_ANSWERS_REVISION = "d3f8b21a6c40"
PROFILE_KNOWLEDGE_REVISION = "e4a7b1c90d52"
CHUNK_EMBEDDINGS_REVISION = "f4b18c7a2e60"
CONVERSATION_HISTORY_REVISION = "910e2719d549"
MODEL_CREDITS_REVISION = "2a7c4e9f8b10"
COURSE_SETTINGS_REVISION = "7b3e1a9c4d28"
GENERATION_SETTINGS_REVISION = "b2f47c8d0915"
QUIZ_PROGRESS_REVISION = "c8e1f5a9b3d2"
QUIZ_SCHEMA_REVISION = "c8d4a1f39e72"
PGVECTOR_HARDENING_REVISION = "f5a7c2d9e104"
CREDIT_LEDGER_REVISION = "d7f3a2c48e15"
TYPED_CONVERSATIONS_REVISION = "b9c1d4e7f2a6"
LEARNER_CONTEXT_REVISION = "a3d9e5c17b48"
REMOVE_NOTIFICATION_SETTINGS_REVISION = "e7c1d4a8b203"
COURSE_ARCHIVE_STATE_REVISION = "f8b4c2d1e7a3"
PROCESSING_JOB_CORRELATION_ID_REVISION = "3e8b1a4c7f20"
AI_USAGE_COST_REVISION = "c2a6e9f4d817"
RATE_LIMIT_BUCKETS_REVISION = "784a1eb8fba0"
GENERATED_CITATIONS_REVISION = "d1f6b3a8c724"
EXAM_DATE_REVISION = "e2b7c94f1a03"
COURSE_TOPICS_REVISION = "f3c8d05a2b16"
PROGRESS_READ_INDEXES_REVISION = "15bb8ad6d0f1"
DATA_RETENTION_REVISION = "a6e2c8f41b90"
ROADMAP_SCHEMA_PREP_REVISION = "4399b6d253bf"
EXAM_MODE_REVISION = "a6d3f81c9b47"
EXAM_UNLOCKS_REVISION = "b5e9a2c7d341"
QUIZ_SESSIONS_REVISION = "d4a7c19e6b83"
PROFILE_DOCUMENTS_REVISION = "e1a2b3c4d5e6"
EMAIL_VERIFICATION_REVISION = "c7a2e5b91d63"
RATE_LIMIT_REVISION = "b88c7483c27d"
PROFILE_SCHEMA_ALIGNMENT_REVISION = "e74c4d3649f1"
GENERATION_JOBS_REVISION = "ebccfdeadee4"
HEAD_REVISION = GENERATION_JOBS_REVISION


def test_alembic_uses_only_canonical_script_directory() -> None:
    config = Config(str(ALEMBIC_CONFIG))
    scripts = ScriptDirectory.from_config(config)
    script_directories = {
        path.parent.resolve()
        for path in PROJECT_ROOT.rglob("env.py")
        if (path.parent / "script.py.mako").is_file()
        and (path.parent / "versions").is_dir()
    }

    assert Path(scripts.dir).resolve() == ALEMBIC_DIRECTORY.resolve()
    assert script_directories == {ALEMBIC_DIRECTORY.resolve()}
    assert all(
        Path(revision.path)
        .resolve()
        .is_relative_to(ALEMBIC_VERSIONS_DIRECTORY.resolve())
        for revision in scripts.walk_revisions()
    )


def test_postgresql_contract_pins_the_same_head_revision() -> None:
    """The PostgreSQL contract keeps its own head constant and is skipped locally.

    Without this guard a new migration passes every locally runnable test and
    only fails in the PostgreSQL CI job, where the stale constant leaves the
    database one revision behind and `alembic current --check-heads` fails.
    """
    source = (PROJECT_ROOT / "tests" / "test_postgresql.py").read_text(encoding="utf-8")
    match = re.search(r'^HEAD_REVISION = "([0-9a-f]+)"', source, re.MULTILINE)

    assert match is not None, "tests/test_postgresql.py no longer pins HEAD_REVISION"
    assert match.group(1) == HEAD_REVISION


def test_migration_graph_has_one_canonical_base_and_head() -> None:
    config = Config(str(ALEMBIC_CONFIG))
    scripts = ScriptDirectory.from_config(config)
    revisions = {
        revision.revision: revision.down_revision
        for revision in scripts.walk_revisions()
    }

    assert scripts.get_bases() == [BASE_REVISION]
    assert scripts.get_heads() == [HEAD_REVISION]
    assert revisions == {
        GENERATION_JOBS_REVISION: PROFILE_SCHEMA_ALIGNMENT_REVISION,
        PROFILE_SCHEMA_ALIGNMENT_REVISION: RATE_LIMIT_REVISION,
        RATE_LIMIT_REVISION: EMAIL_VERIFICATION_REVISION,
        EMAIL_VERIFICATION_REVISION: PROFILE_DOCUMENTS_REVISION,
        PROFILE_DOCUMENTS_REVISION: QUIZ_SESSIONS_REVISION,
        QUIZ_SESSIONS_REVISION: EXAM_UNLOCKS_REVISION,
        EXAM_UNLOCKS_REVISION: EXAM_MODE_REVISION,
        EXAM_MODE_REVISION: ROADMAP_SCHEMA_PREP_REVISION,
        ROADMAP_SCHEMA_PREP_REVISION: DATA_RETENTION_REVISION,
        DATA_RETENTION_REVISION: PROGRESS_READ_INDEXES_REVISION,
        PROGRESS_READ_INDEXES_REVISION: COURSE_TOPICS_REVISION,
        COURSE_TOPICS_REVISION: EXAM_DATE_REVISION,
        EXAM_DATE_REVISION: GENERATED_CITATIONS_REVISION,
        GENERATED_CITATIONS_REVISION: RATE_LIMIT_BUCKETS_REVISION,
        RATE_LIMIT_BUCKETS_REVISION: AI_USAGE_COST_REVISION,
        AI_USAGE_COST_REVISION: PROCESSING_JOB_CORRELATION_ID_REVISION,
        PROCESSING_JOB_CORRELATION_ID_REVISION: COURSE_ARCHIVE_STATE_REVISION,
        COURSE_ARCHIVE_STATE_REVISION: REMOVE_NOTIFICATION_SETTINGS_REVISION,
        REMOVE_NOTIFICATION_SETTINGS_REVISION: LEARNER_CONTEXT_REVISION,
        LEARNER_CONTEXT_REVISION: TYPED_CONVERSATIONS_REVISION,
        TYPED_CONVERSATIONS_REVISION: CREDIT_LEDGER_REVISION,
        CREDIT_LEDGER_REVISION: PGVECTOR_HARDENING_REVISION,
        PGVECTOR_HARDENING_REVISION: QUIZ_SCHEMA_REVISION,
        QUIZ_SCHEMA_REVISION: QUIZ_PROGRESS_REVISION,
        QUIZ_PROGRESS_REVISION: GENERATION_SETTINGS_REVISION,
        GENERATION_SETTINGS_REVISION: COURSE_SETTINGS_REVISION,
        COURSE_SETTINGS_REVISION: MODEL_CREDITS_REVISION,
        MODEL_CREDITS_REVISION: CONVERSATION_HISTORY_REVISION,
        CONVERSATION_HISTORY_REVISION: CHUNK_EMBEDDINGS_REVISION,
        CHUNK_EMBEDDINGS_REVISION: PROFILE_KNOWLEDGE_REVISION,
        PROFILE_KNOWLEDGE_REVISION: QUIZ_ATTEMPT_ANSWERS_REVISION,
        QUIZ_ATTEMPT_ANSWERS_REVISION: ATTRIBUTION_REVISION,
        ATTRIBUTION_REVISION: HARDENING_REVISION,
        HARDENING_REVISION: SYLLABUS_REVISION,
        SYLLABUS_REVISION: AI_USAGE_REVISION,
        AI_USAGE_REVISION: COURSE_FIELDS_REVISION,
        COURSE_FIELDS_REVISION: CHUNK_RANGES_REVISION,
        CHUNK_RANGES_REVISION: VISUAL_REVISION,
        VISUAL_REVISION: PAGES_REVISION,
        PAGES_REVISION: STAGES_REVISION,
        STAGES_REVISION: PROCESSING_REVISION,
        PROCESSING_REVISION: BASE_REVISION,
        BASE_REVISION: None,
    }


def invoke_alembic(
    database_path: Path,
    temporary_root: Path,
    *arguments: str,
) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment.update(
        {
            "DEPLOYMENT_MODE": "self_hosted",
            "DATABASE_URL": f"sqlite:///{database_path.as_posix()}",
            "STORAGE_BACKEND": "local",
            "UPLOAD_DIRECTORY": str(temporary_root / "uploads"),
            "CHROMA_PERSIST_DIRECTORY": str(temporary_root / "chroma"),
            "PYTHONHASHSEED": "0",
        }
    )
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            str(ALEMBIC_CONFIG),
            *arguments,
        ],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    return completed


def run_alembic(
    database_path: Path,
    temporary_root: Path,
    *arguments: str,
) -> subprocess.CompletedProcess[str]:
    completed = invoke_alembic(database_path, temporary_root, *arguments)
    assert completed.returncode == 0, (
        f"Alembic {' '.join(arguments)} failed\n"
        f"stdout:\n{completed.stdout}\n"
        f"stderr:\n{completed.stderr}"
    )
    return completed


def insert_predecessor_document(
    connection: sqlite3.Connection,
    *,
    storage_key: str,
) -> tuple[str, int]:
    suffix = uuid4().hex
    role_id = connection.execute("SELECT id FROM roles WHERE name = 'user'").fetchone()[
        0
    ]
    user_id = connection.execute(
        "INSERT INTO users "
        "(name, email, password_hash, role_id, is_banned, preferred_model) "
        "VALUES (?, ?, ?, ?, 0, ?)",
        (
            "Hardening migration user",
            f"hardening-{suffix}@example.com",
            "hash",
            role_id,
            "model",
        ),
    ).lastrowid
    course_id = connection.execute(
        "INSERT INTO courses "
        "(title, description, instructor, price, is_deleted, owner_id) "
        "VALUES (?, NULL, ?, 0, 0, ?)",
        (f"Hardening course {suffix}", "Instructor", user_id),
    ).lastrowid
    document_id = uuid4().hex
    connection.execute(
        "INSERT INTO uploaded_documents "
        "(id, original_file_name, file_type, mime_type, file_size, file_hash, "
        "user_id, course_id, storage_provider, storage_key, status) "
        "VALUES (?, ?, 'txt', 'text/plain', 7, ?, ?, ?, 'local:test', ?, 'ready')",
        (
            document_id,
            f"hardening-{suffix}.txt",
            uuid4().hex * 2,
            user_id,
            course_id,
            storage_key,
        ),
    )
    return document_id, course_id


def assert_hardening_not_applied(database_path: Path) -> None:
    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone() == (SYLLABUS_REVISION,)
        document_indexes = {
            row[1]
            for row in connection.execute("PRAGMA index_list(uploaded_documents)")
        }
        assert (
            "uq_uploaded_documents_storage_provider_storage_key" not in document_indexes
        )
        assert (
            connection.execute(
                "SELECT name FROM sqlite_master WHERE name GLOB '_alembic_tmp_*'"
            ).fetchall()
            == []
        )

        chunk_sql = connection.execute(
            "SELECT sql FROM sqlite_master "
            "WHERE type = 'table' AND name = 'document_chunks'"
        ).fetchone()[0]
        job_sql = connection.execute(
            "SELECT sql FROM sqlite_master "
            "WHERE type = 'table' AND name = 'processing_jobs'"
        ).fetchone()[0]
        question_sql = connection.execute(
            "SELECT sql FROM sqlite_master "
            "WHERE type = 'table' AND name = 'quiz_questions'"
        ).fetchone()[0]
        assert "ck_document_chunks_chunk_index_nonnegative" not in chunk_sql
        assert "ck_processing_jobs_job_type_valid" not in job_sql
        assert "ck_quiz_questions_question_index_nonnegative" not in question_sql


def course_columns(connection: sqlite3.Connection) -> set[str]:
    return {row[1] for row in connection.execute("PRAGMA table_info(courses)")}


def database_tables(connection: sqlite3.Connection) -> set[str]:
    return {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
        if not row[0].startswith("sqlite_")
    }


def index_columns(
    connection: sqlite3.Connection, table_name: str
) -> dict[str, list[str]]:
    return {
        row[1]: [
            column[2] for column in connection.execute(f"PRAGMA index_info('{row[1]}')")
        ]
        for row in connection.execute(f"PRAGMA index_list('{table_name}')")
    }


def assert_upgraded_schema(database_path: Path) -> None:
    with sqlite3.connect(database_path) as connection:
        tables = database_tables(connection)
        assert "alembic_version" in tables
        assert "roles" in tables
        assert "users" in tables
        assert "courses" in tables
        assert "uploaded_documents" in tables
        assert "document_chunks" in tables
        assert "document_pages" in tables
        assert "document_visuals" in tables
        assert "processing_jobs" in tables
        assert "ai_usage_logs" in tables
        assert "credit_transactions" in tables
        assert "conversations" in tables
        assert "conversation_messages" in tables

        roles = connection.execute("SELECT name FROM roles ORDER BY name").fetchall()
        assert roles == [("admin",), ("user",)]

        revision = connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone()
        assert revision == (HEAD_REVISION,)

        usage_column_rows = connection.execute(
            "PRAGMA table_info(ai_usage_logs)"
        ).fetchall()
        usage_columns = {row[1] for row in usage_column_rows}
        assert {"estimated_cost_usd", "pricing_version"} <= usage_columns
        assert next(row[2] for row in usage_column_rows if row[1] == "model") == (
            "VARCHAR(128)"
        )
        usage_sql = connection.execute(
            "SELECT sql FROM sqlite_master "
            "WHERE type = 'table' AND name = 'ai_usage_logs'"
        ).fetchone()[0]
        assert "ck_ai_usage_logs_estimated_cost_range" in usage_sql
        assert "ck_ai_usage_logs_pricing_pair" in usage_sql
        usage_indexes = {
            row[1] for row in connection.execute("PRAGMA index_list(ai_usage_logs)")
        }
        assert "ix_ai_usage_logs_success_created" in usage_indexes

        create_sql_row = connection.execute(
            "SELECT sql FROM sqlite_master "
            "WHERE type = 'table' AND name = 'uploaded_documents'"
        ).fetchone()
        assert create_sql_row is not None
        normalized_sql = " ".join(create_sql_row[0].lower().split())
        assert "uq_uploaded_documents_course_id_file_hash" in normalized_sql
        assert "unique (course_id, file_hash)" in normalized_sql
        assert "ck_uploaded_documents_file_hash_length" in normalized_sql
        assert "ck_uploaded_documents_file_size_nonnegative" in normalized_sql
        assert "ck_uploaded_documents_status_valid" in normalized_sql
        document_indexes = {
            row[1]
            for row in connection.execute("PRAGMA index_list(uploaded_documents)")
        }
        assert "uq_uploaded_documents_storage_provider_storage_key" in document_indexes
        assert index_columns(connection, "uploaded_documents")[
            "ix_uploaded_documents_course_status_created"
        ] == ["course_id", "status", "created_at", "id"]

        users_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'users'"
        ).fetchone()
        assert users_sql is not None
        assert "uq_users_is_initial_admin" in users_sql[0].lower()

        chunk_sql = connection.execute(
            "SELECT sql FROM sqlite_master "
            "WHERE type = 'table' AND name = 'document_chunks'"
        ).fetchone()
        assert chunk_sql is not None
        normalized_chunk_sql = " ".join(chunk_sql[0].lower().split())
        assert "ck_document_chunks_page_range_valid" in normalized_chunk_sql
        chunk_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(document_chunks)")
        }
        assert {"page_number", "end_page_number"} <= chunk_columns
        assert "ck_document_chunks_chunk_index_nonnegative" in normalized_chunk_sql
        assert index_columns(connection, "document_chunks")[
            "ix_document_chunks_course_document_index"
        ] == ["course_id", "document_id", "chunk_index", "id"]
        job_sql = connection.execute(
            "SELECT sql FROM sqlite_master "
            "WHERE type = 'table' AND name = 'processing_jobs'"
        ).fetchone()
        assert job_sql is not None
        normalized_job_sql = " ".join(job_sql[0].lower().split())
        assert "uq_processing_jobs_document_type" in normalized_job_sql
        assert "ck_processing_jobs_lease_state_valid" in normalized_job_sql
        assert "ck_processing_jobs_processing_stage_valid" in normalized_job_sql
        assert "ck_processing_jobs_failed_stage_valid" in normalized_job_sql
        assert "ck_processing_jobs_job_type_valid" in normalized_job_sql
        assert "ck_processing_jobs_failed_error_code_nonblank" in normalized_job_sql
        assert "ck_processing_jobs_running_lease_owner_nonblank" in normalized_job_sql
        assert "ck_processing_jobs_running_claim_token_length" in normalized_job_sql
        job_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(processing_jobs)")
        }
        assert {"processing_stage", "failed_stage"} <= job_columns
        page_sql = connection.execute(
            "SELECT sql FROM sqlite_master "
            "WHERE type = 'table' AND name = 'document_pages'"
        ).fetchone()
        assert page_sql is not None
        normalized_page_sql = " ".join(page_sql[0].lower().split())
        assert "uq_document_pages_document_content_index" in normalized_page_sql
        assert "ck_document_pages_raw_extraction_method_valid" in normalized_page_sql
        assert "ck_document_pages_extraction_method_valid" in normalized_page_sql
        assert "ck_document_pages_ocr_candidate_valid" in normalized_page_sql
        assert "ck_document_pages_ocr_status_valid" in normalized_page_sql
        assert "ck_document_pages_visual_analysis_status_valid" in normalized_page_sql
        page_columns = {
            row[1]: row
            for row in connection.execute("PRAGMA table_info(document_pages)")
        }
        assert {
            "raw_text",
            "raw_extraction_method",
            "raw_needs_ocr",
            "ocr_status",
            "has_visual_content",
            "visual_analysis_status",
        } <= set(page_columns)
        assert page_columns["raw_text"][3] == 1

        visual_sql = connection.execute(
            "SELECT sql FROM sqlite_master "
            "WHERE type = 'table' AND name = 'document_visuals'"
        ).fetchone()
        assert visual_sql is not None
        normalized_visual_sql = " ".join(visual_sql[0].lower().split())
        assert "uq_visual_page_index" in normalized_visual_sql
        assert "unique (page_id, visual_index)" in normalized_visual_sql
        assert "ck_document_visuals_visual_index_nonnegative" in normalized_visual_sql
        assert "ck_document_visuals_visual_type_valid" in normalized_visual_sql
        assert "ck_document_visuals_source_valid" in normalized_visual_sql
        assert "ck_document_visuals_bbox_valid" in normalized_visual_sql
        assert "ck_document_visuals_analysis_status_valid" in normalized_visual_sql
        assert "ck_document_visuals_description_status_valid" in normalized_visual_sql
        assert "ck_document_visuals_failed_error_code_required" in normalized_visual_sql
        assert "length(trim(error_code," in normalized_visual_sql
        assert "on delete cascade" in normalized_visual_sql
        visual_indexes = {
            row[1] for row in connection.execute("PRAGMA index_list(document_visuals)")
        }
        assert "ix_document_visuals_page_id" in visual_indexes

        question_sql = connection.execute(
            "SELECT sql FROM sqlite_master "
            "WHERE type = 'table' AND name = 'quiz_questions'"
        ).fetchone()
        assert question_sql is not None
        normalized_question_sql = " ".join(question_sql[0].lower().split())
        assert "ck_quiz_questions_question_index_nonnegative" in normalized_question_sql
        assert (
            "ck_quiz_questions_correct_option_index_nonnegative"
            in normalized_question_sql
        )

        conversation_sql = connection.execute(
            "SELECT sql FROM sqlite_master "
            "WHERE type = 'table' AND name = 'conversations'"
        ).fetchone()
        assert conversation_sql is not None
        normalized_conversation_sql = " ".join(conversation_sql[0].lower().split())
        assert "conversation_type varchar(20) not null" in normalized_conversation_sql
        assert "ck_conversations_conversation_type_valid" in normalized_conversation_sql
        assert "conversation_type in ('course_qa', 'ai_tutor')" in (
            normalized_conversation_sql
        )
        assert index_columns(connection, "conversations")[
            "ix_conversations_user_course_updated"
        ] == ["user_id", "course_id", "updated_at", "id"]

        assert index_columns(connection, "generated_outputs")[
            "ix_generated_outputs_user_course_created"
        ] == ["user_id", "course_id", "created_at", "id"]
        assert index_columns(connection, "generated_outputs")[
            "ix_generated_outputs_user_created"
        ] == ["user_id", "created_at", "id"]
        attempt_indexes = index_columns(connection, "quiz_attempts")
        assert attempt_indexes["ix_quiz_attempts_quiz_user_created"] == [
            "quiz_id",
            "user_id",
            "created_at",
            "id",
        ]
        assert attempt_indexes["ix_quiz_attempts_user_created"] == [
            "user_id",
            "created_at",
            "id",
        ]
        assert attempt_indexes["ix_quiz_attempts_quiz_created"] == [
            "quiz_id",
            "created_at",
            "id",
        ]
        assert "ix_quiz_attempts_user_id" not in attempt_indexes
        assert "ix_quiz_attempts_quiz_id" not in attempt_indexes

        course_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'courses'"
        ).fetchone()
        assert course_sql is not None
        normalized_course_sql = " ".join(course_sql[0].lower().split())
        assert "subject_area varchar(100)" in normalized_course_sql
        assert "education_level varchar(20) default 'unspecified' not null" in (
            normalized_course_sql
        )
        assert "ck_courses_education_level_valid" in normalized_course_sql

        user_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'users'"
        ).fetchone()
        assert user_sql is not None
        normalized_user_sql = " ".join(user_sql[0].lower().split())
        assert "education_level varchar(20) default 'unspecified' not null" in (
            normalized_user_sql
        )
        assert "ck_users_education_level_valid" in normalized_user_sql

        document_sql = connection.execute(
            "SELECT sql FROM sqlite_master "
            "WHERE type = 'table' AND name = 'uploaded_documents'"
        ).fetchone()
        assert document_sql is not None
        normalized_document_sql = " ".join(document_sql[0].lower().split())
        assert "material_kind varchar(20) default 'unspecified' not null" in (
            normalized_document_sql
        )
        assert "ck_uploaded_documents_material_kind_valid" in normalized_document_sql


def test_production_migration_does_not_create_missing_database_parent(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "missing-mount" / "lumina.sqlite3"
    environment = os.environ.copy()
    environment.update(
        {
            "APP_ENV": "production",
            "DEPLOYMENT_MODE": "self_hosted",
            "DATABASE_URL": f"sqlite:///{database_path.as_posix()}",
        }
    )

    completed = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(ALEMBIC_CONFIG), "upgrade", "head"],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )

    assert completed.returncode != 0
    assert "parent directory must already exist" in completed.stderr
    assert not database_path.parent.exists()


def test_fresh_alembic_baseline_upgrades_downgrades_and_reupgrades(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "missing-parent" / "migration.sqlite3"

    run_alembic(database_path, tmp_path, "upgrade", "head")
    assert_upgraded_schema(database_path)

    run_alembic(database_path, tmp_path, "downgrade", "base")
    with sqlite3.connect(database_path) as connection:
        assert database_tables(connection) == {"alembic_version"}
        assert (
            connection.execute("SELECT version_num FROM alembic_version").fetchall()
            == []
        )

    run_alembic(database_path, tmp_path, "upgrade", "head")
    assert_upgraded_schema(database_path)


def test_hardening_preflight_rejects_duplicate_storage_and_remains_retryable(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "duplicate-storage.sqlite3"
    run_alembic(database_path, tmp_path, "upgrade", CHUNK_RANGES_REVISION)

    with sqlite3.connect(database_path) as connection:
        insert_predecessor_document(connection, storage_key="shared/document.txt")
        second_document_id, _ = insert_predecessor_document(
            connection,
            storage_key="shared/document.txt",
        )
        connection.commit()

    completed = invoke_alembic(database_path, tmp_path, "upgrade", HEAD_REVISION)
    assert completed.returncode != 0
    assert "Duplicate document storage locations" in completed.stderr
    assert_hardening_not_applied(database_path)

    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "UPDATE uploaded_documents SET storage_key = ? WHERE id = ?",
            ("shared/document-2.txt", second_document_id),
        )
        connection.commit()

    run_alembic(database_path, tmp_path, "upgrade", HEAD_REVISION)
    assert_upgraded_schema(database_path)


def test_hardening_preflight_rejects_foreign_key_violations(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "foreign-key-violation.sqlite3"
    run_alembic(database_path, tmp_path, "upgrade", CHUNK_RANGES_REVISION)

    with sqlite3.connect(database_path) as connection:
        orphan_id = connection.execute(
            "INSERT INTO document_chunks "
            "(document_id, course_id, chunk_index, page_number, "
            "end_page_number, text) VALUES (?, ?, 0, 1, 1, ?)",
            (uuid4().hex, 999_999, "Orphaned chunk"),
        ).lastrowid
        connection.commit()

    completed = invoke_alembic(database_path, tmp_path, "upgrade", HEAD_REVISION)
    assert completed.returncode != 0
    assert "Foreign key violations require manual correction" in completed.stderr
    assert_hardening_not_applied(database_path)

    with sqlite3.connect(database_path) as connection:
        connection.execute("DELETE FROM document_chunks WHERE id = ?", (orphan_id,))
        connection.commit()

    run_alembic(database_path, tmp_path, "upgrade", HEAD_REVISION)
    assert_upgraded_schema(database_path)


def test_hardening_batch_failure_is_atomic_and_retryable(tmp_path: Path) -> None:
    database_path = tmp_path / "hardening-atomicity.sqlite3"
    run_alembic(database_path, tmp_path, "upgrade", CHUNK_RANGES_REVISION)

    with sqlite3.connect(database_path) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        document_id, course_id = insert_predecessor_document(
            connection,
            storage_key="atomic/document.txt",
        )
        connection.execute("PRAGMA ignore_check_constraints=ON")
        chunk_id = connection.execute(
            "INSERT INTO document_chunks "
            "(document_id, course_id, chunk_index, page_number, "
            "end_page_number, text) VALUES (?, ?, 0, 0, 0, ?)",
            (document_id, course_id, "Invalid legacy page"),
        ).lastrowid
        connection.execute("PRAGMA ignore_check_constraints=OFF")
        quiz_id = connection.execute(
            "INSERT INTO quizzes (course_id, title) VALUES (?, ?)",
            (course_id, "Hardening quiz"),
        ).lastrowid
        question_id = connection.execute(
            "INSERT INTO quiz_questions "
            "(quiz_id, question_index, question_text, options, "
            "correct_option_index) VALUES (?, 0, ?, ?, 0)",
            (quiz_id, "Hardening question?", '["Yes", "No"]'),
        ).lastrowid
        connection.commit()

    completed = invoke_alembic(database_path, tmp_path, "upgrade", HEAD_REVISION)
    assert completed.returncode != 0
    assert "CHECK constraint failed" in completed.stderr
    assert_hardening_not_applied(database_path)

    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT page_number, end_page_number FROM document_chunks WHERE id = ?",
            (chunk_id,),
        ).fetchone() == (0, 0)
        connection.execute(
            "UPDATE document_chunks SET page_number = 1, end_page_number = 1 "
            "WHERE id = ?",
            (chunk_id,),
        )
        connection.commit()

    run_alembic(database_path, tmp_path, "upgrade", HEAD_REVISION)
    assert_upgraded_schema(database_path)

    with sqlite3.connect(database_path) as connection:
        for table, column, row_id in (
            ("document_chunks", "chunk_index", chunk_id),
            ("quiz_questions", "question_index", question_id),
            ("quiz_questions", "correct_option_index", question_id),
        ):
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(
                    f"UPDATE {table} SET {column} = 'not-an-integer' WHERE id = ?",
                    (row_id,),
                )
            connection.rollback()


def test_visual_enrichment_migration_backfills_and_round_trips(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "visual-backfill.sqlite3"
    run_alembic(database_path, tmp_path, "upgrade", PAGES_REVISION)

    document_id = uuid4().hex
    with sqlite3.connect(database_path) as connection:
        role_id = connection.execute(
            "SELECT id FROM roles WHERE name = 'user'"
        ).fetchone()[0]
        user_id = connection.execute(
            "INSERT INTO users "
            "(name, email, password_hash, role_id, is_banned, preferred_model) "
            "VALUES (?, ?, ?, ?, 0, ?)",
            (
                "Visual migration user",
                "visual-migration@example.com",
                "hash",
                role_id,
                "model",
            ),
        ).lastrowid
        course_id = connection.execute(
            "INSERT INTO courses "
            "(title, description, instructor, price, is_deleted, owner_id) "
            "VALUES (?, NULL, ?, 0, 0, ?)",
            ("Visual migration course", "Instructor", user_id),
        ).lastrowid
        connection.execute(
            "INSERT INTO uploaded_documents "
            "(id, original_file_name, file_type, mime_type, file_size, file_hash, "
            "user_id, course_id, storage_provider, storage_key, status) "
            "VALUES (?, ?, 'pdf', 'application/pdf', 7, ?, ?, ?, 'local:test', ?, "
            "'ready')",
            (
                document_id,
                "visual-migration.pdf",
                "f" * 64,
                user_id,
                course_id,
                "visual-migration.pdf",
            ),
        )
        connection.executemany(
            "INSERT INTO document_pages "
            "(document_id, course_id, content_index, page_number, text, "
            "extraction_method, has_images, needs_ocr) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                (
                    document_id,
                    course_id,
                    0,
                    None,
                    "Legacy decoded text",
                    "decoded",
                    0,
                    0,
                ),
                (
                    document_id,
                    course_id,
                    1,
                    2,
                    "Legacy scanned text",
                    "native",
                    1,
                    1,
                ),
            ),
        )
        connection.commit()

    run_alembic(database_path, tmp_path, "upgrade", HEAD_REVISION)
    with sqlite3.connect(database_path) as connection:
        rows = connection.execute(
            "SELECT content_index, raw_text, text, raw_extraction_method, "
            "extraction_method, has_visual_content, ocr_status, "
            "visual_analysis_status FROM document_pages ORDER BY content_index"
        ).fetchall()
        assert rows == [
            (
                0,
                "Legacy decoded text",
                "Legacy decoded text",
                "decoded",
                "decoded",
                0,
                "not_required",
                "not_applicable",
            ),
            (
                1,
                "Legacy scanned text",
                "Legacy scanned text",
                "native",
                "native",
                0,
                "pending",
                "not_applicable",
            ),
        ]

        page_id = connection.execute(
            "SELECT id FROM document_pages WHERE content_index = 1"
        ).fetchone()[0]
        connection.execute(
            "UPDATE document_pages SET text = 'OCR enriched text', "
            "extraction_method = 'ocr', has_images = 0, has_visual_content = 1, "
            "ocr_status = 'succeeded', visual_analysis_status = 'completed' "
            "WHERE id = ?",
            (page_id,),
        )
        connection.execute(
            "INSERT INTO document_visuals "
            "(page_id, visual_index, visual_type, source, bbox_x0, bbox_y0, "
            "bbox_x1, bbox_y1, description, analysis_status) "
            "VALUES (?, 0, 'diagram', 'drawing', 1, 2, 10, 20, ?, 'succeeded')",
            (page_id, "Migrated diagram"),
        )
        connection.commit()

    run_alembic(database_path, tmp_path, "downgrade", PAGES_REVISION)
    with sqlite3.connect(database_path) as connection:
        assert "document_visuals" not in database_tables(connection)
        page_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(document_pages)")
        }
        assert {
            "raw_text",
            "raw_extraction_method",
            "raw_needs_ocr",
            "ocr_status",
            "has_visual_content",
            "visual_analysis_status",
        }.isdisjoint(page_columns)
        downgraded_page = connection.execute(
            "SELECT text, extraction_method, has_images, needs_ocr "
            "FROM document_pages WHERE content_index = 1"
        ).fetchone()
        assert downgraded_page == ("Legacy scanned text", "native", 0, 0)
        revision = connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone()
        assert revision == (PAGES_REVISION,)

    run_alembic(database_path, tmp_path, "upgrade", HEAD_REVISION)
    with sqlite3.connect(database_path) as connection:
        reupgraded_page = connection.execute(
            "SELECT raw_text, text, raw_extraction_method, extraction_method, "
            "has_visual_content, ocr_status, visual_analysis_status "
            "FROM document_pages WHERE content_index = 1"
        ).fetchone()
        assert reupgraded_page == (
            "Legacy scanned text",
            "Legacy scanned text",
            "native",
            "native",
            0,
            "not_required",
            "not_applicable",
        )
        assert connection.execute(
            "SELECT COUNT(*) FROM document_visuals"
        ).fetchone() == (0,)


def test_chunk_page_range_migration_backfills_and_round_trips(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "chunk-page-range.sqlite3"
    run_alembic(database_path, tmp_path, "upgrade", VISUAL_REVISION)

    document_id = uuid4().hex
    with sqlite3.connect(database_path) as connection:
        role_id = connection.execute(
            "SELECT id FROM roles WHERE name = 'user'"
        ).fetchone()[0]
        user_id = connection.execute(
            "INSERT INTO users "
            "(name, email, password_hash, role_id, is_banned, preferred_model) "
            "VALUES (?, ?, ?, ?, 0, ?)",
            ("Chunk user", "chunk-migration@example.com", "hash", role_id, "model"),
        ).lastrowid
        course_id = connection.execute(
            "INSERT INTO courses "
            "(title, description, instructor, price, is_deleted, owner_id) "
            "VALUES (?, NULL, ?, 0, 0, ?)",
            ("Chunk course", "Instructor", user_id),
        ).lastrowid
        connection.execute(
            "INSERT INTO uploaded_documents "
            "(id, original_file_name, file_type, mime_type, file_size, file_hash, "
            "user_id, course_id, storage_provider, storage_key, status) "
            "VALUES (?, ?, 'pdf', 'application/pdf', 7, ?, ?, ?, 'local:test', ?, "
            "'ready')",
            (
                document_id,
                "chunk-migration.pdf",
                "e" * 64,
                user_id,
                course_id,
                "chunk-migration.pdf",
            ),
        )
        connection.executemany(
            "INSERT INTO document_chunks "
            "(document_id, course_id, chunk_index, page_number, text) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                (document_id, course_id, 0, 2, "Paged chunk"),
                (document_id, course_id, 1, None, "Unpaged chunk"),
            ),
        )
        connection.commit()

    run_alembic(database_path, tmp_path, "upgrade", CHUNK_RANGES_REVISION)
    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT page_number, end_page_number FROM document_chunks "
            "ORDER BY chunk_index"
        ).fetchall() == [(2, 2), (None, None)]

    run_alembic(database_path, tmp_path, "upgrade", HEAD_REVISION)
    assert_upgraded_schema(database_path)

    run_alembic(database_path, tmp_path, "downgrade", VISUAL_REVISION)
    with sqlite3.connect(database_path) as connection:
        chunk_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(document_chunks)")
        }
        assert "end_page_number" not in chunk_columns
        assert connection.execute(
            "SELECT page_number FROM document_chunks ORDER BY chunk_index"
        ).fetchall() == [(2,), (None,)]

    run_alembic(database_path, tmp_path, "upgrade", HEAD_REVISION)
    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT page_number, end_page_number FROM document_chunks "
            "ORDER BY chunk_index"
        ).fetchall() == [(2, 2), (None, None)]


def test_processing_migration_backfills_existing_documents(tmp_path: Path) -> None:
    database_path = tmp_path / "backfill.sqlite3"
    run_alembic(database_path, tmp_path, "upgrade", BASE_REVISION)

    document_ids = [uuid4().hex for _ in range(6)]
    with sqlite3.connect(database_path) as connection:
        role_id = connection.execute(
            "SELECT id FROM roles WHERE name = 'admin'"
        ).fetchone()[0]
        user_id = connection.execute(
            "INSERT INTO users "
            "(name, email, password_hash, role_id, is_banned, preferred_model) "
            "VALUES (?, ?, ?, ?, 0, ?)",
            ("Migration user", "migration@example.com", "hash", role_id, "model"),
        ).lastrowid
        course_id = connection.execute(
            "INSERT INTO courses "
            "(title, description, instructor, price, is_deleted, owner_id) "
            "VALUES (?, NULL, ?, 0, 0, ?)",
            ("Migration course", "Instructor", user_id),
        ).lastrowid
        deleted_course_id = connection.execute(
            "INSERT INTO courses "
            "(title, description, instructor, price, is_deleted, owner_id) "
            "VALUES (?, NULL, ?, 0, 1, ?)",
            ("Deleted course", "Instructor", user_id),
        ).lastrowid
        for index, (document_id, state) in enumerate(
            zip(
                document_ids,
                (
                    "pending",
                    "processing",
                    "completed",
                    "completed",
                    "failed",
                    "pending",
                ),
                strict=True,
            )
        ):
            connection.execute(
                "INSERT INTO uploaded_documents "
                "(id, original_file_name, file_type, mime_type, file_size, "
                "file_hash, user_id, course_id, storage_provider, storage_key, "
                "status, processing_error) "
                "VALUES (?, ?, 'txt', 'text/plain', 5, ?, ?, ?, 'local:test', ?, ?, ?)",
                (
                    document_id,
                    f"document-{index}.txt",
                    f"{index:064x}",
                    user_id,
                    deleted_course_id if index == 5 else course_id,
                    f"document-{index}",
                    state,
                    "legacy detail" if state == "failed" else None,
                ),
            )
        connection.execute(
            "INSERT INTO document_chunks "
            "(document_id, course_id, chunk_index, text) VALUES (?, ?, 0, ?)",
            (document_ids[3], course_id, "Existing canonical chunk"),
        )
        connection.commit()

    run_alembic(database_path, tmp_path, "upgrade", "head")
    with sqlite3.connect(database_path) as connection:
        rows = connection.execute(
            "SELECT d.status, j.status, j.attempt_count, j.max_attempts, "
            "j.last_error_code "
            "FROM uploaded_documents AS d "
            "JOIN processing_jobs AS j ON j.document_id = d.id "
            "ORDER BY d.storage_key"
        ).fetchall()
        assert rows == [
            ("uploaded", "queued", 0, 3, None),
            ("uploaded", "queued", 0, 3, None),
            ("uploaded", "queued", 0, 3, None),
            ("ready", "succeeded", 1, 3, None),
            ("failed", "failed", 3, 3, "LEGACY_PROCESSING_FAILED"),
            ("failed", "failed", 3, 3, "COURSE_DELETED"),
        ]
        assert connection.execute("SELECT COUNT(*) FROM document_pages").fetchone() == (
            0,
        )

        connection.execute(
            "UPDATE processing_jobs SET status = 'running', attempt_count = 1, "
            "claimed_at = CURRENT_TIMESTAMP, heartbeat_at = CURRENT_TIMESTAMP, "
            "lease_expires_at = datetime('now', '+1 hour'), "
            "lease_owner = 'migration-test', "
            "claim_token = '00000000-0000-0000-0000-000000000000' "
            "WHERE document_id = ?",
            (document_ids[0],),
        )
        connection.execute(
            "UPDATE uploaded_documents SET status = 'processing' WHERE id = ?",
            (document_ids[0],),
        )
        connection.commit()

    run_alembic(database_path, tmp_path, "downgrade", BASE_REVISION)
    with sqlite3.connect(database_path) as connection:
        assert "processing_jobs" not in database_tables(connection)
        chunk_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(document_chunks)")
        }
        assert "page_number" not in chunk_columns
        revision = connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone()
        assert revision == (BASE_REVISION,)
        document_status = connection.execute(
            "SELECT status FROM uploaded_documents WHERE id = ?",
            (document_ids[0],),
        ).fetchone()
        assert document_status == ("pending",)


def test_course_workspace_migration_backfills_and_round_trips(
    tmp_path: Path,
) -> None:
    """Upgrade, downgrade and re-upgrade must all behave, and preserve syllabus text.

    The frontend stored syllabus prose in ``description`` before this revision, so
    the upgrade backfills it. Downgrade drops only the new columns, which leaves
    ``description`` intact for the re-upgrade to derive from again.
    """
    database_path = tmp_path / "course-workspace.sqlite3"
    run_alembic(database_path, tmp_path, "upgrade", AI_USAGE_REVISION)

    created_at = "2026-01-02 03:04:05"
    with sqlite3.connect(database_path) as connection:
        columns = course_columns(connection)
        assert "syllabus" not in columns
        assert "updated_at" not in columns

        role_id = connection.execute(
            "SELECT id FROM roles WHERE name = 'user'"
        ).fetchone()[0]
        user_id = connection.execute(
            "INSERT INTO users "
            "(name, email, password_hash, role_id, is_banned, preferred_model) "
            "VALUES (?, ?, ?, ?, 0, ?)",
            (
                "Course workspace user",
                "course-workspace@example.com",
                "hash",
                role_id,
                "model",
            ),
        ).lastrowid
        described_id = connection.execute(
            "INSERT INTO courses "
            "(title, description, is_deleted, owner_id, created_at) "
            "VALUES (?, ?, 0, ?, ?)",
            ("Described course", "Week 1: Fundamentals", user_id, created_at),
        ).lastrowid
        bare_id = connection.execute(
            "INSERT INTO courses "
            "(title, description, is_deleted, owner_id, created_at) "
            "VALUES (?, NULL, 0, ?, ?)",
            ("Bare course", user_id, created_at),
        ).lastrowid

    run_alembic(database_path, tmp_path, "upgrade", "head")

    with sqlite3.connect(database_path) as connection:
        columns = course_columns(connection)
        assert "syllabus" in columns
        assert "updated_at" in columns
        assert connection.execute(
            "SELECT syllabus, updated_at FROM courses WHERE id = ?",
            (described_id,),
        ).fetchone() == ("Week 1: Fundamentals", created_at)
        assert connection.execute(
            "SELECT syllabus, updated_at FROM courses WHERE id = ?",
            (bare_id,),
        ).fetchone() == (None, created_at)
        assert connection.execute(
            "SELECT title, owner_id FROM courses WHERE id = ?", (described_id,)
        ).fetchone() == ("Described course", user_id)

    run_alembic(database_path, tmp_path, "downgrade", AI_USAGE_REVISION)

    with sqlite3.connect(database_path) as connection:
        columns = course_columns(connection)
        assert "syllabus" not in columns
        assert "updated_at" not in columns
        assert connection.execute(
            "SELECT description FROM courses WHERE id = ?", (described_id,)
        ).fetchone() == ("Week 1: Fundamentals",)

    run_alembic(database_path, tmp_path, "upgrade", "head")

    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT syllabus FROM courses WHERE id = ?", (described_id,)
        ).fetchone() == ("Week 1: Fundamentals",)
        revision = connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone()
        assert revision == (HEAD_REVISION,)


def generated_output_columns(connection: sqlite3.Connection) -> set[str]:
    return {
        row[1] for row in connection.execute("PRAGMA table_info(generated_outputs)")
    }


def test_generated_output_attribution_migration_round_trips(tmp_path: Path) -> None:
    """Legacy rows keep unknown attribution; nothing is invented by the upgrade."""
    database_path = tmp_path / "generated-output-attribution.sqlite3"
    run_alembic(database_path, tmp_path, "upgrade", HARDENING_REVISION)

    with sqlite3.connect(database_path) as connection:
        columns = generated_output_columns(connection)
        assert "user_id" not in columns
        assert "model_used" not in columns

        role_id = connection.execute(
            "SELECT id FROM roles WHERE name = 'user'"
        ).fetchone()[0]
        user_id = connection.execute(
            "INSERT INTO users "
            "(name, email, password_hash, role_id, is_banned, preferred_model) "
            "VALUES (?, ?, ?, ?, 0, ?)",
            ("Attribution user", "attribution@example.com", "hash", role_id, "model"),
        ).lastrowid
        course_id = connection.execute(
            "INSERT INTO courses "
            "(title, description, is_deleted, owner_id, created_at) "
            "VALUES (?, NULL, 0, ?, ?)",
            ("Attribution course", user_id, "2026-01-02 03:04:05"),
        ).lastrowid
        legacy_id = connection.execute(
            "INSERT INTO generated_outputs "
            "(course_id, output_type, content, created_at) VALUES (?, ?, ?, ?)",
            (course_id, "study_guide", '{"title": "Legacy"}', "2026-01-02 03:04:05"),
        ).lastrowid

    run_alembic(database_path, tmp_path, "upgrade", "head")

    with sqlite3.connect(database_path) as connection:
        columns = generated_output_columns(connection)
        assert "user_id" in columns
        assert "model_used" in columns

        assert connection.execute(
            "SELECT user_id, model_used, content FROM generated_outputs WHERE id = ?",
            (legacy_id,),
        ).fetchone() == (None, None, '{"title": "Legacy"}')

        indexes = {
            row[1] for row in connection.execute("PRAGMA index_list(generated_outputs)")
        }
        assert "ix_generated_outputs_user_id" in indexes

        references = {
            (row[2], row[3], row[6])
            for row in connection.execute("PRAGMA foreign_key_list(generated_outputs)")
        }
        assert ("users", "user_id", "SET NULL") in references

        connection.execute(
            "UPDATE generated_outputs SET user_id = ?, model_used = ? WHERE id = ?",
            (user_id, "ollama:qwen3:8b", legacy_id),
        )

    run_alembic(database_path, tmp_path, "downgrade", HARDENING_REVISION)

    with sqlite3.connect(database_path) as connection:
        columns = generated_output_columns(connection)
        assert "user_id" not in columns
        assert "model_used" not in columns
        assert connection.execute(
            "SELECT content FROM generated_outputs WHERE id = ?", (legacy_id,)
        ).fetchone() == ('{"title": "Legacy"}',)

    run_alembic(database_path, tmp_path, "upgrade", "head")

    with sqlite3.connect(database_path) as connection:
        columns = generated_output_columns(connection)
        assert "user_id" in columns
        assert "model_used" in columns
        assert connection.execute(
            "SELECT user_id, model_used FROM generated_outputs WHERE id = ?",
            (legacy_id,),
        ).fetchone() == (None, None)
        assert connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone() == (HEAD_REVISION,)


def test_generated_output_settings_migration_round_trips(tmp_path: Path) -> None:
    """Legacy rows keep unknown generation settings; nothing is invented by the upgrade."""
    database_path = tmp_path / "generated-output-settings.sqlite3"
    run_alembic(database_path, tmp_path, "upgrade", CONVERSATION_HISTORY_REVISION)

    with sqlite3.connect(database_path) as connection:
        columns = generated_output_columns(connection)
        assert "generation_settings" not in columns
        assert "generation_context" not in columns

        role_id = connection.execute(
            "SELECT id FROM roles WHERE name = 'user'"
        ).fetchone()[0]
        user_id = connection.execute(
            "INSERT INTO users "
            "(name, email, password_hash, role_id, is_banned, preferred_model) "
            "VALUES (?, ?, ?, ?, 0, ?)",
            ("Settings user", "settings@example.com", "hash", role_id, "model"),
        ).lastrowid
        course_id = connection.execute(
            "INSERT INTO courses "
            "(title, description, is_deleted, owner_id, created_at) "
            "VALUES (?, NULL, 0, ?, ?)",
            ("Settings course", user_id, "2026-01-02 03:04:05"),
        ).lastrowid
        legacy_id = connection.execute(
            "INSERT INTO generated_outputs "
            "(course_id, user_id, model_used, output_type, content, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                course_id,
                user_id,
                "ollama:qwen3:8b",
                "study_guide",
                '{"title": "Legacy"}',
                "2026-01-02 03:04:05",
            ),
        ).lastrowid

    run_alembic(database_path, tmp_path, "upgrade", "head")

    with sqlite3.connect(database_path) as connection:
        columns = generated_output_columns(connection)
        assert "generation_settings" in columns
        assert "generation_context" in columns

        assert connection.execute(
            "SELECT generation_settings, generation_context, user_id, model_used "
            "FROM generated_outputs WHERE id = ?",
            (legacy_id,),
        ).fetchone() == (None, None, user_id, "ollama:qwen3:8b")

        # The SQLite table is rebuilt to add the columns, so the indexes and
        # foreign keys must survive that rebuild.
        indexes = {
            row[1] for row in connection.execute("PRAGMA index_list(generated_outputs)")
        }
        assert "ix_generated_outputs_course_id" in indexes
        assert "ix_generated_outputs_user_id" in indexes

        references = {
            (row[2], row[3], row[6])
            for row in connection.execute("PRAGMA foreign_key_list(generated_outputs)")
        }
        assert ("users", "user_id", "SET NULL") in references
        assert ("courses", "course_id", "CASCADE") in references

        connection.execute(
            "UPDATE generated_outputs "
            "SET generation_settings = ?, generation_context = ? WHERE id = ?",
            ('{"version": 1}', '{"version": 1}', legacy_id),
        )

    run_alembic(database_path, tmp_path, "downgrade", CONVERSATION_HISTORY_REVISION)

    with sqlite3.connect(database_path) as connection:
        columns = generated_output_columns(connection)
        assert "generation_settings" not in columns
        assert "generation_context" not in columns
        assert connection.execute(
            "SELECT content, user_id, model_used FROM generated_outputs WHERE id = ?",
            (legacy_id,),
        ).fetchone() == ('{"title": "Legacy"}', user_id, "ollama:qwen3:8b")

        references = {
            (row[2], row[3], row[6])
            for row in connection.execute("PRAGMA foreign_key_list(generated_outputs)")
        }
        assert ("users", "user_id", "SET NULL") in references

    run_alembic(database_path, tmp_path, "upgrade", "head")

    with sqlite3.connect(database_path) as connection:
        columns = generated_output_columns(connection)
        assert "generation_settings" in columns
        assert "generation_context" in columns
        assert connection.execute(
            "SELECT generation_settings, generation_context FROM generated_outputs "
            "WHERE id = ?",
            (legacy_id,),
        ).fetchone() == (None, None)
        assert connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone() == (HEAD_REVISION,)


def quiz_question_columns(connection: sqlite3.Connection) -> set[str]:
    return {row[1] for row in connection.execute("PRAGMA table_info(quiz_questions)")}


def quiz_columns(connection: sqlite3.Connection) -> set[str]:
    return {row[1] for row in connection.execute("PRAGMA table_info(quizzes)")}


def not_null_flags(connection: sqlite3.Connection, table: str) -> dict[str, int]:
    return {row[1]: row[3] for row in connection.execute(f"PRAGMA table_info({table})")}


def test_quiz_schema_migration_backfills_and_round_trips(tmp_path: Path) -> None:
    """Legacy multiple-choice questions gain the answer document their index encodes."""
    database_path = tmp_path / "quiz-schema.sqlite3"
    run_alembic(database_path, tmp_path, "upgrade", GENERATION_SETTINGS_REVISION)

    with sqlite3.connect(database_path) as connection:
        assert "question_type" not in quiz_question_columns(connection)
        assert "user_id" not in quiz_columns(connection)

        role_id = connection.execute(
            "SELECT id FROM roles WHERE name = 'user'"
        ).fetchone()[0]
        user_id = connection.execute(
            "INSERT INTO users "
            "(name, email, password_hash, role_id, is_banned, preferred_model) "
            "VALUES (?, ?, ?, ?, 0, ?)",
            ("Quiz user", "quiz-schema@example.com", "hash", role_id, "model"),
        ).lastrowid
        course_id = connection.execute(
            "INSERT INTO courses "
            "(title, description, is_deleted, owner_id, created_at) "
            "VALUES (?, NULL, 0, ?, ?)",
            ("Quiz course", user_id, "2026-01-02 03:04:05"),
        ).lastrowid
        quiz_id = connection.execute(
            "INSERT INTO quizzes (course_id, title, created_at) VALUES (?, ?, ?)",
            (course_id, "Legacy Quiz", "2026-01-02 03:04:05"),
        ).lastrowid
        question_id = connection.execute(
            "INSERT INTO quiz_questions "
            "(quiz_id, question_index, question_text, options, correct_option_index, "
            "topic, explanation) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                quiz_id,
                0,
                "Which complexity is binary search?",
                '["O(n)", "O(log n)", "O(n^2)", "O(1)"]',
                1,
                "Searching",
                "It halves the range.",
            ),
        ).lastrowid
        attempt_id = connection.execute(
            "INSERT INTO quiz_attempts (user_id, quiz_id, score, created_at) "
            "VALUES (?, ?, ?, ?)",
            (user_id, quiz_id, 1.0, "2026-01-02 03:04:05"),
        ).lastrowid
        answer_id = connection.execute(
            "INSERT INTO quiz_attempt_answers "
            "(attempt_id, quiz_question_id, selected_option_index, is_correct) "
            "VALUES (?, ?, ?, ?)",
            (attempt_id, question_id, 1, 1),
        ).lastrowid

    run_alembic(database_path, tmp_path, "upgrade", "head")

    with sqlite3.connect(database_path) as connection:
        assert {
            "question_type",
            "difficulty",
            "correct_answer",
        } <= quiz_question_columns(connection)
        assert {
            "user_id",
            "model_used",
            "generation_settings",
            "generation_context",
        } <= quiz_columns(connection)
        assert {"score", "feedback"} <= {
            row[1]
            for row in connection.execute("PRAGMA table_info(quiz_attempt_answers)")
        }

        assert connection.execute(
            "SELECT question_type, difficulty, correct_answer, correct_option_index "
            "FROM quiz_questions WHERE id = ?",
            (question_id,),
        ).fetchone() == (
            "multiple_choice",
            None,
            '{"type": "multiple_choice", "option_index": 1}',
            1,
        )

        # The relaxations are what make the other three question types storable.
        flags = not_null_flags(connection, "quiz_questions")
        assert flags["options"] == 0
        assert flags["correct_option_index"] == 0
        assert not_null_flags(connection, "quiz_attempt_answers")["is_correct"] == 0

        connection.execute(
            "INSERT INTO quiz_questions "
            "(quiz_id, question_index, question_text, question_type, difficulty, "
            "correct_answer) VALUES (?, ?, ?, ?, ?, ?)",
            (
                quiz_id,
                1,
                "Explain why sorting is required.",
                "open_ended",
                "medium",
                '{"type": "open_ended", "reference_answer": "Ordering."}',
            ),
        )

        # The SQLite tables are rebuilt, so indexes and foreign keys must survive.
        indexes = {row[1] for row in connection.execute("PRAGMA index_list(quizzes)")}
        assert {"ix_quizzes_course_id", "ix_quizzes_user_id"} <= indexes

        references = {
            (row[2], row[3], row[6])
            for row in connection.execute("PRAGMA foreign_key_list(quizzes)")
        }
        assert ("users", "user_id", "SET NULL") in references
        assert ("courses", "course_id", "CASCADE") in references

        assert connection.execute(
            "SELECT selected_option_index, is_correct FROM quiz_attempt_answers "
            "WHERE id = ?",
            (answer_id,),
        ).fetchone() == (1, 1)

    run_alembic(database_path, tmp_path, "downgrade", GENERATION_SETTINGS_REVISION)

    with sqlite3.connect(database_path) as connection:
        columns = quiz_question_columns(connection)
        assert "question_type" not in columns
        assert "difficulty" not in columns
        assert "correct_answer" not in columns
        assert "user_id" not in quiz_columns(connection)

        assert connection.execute(
            "SELECT options, correct_option_index FROM quiz_questions WHERE id = ?",
            (question_id,),
        ).fetchone() == ('["O(n)", "O(log n)", "O(n^2)", "O(1)"]', 1)

        # The open-ended question has no option list, so the downgrade must give
        # it one for the restored NOT NULL constraint to hold.
        assert connection.execute(
            "SELECT options, correct_option_index FROM quiz_questions "
            "WHERE quiz_id = ? AND question_index = 1",
            (quiz_id,),
        ).fetchone() == ("[]", 0)

    run_alembic(database_path, tmp_path, "upgrade", "head")

    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT question_type, correct_answer FROM quiz_questions WHERE id = ?",
            (question_id,),
        ).fetchone() == (
            "multiple_choice",
            '{"type": "multiple_choice", "option_index": 1}',
        )
        assert connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone() == (HEAD_REVISION,)


def profile_knowledge_columns(connection: sqlite3.Connection) -> set[str]:
    return {
        row[1] for row in connection.execute("PRAGMA table_info(profile_knowledge)")
    }


def test_profile_knowledge_migration_round_trips(tmp_path: Path) -> None:
    """profile_knowledge table adds updated_at backfilled from created_at and round-trips."""
    database_path = tmp_path / "profile-knowledge.sqlite3"
    run_alembic(database_path, tmp_path, "upgrade", QUIZ_ATTEMPT_ANSWERS_REVISION)

    with sqlite3.connect(database_path) as connection:
        columns = profile_knowledge_columns(connection)
        assert "updated_at" not in columns

        role_id = connection.execute(
            "SELECT id FROM roles WHERE name = 'user'"
        ).fetchone()[0]
        user_id = connection.execute(
            "INSERT INTO users "
            "(name, email, password_hash, role_id, is_banned, preferred_model) "
            "VALUES (?, ?, ?, ?, 0, ?)",
            ("PK user", "pk-migrate@example.com", "hash", role_id, "model"),
        ).lastrowid
        pk_id = connection.execute(
            "INSERT INTO profile_knowledge "
            "(user_id, topic, detail, created_at) "
            "VALUES (?, ?, ?, ?)",
            (user_id, "Calculus", "Knows basic derivatives.", "2026-02-01 10:00:00"),
        ).lastrowid

    run_alembic(database_path, tmp_path, "upgrade", "head")

    with sqlite3.connect(database_path) as connection:
        columns = profile_knowledge_columns(connection)
        assert "updated_at" in columns
        row = connection.execute(
            "SELECT topic, detail, created_at, updated_at FROM profile_knowledge WHERE id = ?",
            (pk_id,),
        ).fetchone()
        assert row[0] == "Calculus"
        assert row[1] == "Knows basic derivatives."
        assert row[2] == "2026-02-01 10:00:00"
        assert row[3] == "2026-02-01 10:00:00"

    run_alembic(database_path, tmp_path, "downgrade", QUIZ_ATTEMPT_ANSWERS_REVISION)

    with sqlite3.connect(database_path) as connection:
        columns = profile_knowledge_columns(connection)
        assert "updated_at" not in columns
        row = connection.execute(
            "SELECT topic, detail FROM profile_knowledge WHERE id = ?",
            (pk_id,),
        ).fetchone()
        assert row == ("Calculus", "Knows basic derivatives.")

    run_alembic(database_path, tmp_path, "upgrade", "head")

    with sqlite3.connect(database_path) as connection:
        columns = profile_knowledge_columns(connection)
        assert "updated_at" in columns
        assert connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone() == (HEAD_REVISION,)


def quiz_questions_columns(connection: sqlite3.Connection) -> set[str]:
    return {row[1] for row in connection.execute("PRAGMA table_info(quiz_questions)")}


def quiz_attempt_answers_columns(connection: sqlite3.Connection) -> set[str]:
    return {
        row[1] for row in connection.execute("PRAGMA table_info(quiz_attempt_answers)")
    }


def progress_columns(connection: sqlite3.Connection) -> set[str]:
    return {row[1] for row in connection.execute("PRAGMA table_info(progress)")}


def test_quiz_progress_migration_round_trips(tmp_path: Path) -> None:
    """Quiz questions, attempt answers, and progress tables expand and round-trip."""
    database_path = tmp_path / "quiz-progress.sqlite3"
    run_alembic(database_path, tmp_path, "upgrade", GENERATION_SETTINGS_REVISION)

    with sqlite3.connect(database_path) as connection:
        q_cols = quiz_questions_columns(connection)
        ans_cols = quiz_attempt_answers_columns(connection)
        prog_cols = progress_columns(connection)

        assert "question_type" not in q_cols
        assert "text_response" not in ans_cols
        assert "time_spent_seconds" not in ans_cols
        assert "topic" not in ans_cols
        assert "quizzes_completed" not in prog_cols
        assert "correct_answers_count" not in prog_cols
        assert "incorrect_answers_count" not in prog_cols
        assert "total_questions_answered" not in prog_cols
        assert "weak_topics" not in prog_cols
        assert "quiz_history" not in prog_cols

    run_alembic(database_path, tmp_path, "upgrade", "head")

    with sqlite3.connect(database_path) as connection:
        q_cols = quiz_questions_columns(connection)
        ans_cols = quiz_attempt_answers_columns(connection)
        prog_cols = progress_columns(connection)

        assert "question_type" in q_cols
        assert "text_response" in ans_cols
        assert "time_spent_seconds" in ans_cols
        assert "topic" in ans_cols
        assert "quizzes_completed" in prog_cols
        assert "correct_answers_count" in prog_cols
        assert "incorrect_answers_count" in prog_cols
        assert "total_questions_answered" in prog_cols
        assert "weak_topics" in prog_cols
        assert "quiz_history" in prog_cols

        assert connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone() == (HEAD_REVISION,)

    run_alembic(database_path, tmp_path, "downgrade", GENERATION_SETTINGS_REVISION)

    with sqlite3.connect(database_path) as connection:
        q_cols = quiz_questions_columns(connection)
        ans_cols = quiz_attempt_answers_columns(connection)
        prog_cols = progress_columns(connection)

        assert "question_type" not in q_cols
        assert "text_response" not in ans_cols
        assert "quizzes_completed" not in prog_cols

    run_alembic(database_path, tmp_path, "upgrade", "head")

    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone() == (HEAD_REVISION,)


def insert_legacy_user(
    connection: sqlite3.Connection,
    *,
    email: str,
    credits: float | None,
    role: str = "user",
) -> int:
    role_id = connection.execute(
        "SELECT id FROM roles WHERE name = ?", (role,)
    ).fetchone()[0]
    cursor = connection.execute(
        "INSERT INTO users "
        "(name, email, password_hash, role_id, credits, is_banned, preferred_model) "
        "VALUES (?, ?, 'not-a-real-hash', ?, ?, 0, 'gemini:gemini-3.6-flash')",
        (email.split("@")[0], email, role_id, credits),
    )
    return int(cursor.lastrowid)


def test_credit_ledger_migration_reconciles_existing_balances(tmp_path: Path) -> None:
    """Every legacy balance gains one truthful baseline row and nothing more.

    The reconciliation deliberately invents no history: a balance of 37 becomes
    a single +37 row, not a fabricated grant-and-spend sequence it cannot know
    happened. An unmetered administrator stays outside the ledger entirely.
    """
    database_path = tmp_path / "credit-ledger.sqlite3"
    run_alembic(database_path, tmp_path, "upgrade", QUIZ_SCHEMA_REVISION)

    with sqlite3.connect(database_path) as connection:
        assert "credit_transactions" not in database_tables(connection)
        insert_legacy_user(connection, email="alice@example.com", credits=37.0)
        insert_legacy_user(connection, email="bob@example.com", credits=0.0)
        insert_legacy_user(connection, email="carol@example.com", credits=83.0)
        insert_legacy_user(
            connection, email="root@example.com", credits=None, role="admin"
        )

    run_alembic(database_path, tmp_path, "upgrade", "head")

    with sqlite3.connect(database_path) as connection:
        assert "credit_transactions" in database_tables(connection)

        reconciled = connection.execute(
            "SELECT u.email, t.delta, t.balance_after, t.reason, t.actor_type, "
            "t.grant_period "
            "FROM credit_transactions t JOIN users u ON u.id = t.user_id "
            "ORDER BY u.email"
        ).fetchall()
        assert reconciled == [
            (
                "alice@example.com",
                37.0,
                37.0,
                "migration_reconciliation",
                "migration",
                None,
            ),
            (
                "bob@example.com",
                0.0,
                0.0,
                "migration_reconciliation",
                "migration",
                None,
            ),
            (
                "carol@example.com",
                83.0,
                83.0,
                "migration_reconciliation",
                "migration",
                None,
            ),
        ]

        # A metered balance is now derivable; an unmetered one owns no rows.
        drift = connection.execute(
            "SELECT u.email, u.credits, "
            "COALESCE((SELECT SUM(t.delta) FROM credit_transactions t "
            "WHERE t.user_id = u.id), 0) "
            "FROM users u"
        ).fetchall()
        for email, credits, ledger_total in drift:
            if credits is None:
                assert ledger_total == 0, email
            else:
                assert credits == ledger_total, email

    run_alembic(database_path, tmp_path, "downgrade", QUIZ_SCHEMA_REVISION)

    with sqlite3.connect(database_path) as connection:
        assert "credit_transactions" not in database_tables(connection)
        # The ledger is lossy to drop, but no balance is disturbed by dropping it.
        assert connection.execute(
            "SELECT email, credits FROM users ORDER BY email"
        ).fetchall() == [
            ("alice@example.com", 37.0),
            ("bob@example.com", 0.0),
            ("carol@example.com", 83.0),
            ("root@example.com", None),
        ]

    run_alembic(database_path, tmp_path, "upgrade", "head")

    with sqlite3.connect(database_path) as connection:
        # Re-upgrading rebuilds exactly one baseline per metered user.
        assert connection.execute(
            "SELECT COUNT(*) FROM credit_transactions"
        ).fetchone() == (3,)


def test_prompt_context_migration_defaults_legacy_rows_and_enforces_vocabularies(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "prompt-context.sqlite3"
    run_alembic(database_path, tmp_path, "upgrade", TYPED_CONVERSATIONS_REVISION)

    with sqlite3.connect(database_path) as connection:
        course_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(courses)")
        }
        assert "education_level" not in course_columns
        assert "subject_area" not in course_columns

        user_id = insert_legacy_user(
            connection,
            email="legacy-learner@example.com",
            credits=10.0,
        )
        course_id = connection.execute(
            "INSERT INTO courses (title, is_deleted, owner_id) VALUES (?, 0, ?)",
            ("Legacy course without a learner level", user_id),
        ).lastrowid
        document_id = "0f9c1a2b3d4e4f5a6b7c8d9e0f1a2b3c"
        connection.execute(
            "INSERT INTO uploaded_documents ("
            "id, original_file_name, file_type, mime_type, file_size, file_hash, "
            "user_id, course_id, storage_provider, storage_key, status"
            ") VALUES (?, ?, 'txt', 'text/plain', 11, ?, ?, ?, 'local', ?, 'ready')",
            (
                document_id,
                "legacy.txt",
                "a" * 64,
                user_id,
                course_id,
                f"courses/{course_id}/docs/legacy.txt",
            ),
        )

    run_alembic(database_path, tmp_path, "upgrade", HEAD_REVISION)

    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT education_level, subject_area FROM courses WHERE id = ?",
            (course_id,),
        ).fetchone() == ("unspecified", None)
        assert connection.execute(
            "SELECT education_level FROM users WHERE id = ?",
            (user_id,),
        ).fetchone() == ("unspecified",)
        assert connection.execute(
            "SELECT material_kind FROM uploaded_documents WHERE id = ?",
            (document_id,),
        ).fetchone() == ("unspecified",)

        connection.execute(
            "UPDATE courses SET education_level = 'high_school' WHERE id = ?",
            (course_id,),
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE courses SET education_level = 'university' WHERE id = ?",
                (course_id,),
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE users SET education_level = 'college' WHERE id = ?",
                (user_id,),
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE uploaded_documents SET material_kind = 'mixed' WHERE id = ?",
                (document_id,),
            )

    run_alembic(database_path, tmp_path, "downgrade", TYPED_CONVERSATIONS_REVISION)

    with sqlite3.connect(database_path) as connection:
        course_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(courses)")
        }
        assert "education_level" not in course_columns
        assert "subject_area" not in course_columns
        document_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(uploaded_documents)")
        }
        assert "material_kind" not in document_columns
        assert connection.execute(
            "SELECT COUNT(*) FROM uploaded_documents WHERE id = ?",
            (document_id,),
        ).fetchone() == (1,)

    run_alembic(database_path, tmp_path, "upgrade", HEAD_REVISION)

    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT education_level FROM courses WHERE id = ?",
            (course_id,),
        ).fetchone() == ("unspecified",)


def test_typed_conversation_migration_backfills_and_enforces_types(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "typed-conversations.sqlite3"
    run_alembic(database_path, tmp_path, "upgrade", CREDIT_LEDGER_REVISION)

    with sqlite3.connect(database_path) as connection:
        conversation_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(conversations)")
        }
        assert "conversation_type" not in conversation_columns

        user_id = insert_legacy_user(
            connection,
            email="legacy-conversation@example.com",
            credits=10.0,
        )
        course_id = connection.execute(
            "INSERT INTO courses (title, is_deleted, owner_id) VALUES (?, 0, ?)",
            ("Legacy conversation course", user_id),
        ).lastrowid
        conversation_id = connection.execute(
            "INSERT INTO conversations (user_id, course_id) VALUES (?, ?)",
            (user_id, course_id),
        ).lastrowid
        message_id = connection.execute(
            "INSERT INTO conversation_messages (conversation_id, role, content) "
            "VALUES (?, 'user', ?)",
            (conversation_id, "A legacy Course Q&A question"),
        ).lastrowid

    run_alembic(database_path, tmp_path, "upgrade", HEAD_REVISION)

    with sqlite3.connect(database_path) as connection:
        columns = {
            row[1]: row
            for row in connection.execute("PRAGMA table_info(conversations)")
        }
        assert columns["conversation_type"][3] == 1
        assert connection.execute(
            "SELECT conversation_type FROM conversations WHERE id = ?",
            (conversation_id,),
        ).fetchone() == ("course_qa",)
        assert connection.execute(
            "SELECT role, content FROM conversation_messages WHERE id = ?",
            (message_id,),
        ).fetchone() == ("user", "A legacy Course Q&A question")

        create_sql = connection.execute(
            "SELECT sql FROM sqlite_master "
            "WHERE type = 'table' AND name = 'conversations'"
        ).fetchone()[0]
        normalized_sql = " ".join(create_sql.lower().split())
        assert "ck_conversations_conversation_type_valid" in normalized_sql
        assert "conversation_type in ('course_qa', 'ai_tutor')" in normalized_sql

        ai_tutor_id = connection.execute(
            "INSERT INTO conversations (user_id, course_id, conversation_type) "
            "VALUES (?, ?, 'ai_tutor')",
            (user_id, course_id),
        ).lastrowid
        assert connection.execute(
            "SELECT conversation_type FROM conversations WHERE id = ?",
            (ai_tutor_id,),
        ).fetchone() == ("ai_tutor",)
        connection.commit()

        for invalid_type in (None, "other"):
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(
                    "INSERT INTO conversations "
                    "(user_id, course_id, conversation_type) VALUES (?, ?, ?)",
                    (user_id, course_id, invalid_type),
                )
            connection.rollback()

    run_alembic(database_path, tmp_path, "downgrade", CREDIT_LEDGER_REVISION)
    with sqlite3.connect(database_path) as connection:
        assert "conversation_type" not in {
            row[1] for row in connection.execute("PRAGMA table_info(conversations)")
        }
        assert connection.execute(
            "SELECT role, content FROM conversation_messages WHERE id = ?",
            (message_id,),
        ).fetchone() == ("user", "A legacy Course Q&A question")

    run_alembic(database_path, tmp_path, "upgrade", HEAD_REVISION)
    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT conversation_type FROM conversations ORDER BY id"
        ).fetchall() == [("course_qa",), ("course_qa",)]
        assert connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone() == (HEAD_REVISION,)


def test_remove_notification_settings_migration(tmp_path: Path) -> None:
    database_path = tmp_path / "remove-notification-settings.sqlite3"
    run_alembic(database_path, tmp_path, "upgrade", LEARNER_CONTEXT_REVISION)

    with sqlite3.connect(database_path) as connection:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(course_settings)")
        }
        assert "notifications" in columns
        assert "progress_reminders" in columns

        user_id = insert_legacy_user(
            connection,
            email="settings-owner@example.com",
            credits=10.0,
        )
        course_id = connection.execute(
            "INSERT INTO courses (title, is_deleted, owner_id) VALUES (?, 0, ?)",
            ("Settings test course", user_id),
        ).lastrowid
        settings_id = connection.execute(
            "INSERT INTO course_settings "
            "(course_id, study_mode, difficulty, question_count, summary_length, detail_level, notifications, progress_reminders) "
            "VALUES (?, 'Exam', 'Hard', 15, 'Long', 'Detailed', 0, 0)",
            (course_id,),
        ).lastrowid

    run_alembic(database_path, tmp_path, "upgrade", HEAD_REVISION)

    with sqlite3.connect(database_path) as connection:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(course_settings)")
        }
        assert "notifications" not in columns
        assert "progress_reminders" not in columns
        row = connection.execute(
            "SELECT study_mode, difficulty, question_count, summary_length, detail_level "
            "FROM course_settings WHERE id = ?",
            (settings_id,),
        ).fetchone()
        assert row == ("Exam", "Hard", 15, "Long", "Detailed")

    run_alembic(database_path, tmp_path, "downgrade", LEARNER_CONTEXT_REVISION)

    with sqlite3.connect(database_path) as connection:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(course_settings)")
        }
        assert "notifications" in columns
        assert "progress_reminders" in columns

    run_alembic(database_path, tmp_path, "upgrade", HEAD_REVISION)

    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone() == (HEAD_REVISION,)


def test_course_archive_state_migration(tmp_path: Path) -> None:
    database_path = tmp_path / "course-archive-state.sqlite3"
    run_alembic(
        database_path, tmp_path, "upgrade", REMOVE_NOTIFICATION_SETTINGS_REVISION
    )

    with sqlite3.connect(database_path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(courses)")}
        assert "is_archived" not in columns

        user_id = insert_legacy_user(
            connection,
            email="archive-owner@example.com",
            credits=10.0,
        )
        course_id = connection.execute(
            "INSERT INTO courses (title, is_deleted, owner_id) VALUES (?, 0, ?)",
            ("Archive test course", user_id),
        ).lastrowid

    run_alembic(database_path, tmp_path, "upgrade", HEAD_REVISION)

    with sqlite3.connect(database_path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(courses)")}
        assert "is_archived" in columns
        row = connection.execute(
            "SELECT title, is_archived, is_deleted FROM courses WHERE id = ?",
            (course_id,),
        ).fetchone()
        assert row == ("Archive test course", 0, 0)

    run_alembic(
        database_path, tmp_path, "downgrade", REMOVE_NOTIFICATION_SETTINGS_REVISION
    )

    with sqlite3.connect(database_path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(courses)")}
        assert "is_archived" not in columns

    run_alembic(database_path, tmp_path, "upgrade", HEAD_REVISION)

    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone() == (HEAD_REVISION,)


def test_processing_job_correlation_id_migration(tmp_path: Path) -> None:
    database_path = tmp_path / "processing-job-correlation-id.sqlite3"
    run_alembic(database_path, tmp_path, "upgrade", COURSE_ARCHIVE_STATE_REVISION)

    with sqlite3.connect(database_path) as connection:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(processing_jobs)")
        }
        assert "correlation_id" not in columns

        user_id = insert_legacy_user(
            connection,
            email="job-correlation@example.com",
            credits=10.0,
        )
        course_id = connection.execute(
            "INSERT INTO courses (title, is_deleted, owner_id) VALUES (?, 0, ?)",
            ("Correlation test course", user_id),
        ).lastrowid
        doc_id = str(uuid4())
        connection.execute(
            """
            INSERT INTO uploaded_documents (
                id, course_id, user_id, original_file_name, file_type,
                mime_type, file_size, file_hash, storage_provider,
                storage_key, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                doc_id,
                course_id,
                user_id,
                "doc.pdf",
                "pdf",
                "application/pdf",
                100,
                "0" * 64,
                "local",
                "key123",
                "uploaded",
            ),
        )
        job_id = connection.execute(
            """
            INSERT INTO processing_jobs (
                document_id, course_id, job_type, status, attempt_count,
                max_attempts, available_at
            ) VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (doc_id, course_id, "extract_document", "queued", 0, 3),
        ).lastrowid

    run_alembic(
        database_path,
        tmp_path,
        "upgrade",
        PROCESSING_JOB_CORRELATION_ID_REVISION,
    )

    with sqlite3.connect(database_path) as connection:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(processing_jobs)")
        }
        assert "correlation_id" in columns
        row = connection.execute(
            "SELECT id, correlation_id, status FROM processing_jobs WHERE id = ?",
            (job_id,),
        ).fetchone()
        assert row == (job_id, None, "queued")

    run_alembic(database_path, tmp_path, "downgrade", COURSE_ARCHIVE_STATE_REVISION)

    with sqlite3.connect(database_path) as connection:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(processing_jobs)")
        }
        assert "correlation_id" not in columns

    run_alembic(database_path, tmp_path, "upgrade", HEAD_REVISION)

    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone() == (HEAD_REVISION,)


def test_ai_usage_cost_migration_preserves_existing_events(tmp_path: Path) -> None:
    database_path = tmp_path / "ai-usage-cost.sqlite3"
    run_alembic(
        database_path,
        tmp_path,
        "upgrade",
        PROCESSING_JOB_CORRELATION_ID_REVISION,
    )

    with sqlite3.connect(database_path) as connection:
        user_id = insert_legacy_user(
            connection,
            email="cost-migration@example.com",
            credits=10.0,
        )
        usage_id = connection.execute(
            "INSERT INTO ai_usage_logs "
            "(user_id, generation_type, provider, model, success) "
            "VALUES (?, 'quiz', 'gemini', 'existing-model', 1)",
            (user_id,),
        ).lastrowid

    run_alembic(database_path, tmp_path, "upgrade", HEAD_REVISION)

    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT model, estimated_cost_usd, pricing_version "
            "FROM ai_usage_logs WHERE id = ?",
            (usage_id,),
        ).fetchone() == ("existing-model", None, None)

    run_alembic(
        database_path,
        tmp_path,
        "downgrade",
        PROCESSING_JOB_CORRELATION_ID_REVISION,
    )

    with sqlite3.connect(database_path) as connection:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(ai_usage_logs)")
        }
        assert "estimated_cost_usd" not in columns
        assert "pricing_version" not in columns
        assert connection.execute(
            "SELECT model FROM ai_usage_logs WHERE id = ?", (usage_id,)
        ).fetchone() == ("existing-model",)

    run_alembic(database_path, tmp_path, "upgrade", HEAD_REVISION)

    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone() == (HEAD_REVISION,)


def test_ai_usage_cost_downgrade_rejects_long_model_identifiers(tmp_path: Path) -> None:
    database_path = tmp_path / "ai-usage-cost-long-model.sqlite3"
    run_alembic(database_path, tmp_path, "upgrade", HEAD_REVISION)

    with sqlite3.connect(database_path) as connection:
        user_id = insert_legacy_user(
            connection,
            email="long-cost-model@example.com",
            credits=10.0,
        )
        connection.execute(
            "INSERT INTO ai_usage_logs "
            "(user_id, generation_type, provider, model, success) "
            "VALUES (?, 'quiz', 'custom', ?, 1)",
            (user_id, "m" * 128),
        )

    completed = invoke_alembic(
        database_path,
        tmp_path,
        "downgrade",
        PROCESSING_JOB_CORRELATION_ID_REVISION,
    )

    assert completed.returncode != 0
    assert "model identifiers longer than 100 characters" in completed.stderr
    with sqlite3.connect(database_path) as connection:
        # The rate-limit-buckets downgrade above this one is a plain table
        # drop with no guard, so it completes before the walk reaches (and
        # is rejected by) the guarded ai-usage-cost downgrade -- the DB lands
        # one revision below head, not back at head itself.
        assert connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone() == (AI_USAGE_COST_REVISION,)


def test_course_exam_date_migration_converts_valid_dates_and_nulls_the_rest(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "course-exam-date.sqlite3"
    run_alembic(database_path, tmp_path, "upgrade", GENERATED_CITATIONS_REVISION)

    with sqlite3.connect(database_path) as connection:
        user_id = insert_legacy_user(
            connection,
            email="exam-date-owner@example.com",
            credits=10.0,
        )
        rows = {
            "iso": "2026-12-17",
            "padded": "  2026-03-05  ",
            "bare year": "2026",
            "year and month": "2026-09",
            "blank": "",
            "junk": "next friday",
            "absent": None,
            "impossible": "2026-02-30",
        }
        course_ids = {
            label: connection.execute(
                "INSERT INTO courses (title, exam_date, is_deleted, owner_id)"
                " VALUES (?, ?, 0, ?)",
                (label, value, user_id),
            ).lastrowid
            for label, value in rows.items()
        }

    run_alembic(database_path, tmp_path, "upgrade", EXAM_DATE_REVISION)

    with sqlite3.connect(database_path) as connection:
        stored = {
            label: connection.execute(
                "SELECT exam_date FROM courses WHERE id = ?", (course_id,)
            ).fetchone()[0]
            for label, course_id in course_ids.items()
        }

    assert stored["iso"] == "2026-12-17"
    assert stored["padded"] == "2026-03-05"
    assert stored["bare year"] is None
    assert stored["year and month"] is None
    assert stored["blank"] is None
    assert stored["junk"] is None
    assert stored["absent"] is None
    assert stored["impossible"] is None

    run_alembic(database_path, tmp_path, "downgrade", GENERATED_CITATIONS_REVISION)

    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT exam_date FROM courses WHERE id = ?", (course_ids["iso"],)
        ).fetchone() == ("2026-12-17",)

    run_alembic(database_path, tmp_path, "upgrade", HEAD_REVISION)

    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone() == (HEAD_REVISION,)


def test_course_exam_date_orders_chronologically_after_migration(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "course-exam-date-order.sqlite3"
    run_alembic(database_path, tmp_path, "upgrade", GENERATED_CITATIONS_REVISION)

    with sqlite3.connect(database_path) as connection:
        user_id = insert_legacy_user(
            connection,
            email="exam-order-owner@example.com",
            credits=10.0,
        )
        for title, value in (
            ("December", "2026-12-17"),
            ("February", "2026-02-03"),
            ("September", "2026-09-04"),
        ):
            connection.execute(
                "INSERT INTO courses (title, exam_date, is_deleted, owner_id)"
                " VALUES (?, ?, 0, ?)",
                (title, value, user_id),
            )

    run_alembic(database_path, tmp_path, "upgrade", EXAM_DATE_REVISION)

    with sqlite3.connect(database_path) as connection:
        ordered = [
            row[0]
            for row in connection.execute(
                "SELECT title FROM courses ORDER BY exam_date"
            )
        ]

    assert ordered == ["February", "September", "December"]


def test_course_topics_migration_splits_backfills_and_round_trips(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "course-topics.sqlite3"
    run_alembic(database_path, tmp_path, "upgrade", EXAM_DATE_REVISION)

    with sqlite3.connect(database_path) as connection:
        assert "topics" in {
            row[1] for row in connection.execute("PRAGMA table_info(courses)")
        }
        user_id = insert_legacy_user(
            connection,
            email="topics-owner@example.com",
            credits=10.0,
        )
        populated = connection.execute(
            "INSERT INTO courses (title, topics, is_deleted, owner_id)"
            " VALUES (?, ?, 0, ?)",
            ("Populated", "Graphs, Trees ,, graphs,  Shortest Paths ", user_id),
        ).lastrowid
        blank = connection.execute(
            "INSERT INTO courses (title, topics, is_deleted, owner_id)"
            " VALUES (?, ?, 0, ?)",
            ("Blank", "", user_id),
        ).lastrowid
        absent = connection.execute(
            "INSERT INTO courses (title, topics, is_deleted, owner_id)"
            " VALUES (?, NULL, 0, ?)",
            ("Absent", user_id),
        ).lastrowid

    run_alembic(database_path, tmp_path, "upgrade", COURSE_TOPICS_REVISION)

    with sqlite3.connect(database_path) as connection:
        assert "topics" not in {
            row[1] for row in connection.execute("PRAGMA table_info(courses)")
        }
        assert [
            row[0]
            for row in connection.execute(
                "SELECT name FROM course_topics WHERE course_id = ? ORDER BY position",
                (populated,),
            )
        ] == ["Graphs", "Trees", "Shortest Paths"]
        for course_id in (blank, absent):
            assert (
                connection.execute(
                    "SELECT COUNT(*) FROM course_topics WHERE course_id = ?",
                    (course_id,),
                ).fetchone()[0]
                == 0
            )

    run_alembic(database_path, tmp_path, "downgrade", EXAM_DATE_REVISION)

    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT topics FROM courses WHERE id = ?", (populated,)
        ).fetchone() == ("Graphs, Trees, Shortest Paths",)

    run_alembic(database_path, tmp_path, "upgrade", HEAD_REVISION)

    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone() == (HEAD_REVISION,)


def test_course_topics_cascade_on_course_delete(tmp_path: Path) -> None:
    database_path = tmp_path / "course-topics-cascade.sqlite3"
    run_alembic(database_path, tmp_path, "upgrade", HEAD_REVISION)

    with sqlite3.connect(database_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        user_id = insert_legacy_user(
            connection,
            email="topics-cascade@example.com",
            credits=10.0,
        )
        course_id = connection.execute(
            "INSERT INTO courses (title, is_deleted, owner_id) VALUES (?, 0, ?)",
            ("Cascade", user_id),
        ).lastrowid
        connection.execute(
            "INSERT INTO course_topics (course_id, position, name) VALUES (?, 0, ?)",
            (course_id, "Graphs"),
        )
        connection.execute("DELETE FROM courses WHERE id = ?", (course_id,))

        assert (
            connection.execute(
                "SELECT COUNT(*) FROM course_topics WHERE course_id = ?",
                (course_id,),
            ).fetchone()[0]
            == 0
        )


def _exam_mode_columns(connection, table: str) -> set[str]:
    return {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}


def test_exam_mode_migration_adds_only_tables_and_round_trips(tmp_path: Path) -> None:
    """The revision is add-only, so downgrading must leave no trace of it."""
    database_path = tmp_path / "exam-mode.sqlite3"
    run_alembic(database_path, tmp_path, "upgrade", COURSE_TOPICS_REVISION)

    with sqlite3.connect(database_path) as connection:
        existing = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert "exam_topic_candidates" not in existing
        assert "past_exam_questions" not in existing

    run_alembic(database_path, tmp_path, "upgrade", EXAM_MODE_REVISION)

    with sqlite3.connect(database_path) as connection:
        assert _exam_mode_columns(connection, "exam_topic_candidates") >= {
            "analysis_output_id",
            "course_id",
            "position",
            "topic_key",
            "display_label",
            "aliases",
            "in_syllabus",
            "in_course_topics",
            "in_past_exams",
            "in_material",
            "discovery_confidence",
            "syllabus_weight_percent",
            "past_exam_question_count",
            "citations",
            "created_at",
        }
        assert _exam_mode_columns(connection, "past_exam_questions") >= {
            "course_id",
            "document_id",
            "page_start",
            "page_end",
            "question_text",
            "question_type",
            "marks",
            "answer_guidance",
            "visual_refs",
            "topic_key",
            "citations",
        }
        assert "analysis_output_id" not in _exam_mode_columns(
            connection, "past_exam_questions"
        )
        assert _exam_mode_columns(connection, "uploaded_documents") >= {
            "exam_extraction_status",
            "exam_extraction_error_code",
        }
        indexes = {
            row[1] for row in connection.execute("PRAGMA index_list(generated_outputs)")
        }
        assert "uq_generated_outputs_id_course_id" in indexes

    run_alembic(database_path, tmp_path, "downgrade", COURSE_TOPICS_REVISION)

    with sqlite3.connect(database_path) as connection:
        remaining = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert "exam_topic_candidates" not in remaining
        assert "past_exam_questions" not in remaining
        assert not _exam_mode_columns(connection, "uploaded_documents") & {
            "exam_extraction_status",
            "exam_extraction_error_code",
        }
        indexes = {
            row[1] for row in connection.execute("PRAGMA index_list(generated_outputs)")
        }
        assert "uq_generated_outputs_id_course_id" not in indexes

    run_alembic(database_path, tmp_path, "upgrade", HEAD_REVISION)

    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone() == (HEAD_REVISION,)


def test_exam_mode_migration_leaves_legacy_generated_outputs_alone(
    tmp_path: Path,
) -> None:
    """A pre-existing output must not be backfilled with invented Exam Mode data."""
    database_path = tmp_path / "exam-mode-legacy.sqlite3"
    run_alembic(database_path, tmp_path, "upgrade", COURSE_TOPICS_REVISION)

    with sqlite3.connect(database_path) as connection:
        user_id = insert_legacy_user(
            connection, email="exam-legacy@example.com", credits=10.0
        )
        course_id = connection.execute(
            "INSERT INTO courses (title, is_deleted, owner_id) VALUES (?, 0, ?)",
            ("Legacy", user_id),
        ).lastrowid
        output_id = connection.execute(
            "INSERT INTO generated_outputs (course_id, output_type, content)"
            " VALUES (?, ?, ?)",
            (course_id, "study_guide", "{}"),
        ).lastrowid

    run_alembic(database_path, tmp_path, "upgrade", EXAM_MODE_REVISION)

    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT output_type, content FROM generated_outputs WHERE id = ?",
            (output_id,),
        ).fetchone() == ("study_guide", "{}")
        assert (
            connection.execute("SELECT COUNT(*) FROM exam_topic_candidates").fetchone()[
                0
            ]
            == 0
        )


def test_exam_mode_rows_cascade_from_their_owner_and_their_course(
    tmp_path: Path,
) -> None:
    """Candidates follow their analysis; questions follow the paper they came from."""
    database_path = tmp_path / "exam-mode-cascade.sqlite3"
    run_alembic(database_path, tmp_path, "upgrade", HEAD_REVISION)

    with sqlite3.connect(database_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        user_id = insert_legacy_user(
            connection, email="exam-cascade@example.com", credits=10.0
        )
        course_id = connection.execute(
            "INSERT INTO courses (title, is_deleted, owner_id) VALUES (?, 0, ?)",
            ("Cascade", user_id),
        ).lastrowid
        analysis_id = connection.execute(
            "INSERT INTO generated_outputs (course_id, output_type, content)"
            " VALUES (?, ?, ?)",
            (course_id, "exam_topic_analysis", "{}"),
        ).lastrowid
        document_id = str(uuid4())
        connection.execute(
            "INSERT INTO uploaded_documents "
            "(id, original_file_name, file_type, mime_type, file_size, file_hash, "
            "user_id, course_id, storage_provider, storage_key, status, "
            "material_kind) "
            "VALUES (?, ?, 'pdf', 'application/pdf', 7, ?, ?, ?, 'local:test', ?, "
            "'ready', 'past_exam')",
            (
                document_id,
                "final-2024.pdf",
                "e" * 64,
                user_id,
                course_id,
                "final-2024.pdf",
            ),
        )
        connection.execute(
            "INSERT INTO exam_topic_candidates"
            " (analysis_output_id, course_id, position, topic_key, display_label,"
            "  in_syllabus, in_course_topics, in_past_exams, in_material,"
            "  discovery_confidence)"
            " VALUES (?, ?, 0, 'graph-traversal', 'Graph Traversal', 1, 0, 0, 0, 0.9)",
            (analysis_id, course_id),
        )
        connection.execute(
            "INSERT INTO past_exam_questions"
            " (document_id, course_id, position, question_text, question_type)"
            " VALUES (?, ?, 0, 'Explain BFS.', 'structured')",
            (document_id, course_id),
        )

        connection.execute("DELETE FROM generated_outputs WHERE id = ?", (analysis_id,))

        assert (
            connection.execute("SELECT COUNT(*) FROM exam_topic_candidates").fetchone()[
                0
            ]
            == 0
        )
        # A question outlives the analysis that ranked from it, because it
        # belongs to the paper rather than to any one reading of it.
        assert (
            connection.execute("SELECT COUNT(*) FROM past_exam_questions").fetchone()[0]
            == 1
        )

        connection.execute(
            "DELETE FROM uploaded_documents WHERE id = ?", (document_id,)
        )

        assert (
            connection.execute("SELECT COUNT(*) FROM past_exam_questions").fetchone()[0]
            == 0
        )


def test_the_unlocks_migration_adds_only_what_it_claims_and_round_trips(
    tmp_path: Path,
) -> None:
    """The revision is add-only, so downgrading must leave no trace of it."""
    database_path = tmp_path / "exam-unlocks.sqlite3"
    run_alembic(database_path, tmp_path, "upgrade", EXAM_MODE_REVISION)

    with sqlite3.connect(database_path) as connection:
        assert "exam_topic_unlocks" not in {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert not _exam_mode_columns(connection, "quizzes") & {
            "purpose",
            "exam_plan_output_id",
            "exam_topic_key",
        }

    run_alembic(database_path, tmp_path, "upgrade", EXAM_UNLOCKS_REVISION)

    with sqlite3.connect(database_path) as connection:
        assert _exam_mode_columns(connection, "exam_topic_unlocks") >= {
            "course_id",
            "user_id",
            "topic_key",
            "credit_transaction_id",
            "amount",
            "created_at",
        }
        assert _exam_mode_columns(connection, "quizzes") >= {
            "purpose",
            "exam_plan_output_id",
            "exam_topic_key",
        }

    run_alembic(database_path, tmp_path, "downgrade", EXAM_MODE_REVISION)

    with sqlite3.connect(database_path) as connection:
        assert "exam_topic_unlocks" not in {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert not _exam_mode_columns(connection, "quizzes") & {
            "purpose",
            "exam_plan_output_id",
            "exam_topic_key",
        }

    run_alembic(database_path, tmp_path, "upgrade", HEAD_REVISION)


def test_an_existing_quiz_is_not_given_an_invented_purpose(tmp_path: Path) -> None:
    """A null purpose is the truth for a quiz nobody classified."""
    database_path = tmp_path / "exam-unlocks-legacy.sqlite3"
    run_alembic(database_path, tmp_path, "upgrade", EXAM_MODE_REVISION)

    with sqlite3.connect(database_path) as connection:
        user_id = insert_legacy_user(
            connection, email="quiz-purpose@example.com", credits=10.0
        )
        course_id = connection.execute(
            "INSERT INTO courses (title, is_deleted, owner_id) VALUES (?, 0, ?)",
            ("Legacy", user_id),
        ).lastrowid
        quiz_id = connection.execute(
            "INSERT INTO quizzes (course_id, user_id, title) VALUES (?, ?, ?)",
            (course_id, user_id, "Old quiz"),
        ).lastrowid

    run_alembic(database_path, tmp_path, "upgrade", EXAM_UNLOCKS_REVISION)

    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT purpose, exam_plan_output_id, exam_topic_key"
            " FROM quizzes WHERE id = ?",
            (quiz_id,),
        ).fetchone() == (None, None, None)


def test_a_topic_can_only_be_unlocked_once_per_student_and_course(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "exam-unlocks-unique.sqlite3"
    run_alembic(database_path, tmp_path, "upgrade", HEAD_REVISION)

    with sqlite3.connect(database_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        user_id = insert_legacy_user(
            connection, email="unlock-unique@example.com", credits=10.0
        )
        course_id = connection.execute(
            "INSERT INTO courses (title, is_deleted, owner_id) VALUES (?, 0, ?)",
            ("Unique", user_id),
        ).lastrowid
        connection.execute(
            "INSERT INTO exam_topic_unlocks (course_id, user_id, topic_key, amount)"
            " VALUES (?, ?, 'graph-traversal', 2.0)",
            (course_id, user_id),
        )

        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO exam_topic_unlocks (course_id, user_id, topic_key, amount)"
                " VALUES (?, ?, 'graph-traversal', 2.0)",
                (course_id, user_id),
            )

        connection.execute(
            "DELETE FROM courses WHERE id = ?",
            (course_id,),
        )
        assert (
            connection.execute("SELECT COUNT(*) FROM exam_topic_unlocks").fetchone()[0]
            == 0
        )


def test_a_past_exam_question_cannot_disagree_with_its_paper_about_the_course(
    tmp_path: Path,
) -> None:
    """The composite key is what stops a question naming another course's paper."""
    database_path = tmp_path / "exam-question-composite.sqlite3"
    run_alembic(database_path, tmp_path, "upgrade", HEAD_REVISION)

    with sqlite3.connect(database_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        user_id = insert_legacy_user(
            connection, email="exam-question-composite@example.com", credits=10.0
        )
        first = connection.execute(
            "INSERT INTO courses (title, is_deleted, owner_id) VALUES (?, 0, ?)",
            ("First", user_id),
        ).lastrowid
        second = connection.execute(
            "INSERT INTO courses (title, is_deleted, owner_id) VALUES (?, 0, ?)",
            ("Second", user_id),
        ).lastrowid
        document_id = str(uuid4())
        connection.execute(
            "INSERT INTO uploaded_documents "
            "(id, original_file_name, file_type, mime_type, file_size, file_hash, "
            "user_id, course_id, storage_provider, storage_key, status, "
            "material_kind) "
            "VALUES (?, ?, 'pdf', 'application/pdf', 7, ?, ?, ?, 'local:test', ?, "
            "'ready', 'past_exam')",
            (
                document_id,
                "midterm.pdf",
                "d" * 64,
                user_id,
                first,
                "midterm.pdf",
            ),
        )

        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO past_exam_questions"
                " (document_id, course_id, position, question_text, question_type)"
                " VALUES (?, ?, 0, 'Explain BFS.', 'structured')",
                (document_id, second),
            )


def test_an_exam_candidate_cannot_disagree_with_its_analysis_about_the_course(
    tmp_path: Path,
) -> None:
    """The composite key is what stops a course-scoped read leaking another course."""
    database_path = tmp_path / "exam-mode-composite.sqlite3"
    run_alembic(database_path, tmp_path, "upgrade", HEAD_REVISION)

    with sqlite3.connect(database_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        user_id = insert_legacy_user(
            connection, email="exam-composite@example.com", credits=10.0
        )
        first = connection.execute(
            "INSERT INTO courses (title, is_deleted, owner_id) VALUES (?, 0, ?)",
            ("First", user_id),
        ).lastrowid
        second = connection.execute(
            "INSERT INTO courses (title, is_deleted, owner_id) VALUES (?, 0, ?)",
            ("Second", user_id),
        ).lastrowid
        analysis_id = connection.execute(
            "INSERT INTO generated_outputs (course_id, output_type, content)"
            " VALUES (?, ?, ?)",
            (first, "exam_topic_analysis", "{}"),
        ).lastrowid

        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO exam_topic_candidates"
                " (analysis_output_id, course_id, position, topic_key, display_label,"
                "  in_syllabus, in_course_topics, in_past_exams, in_material,"
                "  discovery_confidence)"
                " VALUES (?, ?, 0, 'sorting', 'Sorting', 1, 0, 0, 0, 0.5)",
                (analysis_id, second),
            )


def test_progress_read_indexes_upgrade_downgrade_and_reupgrade(tmp_path: Path) -> None:
    database_path = tmp_path / "progress-read-indexes.sqlite3"
    expected = {
        "uploaded_documents": {
            "ix_uploaded_documents_course_status_created": [
                "course_id",
                "status",
                "created_at",
                "id",
            ]
        },
        "document_chunks": {
            "ix_document_chunks_course_document_index": [
                "course_id",
                "document_id",
                "chunk_index",
                "id",
            ]
        },
        "generated_outputs": {
            "ix_generated_outputs_user_course_created": [
                "user_id",
                "course_id",
                "created_at",
                "id",
            ],
            "ix_generated_outputs_user_created": [
                "user_id",
                "created_at",
                "id",
            ],
        },
        "conversations": {
            "ix_conversations_user_course_updated": [
                "user_id",
                "course_id",
                "updated_at",
                "id",
            ]
        },
        "quiz_attempts": {
            "ix_quiz_attempts_quiz_user_created": [
                "quiz_id",
                "user_id",
                "created_at",
                "id",
            ],
            "ix_quiz_attempts_user_created": ["user_id", "created_at", "id"],
            "ix_quiz_attempts_quiz_created": ["quiz_id", "created_at", "id"],
        },
    }

    run_alembic(database_path, tmp_path, "upgrade", COURSE_TOPICS_REVISION)
    with sqlite3.connect(database_path) as connection:
        for table_name, indexes in expected.items():
            assert set(indexes).isdisjoint(index_columns(connection, table_name))
        connection.execute(
            "CREATE INDEX ix_quiz_attempts_user_created "
            "ON quiz_attempts (user_id, created_at, id)"
        )

    run_alembic(database_path, tmp_path, "upgrade", HEAD_REVISION)
    with sqlite3.connect(database_path) as connection:
        for table_name, indexes in expected.items():
            actual = index_columns(connection, table_name)
            assert {name: actual[name] for name in indexes} == indexes
        attempt_indexes = index_columns(connection, "quiz_attempts")
        assert "ix_quiz_attempts_user_id" not in attempt_indexes
        assert "ix_quiz_attempts_quiz_id" not in attempt_indexes
        connection.execute("DROP INDEX ix_quiz_attempts_user_created")

    run_alembic(database_path, tmp_path, "downgrade", COURSE_TOPICS_REVISION)
    with sqlite3.connect(database_path) as connection:
        for table_name, indexes in expected.items():
            assert set(indexes).isdisjoint(index_columns(connection, table_name))
        attempt_indexes = index_columns(connection, "quiz_attempts")
        assert attempt_indexes["ix_quiz_attempts_user_id"] == ["user_id"]
        assert attempt_indexes["ix_quiz_attempts_quiz_id"] == ["quiz_id"]

    run_alembic(database_path, tmp_path, "upgrade", HEAD_REVISION)
    with sqlite3.connect(database_path) as connection:
        for table_name, indexes in expected.items():
            actual = index_columns(connection, table_name)
            assert {name: actual[name] for name in indexes} == indexes
        attempt_indexes = index_columns(connection, "quiz_attempts")
        assert "ix_quiz_attempts_user_id" not in attempt_indexes
        assert "ix_quiz_attempts_quiz_id" not in attempt_indexes


def _seed_quiz(connection, *, email: str, title: str = "Paper"):
    """A user, a course, and one quiz, using this suite's own legacy helpers."""
    user_id = insert_legacy_user(connection, email=email, credits=10.0)
    course_id = connection.execute(
        "INSERT INTO courses (title, is_deleted, owner_id) VALUES (?, 0, ?)",
        ("Legacy", user_id),
    ).lastrowid
    quiz_id = connection.execute(
        "INSERT INTO quizzes (course_id, user_id, title) VALUES (?, ?, ?)",
        (course_id, user_id, title),
    ).lastrowid
    return user_id, course_id, quiz_id


def test_the_quiz_sessions_migration_adds_only_what_it_claims_and_round_trips(
    tmp_path: Path,
) -> None:
    """The revision is add-only, so downgrading must leave no trace of it."""
    database_path = tmp_path / "quiz-sessions.sqlite3"
    run_alembic(database_path, tmp_path, "upgrade", EXAM_UNLOCKS_REVISION)

    def tables(connection) -> set[str]:
        return {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }

    with sqlite3.connect(database_path) as connection:
        assert not tables(connection) & {"quiz_sessions", "quiz_session_answers"}
        assert not _exam_mode_columns(connection, "quizzes") & {
            "time_limit_seconds",
            "generation_request_id",
        }
        assert "source_past_exam_question_id" not in _exam_mode_columns(
            connection, "quiz_questions"
        )

    run_alembic(database_path, tmp_path, "upgrade", QUIZ_SESSIONS_REVISION)

    with sqlite3.connect(database_path) as connection:
        assert tables(connection) >= {"quiz_sessions", "quiz_session_answers"}
        assert _exam_mode_columns(connection, "quiz_sessions") >= {
            "quiz_id",
            "user_id",
            "attempt_id",
            "status",
            "time_limit_seconds",
            "started_at",
            "expires_at",
            "submitted_at",
            "expired_at",
        }
        assert _exam_mode_columns(connection, "quiz_session_answers") >= {
            "session_id",
            "quiz_question_id",
            "selected_option_index",
            "text_response",
        }
        assert _exam_mode_columns(connection, "quizzes") >= {
            "time_limit_seconds",
            "generation_request_id",
        }
        assert "source_past_exam_question_id" in _exam_mode_columns(
            connection, "quiz_questions"
        )

    run_alembic(database_path, tmp_path, "downgrade", EXAM_UNLOCKS_REVISION)

    with sqlite3.connect(database_path) as connection:
        assert not tables(connection) & {"quiz_sessions", "quiz_session_answers"}
        assert not _exam_mode_columns(connection, "quizzes") & {
            "time_limit_seconds",
            "generation_request_id",
        }
        assert "source_past_exam_question_id" not in _exam_mode_columns(
            connection, "quiz_questions"
        )

    run_alembic(database_path, tmp_path, "upgrade", HEAD_REVISION)


def test_an_existing_quiz_is_not_given_an_invented_time_limit(tmp_path: Path) -> None:
    """A null time limit is the truth for every quiz that predates the clock."""
    database_path = tmp_path / "quiz-sessions-legacy.sqlite3"
    run_alembic(database_path, tmp_path, "upgrade", EXAM_UNLOCKS_REVISION)

    with sqlite3.connect(database_path) as connection:
        _, _, quiz_id = _seed_quiz(
            connection, email="legacy-timing@example.com", title="Legacy quiz"
        )

    run_alembic(database_path, tmp_path, "upgrade", QUIZ_SESSIONS_REVISION)

    with sqlite3.connect(database_path) as connection:
        row = connection.execute(
            "SELECT time_limit_seconds, generation_request_id FROM quizzes WHERE id = ?",
            (quiz_id,),
        ).fetchone()

    assert row == (None, None)


def test_two_quizzes_may_share_a_null_generation_request_id(tmp_path: Path) -> None:
    """Null is distinct in the unique index, so nothing needs back-filling.

    Without that, every existing quiz would collide the moment the index was
    created, and the revision could not be add-only.
    """
    database_path = tmp_path / "quiz-sessions-null.sqlite3"
    run_alembic(database_path, tmp_path, "upgrade", QUIZ_SESSIONS_REVISION)

    with sqlite3.connect(database_path) as connection:
        user_id, course_id, _ = _seed_quiz(
            connection, email="null-request@example.com", title="One"
        )
        connection.execute(
            "INSERT INTO quizzes (course_id, user_id, title) VALUES (?, ?, ?)",
            (course_id, user_id, "Two"),
        )
        connection.execute(
            "INSERT INTO quizzes "
            "(course_id, user_id, title, generation_request_id) VALUES (?, ?, ?, ?)",
            (course_id, user_id, "Three", "same-request"),
        )
        connection.commit()

        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO quizzes "
                "(course_id, user_id, title, generation_request_id) "
                "VALUES (?, ?, ?, ?)",
                (course_id, user_id, "Four", "same-request"),
            )


def test_a_sitting_cannot_claim_to_be_submitted_without_an_attempt(
    tmp_path: Path,
) -> None:
    """The bad state is unrepresentable, not merely avoided by the application."""
    database_path = tmp_path / "quiz-sessions-states.sqlite3"
    run_alembic(database_path, tmp_path, "upgrade", QUIZ_SESSIONS_REVISION)

    with sqlite3.connect(database_path) as connection:
        user_id, _, quiz_id = _seed_quiz(connection, email="session-states@example.com")
        connection.commit()

        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO quiz_sessions "
                "(quiz_id, user_id, status, time_limit_seconds, started_at, "
                "expires_at, submitted_at, attempt_id) "
                "VALUES (?, ?, 'submitted', 60, '2026-01-01 00:00:00', "
                "'2026-01-01 01:00:00', '2026-01-01 00:30:00', NULL)",
                (quiz_id, user_id),
            )


def test_a_sitting_cannot_expire_before_it_started(tmp_path: Path) -> None:
    database_path = tmp_path / "quiz-sessions-order.sqlite3"
    run_alembic(database_path, tmp_path, "upgrade", QUIZ_SESSIONS_REVISION)

    with sqlite3.connect(database_path) as connection:
        user_id, _, quiz_id = _seed_quiz(connection, email="session-order@example.com")
        connection.commit()

        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO quiz_sessions "
                "(quiz_id, user_id, status, time_limit_seconds, started_at, expires_at) "
                "VALUES (?, ?, 'active', 60, '2026-01-01 01:00:00', "
                "'2026-01-01 00:00:00')",
                (quiz_id, user_id),
            )


def test_only_one_sitting_of_a_paper_may_be_live_at_a_time(tmp_path: Path) -> None:
    """A reloaded page must rejoin its clock rather than start a second one.

    The index is partial, so a finished sitting never blocks a retake.
    """
    database_path = tmp_path / "quiz-sessions-active.sqlite3"
    run_alembic(database_path, tmp_path, "upgrade", QUIZ_SESSIONS_REVISION)

    with sqlite3.connect(database_path) as connection:
        user_id, _, quiz_id = _seed_quiz(connection, email="session-live@example.com")
        connection.execute(
            "INSERT INTO quiz_sessions "
            "(quiz_id, user_id, status, time_limit_seconds, started_at, expires_at) "
            "VALUES (?, ?, 'active', 60, '2026-01-01 00:00:00', "
            "'2026-01-01 01:00:00')",
            (quiz_id, user_id),
        )
        connection.execute(
            "INSERT INTO quiz_sessions "
            "(quiz_id, user_id, status, time_limit_seconds, started_at, expires_at, "
            "expired_at) "
            "VALUES (?, ?, 'expired', 60, '2025-01-01 00:00:00', "
            "'2025-01-01 01:00:00', '2025-01-01 01:00:00')",
            (quiz_id, user_id),
        )
        connection.commit()

        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO quiz_sessions "
                "(quiz_id, user_id, status, time_limit_seconds, started_at, expires_at) "
                "VALUES (?, ?, 'active', 60, '2026-02-01 00:00:00', "
                "'2026-02-01 01:00:00')",
                (quiz_id, user_id),
            )


def test_upgrade_profile_documents_tables(tmp_path: Path) -> None:
    """Upgrades to PROFILE_DOCUMENTS_REVISION and verifies table schemas and constraints."""
    database_path = tmp_path / "profile-docs-migration.sqlite3"
    run_alembic(database_path, tmp_path, "upgrade", PROFILE_DOCUMENTS_REVISION)

    with sqlite3.connect(database_path) as connection:
        # Seed user
        connection.execute(
            "INSERT INTO users (name, email, password_hash, role_id) VALUES "
            "('Test User', 'user@example.com', 'hash', 1)"
        )
        user_id = connection.execute(
            "SELECT id FROM users WHERE email = 'user@example.com'"
        ).fetchone()[0]

        # Insert into profile_documents
        doc_id = str(uuid4())
        connection.execute(
            "INSERT INTO profile_documents "
            "(id, original_file_name, file_type, mime_type, file_size, file_hash, user_id, storage_provider, storage_key, status) "
            "VALUES (?, 'notes.pdf', 'pdf', 'application/pdf', 1024, ?, ?, 'local:default', 'users/1/doc/source.pdf', 'ready')",
            (doc_id, "a" * 64, user_id),
        )

        # Insert into profile_document_chunks
        connection.execute(
            "INSERT INTO profile_document_chunks (document_id, user_id, chunk_index, text) "
            "VALUES (?, ?, 0, 'sample chunk text')",
            (doc_id, user_id),
        )
        chunk_id = connection.execute(
            "SELECT id FROM profile_document_chunks WHERE document_id = ?",
            (doc_id,),
        ).fetchone()[0]

        # Insert into profile_chunk_embeddings
        connection.execute(
            "INSERT INTO profile_chunk_embeddings (chunk_id, document_id, user_id, chunk_index, embedding, embedding_provider, embedding_model, dimensions) "
            "VALUES (?, ?, ?, 0, ?, 'test', 'test-model', 768)",
            (chunk_id, doc_id, user_id, b"0" * (768 * 4)),
        )

        # Insert into profile_document_pages
        connection.execute(
            "INSERT INTO profile_document_pages (document_id, user_id, content_index, raw_text, text) "
            "VALUES (?, ?, 0, 'raw', 'clean')",
            (doc_id, user_id),
        )
        page_id = connection.execute(
            "SELECT id FROM profile_document_pages WHERE document_id = ?",
            (doc_id,),
        ).fetchone()[0]

        # Insert into profile_document_visuals
        connection.execute(
            "INSERT INTO profile_document_visuals (page_id, visual_index, visual_type, source, bbox_x0, bbox_y0, bbox_x1, bbox_y1) "
            "VALUES (?, 0, 'diagram', 'image', 0.0, 0.0, 100.0, 100.0)",
            (page_id,),
        )

        # Insert into profile_processing_jobs
        connection.execute(
            "INSERT INTO profile_processing_jobs (document_id, user_id, job_type, status, attempt_count, max_attempts) "
            "VALUES (?, ?, 'extract_document', 'queued', 0, 3)",
            (doc_id, user_id),
        )

        connection.commit()

        # Duplicate file_hash for same user should fail
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO profile_documents "
                "(id, original_file_name, file_type, mime_type, file_size, file_hash, user_id, storage_provider, storage_key, status) "
                "VALUES (?, 'notes2.pdf', 'pdf', 'application/pdf', 1024, ?, ?, 'local:default', 'users/1/doc2/source.pdf', 'ready')",
                (str(uuid4()), "a" * 64, user_id),
            )
