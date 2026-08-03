from pathlib import Path

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
        with engine.connect() as connection:
            foreign_keys = connection.exec_driver_sql("PRAGMA foreign_keys").scalar()
        assert database_path.exists()
        assert foreign_keys == 1
    finally:
        engine.dispose()
