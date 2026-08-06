from collections.abc import Generator

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from .config import APP_ENV_PRODUCTION, settings
from .database_engine import create_database_engine

__all__ = ["SessionLocal", "begin_serialized_write", "engine", "get_db"]

engine = create_database_engine(
    settings.database_url,
    create_sqlite_parent_directory=settings.app_env != APP_ENV_PRODUCTION,
)


SessionLocal = sessionmaker(
    bind=engine,
    class_=Session,
    expire_on_commit=False,
    autoflush=False,
)


def begin_serialized_write(db: Session) -> None:
    """Acquire SQLite's write lock; PostgreSQL uses row-level FOR UPDATE locks."""
    if db.get_bind().dialect.name == "sqlite":
        db.execute(text("BEGIN IMMEDIATE"))


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
