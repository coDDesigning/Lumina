from collections.abc import Generator

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from .config import APP_ENV_PRODUCTION, settings
from .database_engine import create_database_engine

__all__ = ["SessionLocal", "begin_serialized_write", "engine", "get_db"]

engine_options = {}
if settings.is_hosted:
    engine_options = {
        "pool_size": settings.database_pool_size,
        "max_overflow": settings.database_max_overflow,
        "pool_recycle": settings.database_pool_recycle_seconds,
        "pool_pre_ping": True,
    }

engine = create_database_engine(
    settings.database_url,
    create_sqlite_parent_directory=settings.app_env != APP_ENV_PRODUCTION,
    **engine_options,
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
