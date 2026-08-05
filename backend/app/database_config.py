"""Database-only environment loading shared by the app and Alembic."""

import os

from sqlalchemy.engine import make_url

MODE_SELF_HOSTED = "self_hosted"
MODE_HOSTED = "hosted"


def load_deployment_mode() -> str:
    mode = os.getenv("DEPLOYMENT_MODE", MODE_SELF_HOSTED)
    if mode not in (MODE_SELF_HOSTED, MODE_HOSTED):
        raise ValueError(
            f"DEPLOYMENT_MODE must be '{MODE_SELF_HOSTED}' or "
            f"'{MODE_HOSTED}', got: '{mode}'"
        )
    return mode


def load_database_url(mode: str | None = None) -> str:
    mode = mode or load_deployment_mode()
    default = "sqlite:///./data/lumina.db" if mode == MODE_SELF_HOSTED else ""
    database_url = os.getenv("DATABASE_URL") or default

    if not database_url:
        raise ValueError("Hosted mode requires DATABASE_URL to be set.")
    parsed_url = make_url(database_url)
    if mode == MODE_HOSTED and parsed_url.get_backend_name() != "postgresql":
        raise ValueError("Hosted mode requires a PostgreSQL DATABASE_URL.")
    supported_drivers = {
        "sqlite": {"sqlite", "sqlite+pysqlite"},
        "postgresql": {"postgresql", "postgresql+psycopg"},
    }
    backend = parsed_url.get_backend_name()
    if (
        backend not in supported_drivers
        or parsed_url.drivername not in supported_drivers[backend]
    ):
        raise ValueError(f"Unsupported database driver: {parsed_url.drivername}")
    return database_url
