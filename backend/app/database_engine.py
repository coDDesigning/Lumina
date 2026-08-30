"""Shared SQLAlchemy engine configuration for runtime and migrations."""

import time
from functools import partial
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, event
from sqlalchemy.engine import URL, Engine, make_url

SQLITE_BUSY_TIMEOUT_MILLISECONDS = 5_000
SQLITE_WAL_AUTOCHECKPOINT_PAGES = 1_000
SQLITE_WAL_JOURNAL_SIZE_LIMIT_BYTES = 64 * 1024 * 1024


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
    create_sqlite_parent_directory: bool = True,
    **engine_options: Any,
) -> Engine:
    """Create an engine with the required behavior for its SQL dialect."""
    url = normalize_database_url(value)

    if is_sqlite_database(url):
        if create_sqlite_parent_directory and url.database not in (
            None,
            "",
            ":memory:",
        ):
            Path(url.database).parent.mkdir(parents=True, exist_ok=True)

        connect_args = dict(engine_options.pop("connect_args", {}))
        connect_args.setdefault("check_same_thread", False)
        url_timeout = url.query.get("timeout")
        if "timeout" not in connect_args:
            connect_args["timeout"] = (
                float(url_timeout)
                if url_timeout is not None
                else SQLITE_BUSY_TIMEOUT_MILLISECONDS / 1000
            )
        sqlite_busy_timeout_milliseconds = int(float(connect_args["timeout"]) * 1000)
        sqlite_database = url.database or ""
        sqlite_file_backed = bool(sqlite_database) and not (
            sqlite_database == ":memory:"
            or sqlite_database.startswith("file::memory:")
            or url.query.get("mode") == "memory"
        )
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
        and sqlite_file_backed
        and "poolclass" not in engine_options
    )
    if apply_runtime_timeouts and supports_pool_timeout:
        engine_options.setdefault("pool_timeout", 5)
    engine_options.setdefault("hide_parameters", True)
    engine = create_engine(url, **engine_options)
    if is_sqlite_database(url):
        event.listen(
            engine,
            "connect",
            partial(
                _configure_sqlite_connection,
                busy_timeout_milliseconds=sqlite_busy_timeout_milliseconds,
                enable_wal=sqlite_file_backed,
            ),
        )
        event.listen(
            engine,
            "checkin",
            partial(
                _restore_sqlite_busy_timeout,
                busy_timeout_milliseconds=sqlite_busy_timeout_milliseconds,
            ),
        )
    return engine


def _configure_sqlite_connection(
    dbapi_connection: Any,
    _connection_record: Any,
    *,
    busy_timeout_milliseconds: int,
    enable_wal: bool,
) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute(f"PRAGMA busy_timeout={busy_timeout_milliseconds}")
    if enable_wal:
        current_mode = cursor.execute("PRAGMA journal_mode").fetchone()[0]
        if str(current_mode).lower() != "wal":
            deadline = time.monotonic() + (busy_timeout_milliseconds / 1000.0)
            while True:
                journal_mode = cursor.execute("PRAGMA journal_mode=WAL").fetchone()[0]
                if str(journal_mode).lower() == "wal":
                    break
                if time.monotonic() >= deadline:
                    cursor.close()
                    raise RuntimeError("File-backed SQLite requires WAL journal mode.")
                time.sleep(0.05)
        cursor.execute("PRAGMA synchronous=FULL")
        cursor.execute(f"PRAGMA wal_autocheckpoint={SQLITE_WAL_AUTOCHECKPOINT_PAGES}")
        cursor.execute(
            f"PRAGMA journal_size_limit={SQLITE_WAL_JOURNAL_SIZE_LIMIT_BYTES}"
        )
    cursor.close()


def _restore_sqlite_busy_timeout(
    dbapi_connection: Any | None,
    _connection_record: Any,
    *,
    busy_timeout_milliseconds: int,
) -> None:
    if dbapi_connection is None:
        return
    cursor = dbapi_connection.cursor()
    cursor.execute(f"PRAGMA busy_timeout={busy_timeout_milliseconds}")
    cursor.close()
