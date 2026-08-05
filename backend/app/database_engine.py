"""Shared SQLAlchemy engine configuration for runtime and migrations."""

from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, event
from sqlalchemy.engine import URL, Engine, make_url


def normalize_database_url(value: str) -> URL:
    """Normalize supported URLs to the drivers installed by Lumina."""
    url = make_url(value)
    if url.drivername == "postgresql":
        return url.set(drivername="postgresql+psycopg")
    return url


def is_sqlite_database(url: URL) -> bool:
    return url.get_backend_name() == "sqlite"


def create_database_engine(
    value: str,
    *,
    apply_runtime_timeouts: bool = True,
    **engine_options: Any,
) -> Engine:
    """Create an engine with the required behavior for its SQL dialect."""
    url = normalize_database_url(value)

    if is_sqlite_database(url):
        if url.database not in (None, "", ":memory:"):
            Path(url.database).parent.mkdir(parents=True, exist_ok=True)

        connect_args = dict(engine_options.pop("connect_args", {}))
        connect_args.setdefault("check_same_thread", False)
        connect_args.setdefault("timeout", 5)
        engine_options["connect_args"] = connect_args
    elif url.get_backend_name() == "postgresql":
        connect_args = dict(engine_options.pop("connect_args", {}))
        connect_args.setdefault("connect_timeout", 5)
        if apply_runtime_timeouts:
            connect_args.setdefault(
                "options",
                "-c lock_timeout=2000 -c statement_timeout=5000",
            )
        engine_options["connect_args"] = connect_args

    supports_pool_timeout = url.get_backend_name() == "postgresql" or (
        is_sqlite_database(url)
        and url.database not in (None, "", ":memory:")
        and "poolclass" not in engine_options
    )
    if apply_runtime_timeouts and supports_pool_timeout:
        engine_options.setdefault("pool_timeout", 5)
    engine_options.setdefault("hide_parameters", True)
    engine = create_engine(url, **engine_options)
    if is_sqlite_database(url):
        event.listen(engine, "connect", _enable_sqlite_foreign_keys)
    return engine


def _enable_sqlite_foreign_keys(dbapi_connection: Any, _connection_record: Any) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()
