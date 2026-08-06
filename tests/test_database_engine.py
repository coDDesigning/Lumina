from pathlib import Path

import pytest
from sqlalchemy.exc import OperationalError

from backend.app import database_engine
from backend.app.database_engine import (
    create_database_engine,
    is_sqlite_database,
    normalize_database_url,
)


def test_postgresql_url_uses_installed_driver() -> None:
    url = normalize_database_url("postgresql://lumina:password@localhost:5432/lumina")

    assert url.drivername == "postgresql+psycopg"
    assert is_sqlite_database(url) is False


def test_sqlite_engine_creates_parent_and_enables_foreign_keys(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "missing" / "lumina.db"
    engine = create_database_engine(f"sqlite:///{database_path.as_posix()}")

    try:
        assert engine.hide_parameters is True
        with engine.connect() as connection:
            foreign_keys = connection.exec_driver_sql("PRAGMA foreign_keys").scalar()
        assert database_path.exists()
        assert foreign_keys == 1
    finally:
        engine.dispose()


def test_sqlite_engine_can_require_an_existing_parent(tmp_path: Path) -> None:
    database_path = tmp_path / "missing" / "lumina.db"
    engine = create_database_engine(
        f"sqlite:///{database_path.as_posix()}",
        create_sqlite_parent_directory=False,
    )

    try:
        with pytest.raises(OperationalError):
            with engine.connect():
                pass
        assert not database_path.parent.exists()
    finally:
        engine.dispose()


def test_migration_engine_can_disable_postgresql_runtime_timeouts(
    monkeypatch,
) -> None:
    captured_options = {}
    sentinel = object()

    def capture_engine(_url, **options):
        captured_options.update(options)
        return sentinel

    monkeypatch.setattr(database_engine, "create_engine", capture_engine)

    created = create_database_engine(
        "postgresql://lumina:password@localhost/lumina",
        apply_runtime_timeouts=False,
    )

    assert created is sentinel
    assert captured_options["connect_args"] == {"connect_timeout": 5}
    assert captured_options["hide_parameters"] is True
