import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

from alembic.config import Config
from alembic.script import ScriptDirectory

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ALEMBIC_CONFIG = PROJECT_ROOT / "alembic.ini"
BASE_REVISION = "97d9fd86a3ba"
HEAD_REVISION = "d2a7f0c91e35"


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
        HEAD_REVISION: BASE_REVISION,
        BASE_REVISION: None,
    }


def run_alembic(
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
    assert completed.returncode == 0, (
        f"Alembic {' '.join(arguments)} failed\n"
        f"stdout:\n{completed.stdout}\n"
        f"stderr:\n{completed.stderr}"
    )
    return completed


def database_tables(connection: sqlite3.Connection) -> set[str]:
    return {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
        if not row[0].startswith("sqlite_")
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
        assert "processing_jobs" in tables

        roles = connection.execute("SELECT name FROM roles ORDER BY name").fetchall()
        assert roles == [("admin",), ("user",)]

        revision = connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone()
        assert revision == (HEAD_REVISION,)

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

        users_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'users'"
        ).fetchone()
        assert users_sql is not None
        assert "uq_users_is_initial_admin" in users_sql[0].lower()

        chunk_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(document_chunks)")
        }
        assert "page_number" in chunk_columns
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
        job_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(processing_jobs)")
        }
        assert {"processing_stage", "failed_stage"} <= job_columns


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
