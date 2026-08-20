from pathlib import Path

import pytest
from sqlalchemy.exc import OperationalError

from backend.app import database_engine
from backend.app.database_engine import (
    SQLITE_BUSY_TIMEOUT_MILLISECONDS,
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
            busy_timeout = connection.exec_driver_sql("PRAGMA busy_timeout").scalar()
        assert database_path.exists()
        assert foreign_keys == 1
        assert busy_timeout == SQLITE_BUSY_TIMEOUT_MILLISECONDS
    finally:
        engine.dispose()


def test_sqlite_engine_restores_busy_timeout_when_connection_returns_to_pool(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "lumina.db"
    engine = create_database_engine(f"sqlite:///{database_path.as_posix()}")

    try:
        with engine.connect() as connection:
            connection.exec_driver_sql("PRAGMA busy_timeout=123456")
            assert connection.exec_driver_sql("PRAGMA busy_timeout").scalar() == 123456

        with engine.connect() as connection:
            assert (
                connection.exec_driver_sql("PRAGMA busy_timeout").scalar()
                == SQLITE_BUSY_TIMEOUT_MILLISECONDS
            )
    finally:
        engine.dispose()


def test_sqlite_engine_preserves_custom_busy_timeout_baseline(tmp_path: Path) -> None:
    database_path = tmp_path / "custom-timeout.db"
    engine = create_database_engine(
        f"sqlite:///{database_path.as_posix()}?timeout=0.234",
        connect_args={"timeout": 0.123},
    )

    try:
        with engine.connect() as connection:
            assert connection.exec_driver_sql("PRAGMA busy_timeout").scalar() == 123
            connection.exec_driver_sql("PRAGMA busy_timeout=999")

        with engine.connect() as connection:
            assert connection.exec_driver_sql("PRAGMA busy_timeout").scalar() == 123
    finally:
        engine.dispose()


def test_sqlite_engine_honors_url_busy_timeout_baseline(tmp_path: Path) -> None:
    database_path = tmp_path / "url-timeout.db"
    engine = create_database_engine(
        f"sqlite:///{database_path.as_posix()}?timeout=0.234"
    )

    try:
        with engine.connect() as connection:
            assert connection.exec_driver_sql("PRAGMA busy_timeout").scalar() == 234
            connection.exec_driver_sql("PRAGMA busy_timeout=999")

        with engine.connect() as connection:
            assert connection.exec_driver_sql("PRAGMA busy_timeout").scalar() == 234
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


def test_postgresql_runtime_pool_options_are_forwarded(monkeypatch) -> None:
    captured_options = {}
    sentinel = object()

    def capture_engine(_url, **options):
        captured_options.update(options)
        return sentinel

    monkeypatch.setattr(database_engine, "create_engine", capture_engine)

    created = create_database_engine(
        "postgresql://lumina:password@proxy.example.com/lumina",
        pool_size=8,
        max_overflow=3,
        pool_recycle=600,
        pool_pre_ping=True,
    )

    assert created is sentinel
    assert captured_options["pool_size"] == 8
    assert captured_options["max_overflow"] == 3
    assert captured_options["pool_recycle"] == 600
    assert captured_options["pool_pre_ping"] is True
    assert captured_options["pool_timeout"] == 5
