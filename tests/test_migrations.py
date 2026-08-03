import os
import sqlite3
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ALEMBIC_CONFIG = PROJECT_ROOT / "alembic.ini"


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

        roles = connection.execute("SELECT name FROM roles ORDER BY name").fetchall()
        assert roles == [("admin",), ("user",)]

        revision = connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone()
        assert revision == ("97d9fd86a3ba",)

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
