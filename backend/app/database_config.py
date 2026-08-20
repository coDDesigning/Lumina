"""Database-only environment loading shared by the app and Alembic."""

import os
from pathlib import Path

from sqlalchemy.engine import make_url

MODE_SELF_HOSTED = "self_hosted"
MODE_HOSTED = "hosted"
APP_ENV_DEVELOPMENT = "development"
APP_ENV_STAGING = "staging"
APP_ENV_PRODUCTION = "production"
APP_ENVIRONMENTS = (APP_ENV_DEVELOPMENT, APP_ENV_STAGING, APP_ENV_PRODUCTION)


def load_deployment_mode() -> str:
    mode = os.getenv("DEPLOYMENT_MODE", MODE_SELF_HOSTED)
    if mode not in (MODE_SELF_HOSTED, MODE_HOSTED):
        raise ValueError(
            f"DEPLOYMENT_MODE must be '{MODE_SELF_HOSTED}' or "
            f"'{MODE_HOSTED}', got: '{mode}'"
        )
    return mode


def load_app_environment() -> str:
    app_env = os.getenv("APP_ENV", APP_ENV_DEVELOPMENT).strip().lower()
    if app_env not in APP_ENVIRONMENTS:
        raise ValueError(
            "APP_ENV must be 'development', 'staging', or 'production', "
            f"got: '{app_env}'"
        )
    return app_env


def load_database_url(
    mode: str | None = None,
    *,
    app_env: str | None = None,
) -> str:
    mode = mode or load_deployment_mode()
    if app_env is None:
        app_env = load_app_environment()
    if app_env not in APP_ENVIRONMENTS:
        raise ValueError(f"Unsupported application environment: '{app_env}'")

    default = "sqlite:///./data/lumina.db" if mode == MODE_SELF_HOSTED else ""
    configured_database_url = os.getenv("DATABASE_URL")
    if app_env == APP_ENV_PRODUCTION and (
        not configured_database_url or not configured_database_url.strip()
    ):
        raise ValueError("Production requires DATABASE_URL to be set explicitly.")
    database_url = configured_database_url or default

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

    if app_env == APP_ENV_PRODUCTION:
        if backend != "sqlite":
            # Must match config.STORAGE_BACKEND_S3; kept as a literal to avoid a
            # circular import for the Alembic environment, which imports this
            # module directly.
            if os.getenv("STORAGE_BACKEND", "local") != "s3":
                raise ValueError(
                    "Production PostgreSQL requires STORAGE_BACKEND=s3 because "
                    "a single instance's local disk cannot qualify as shared "
                    "storage."
                )
        if backend == "sqlite":
            database_path = Path(parsed_url.database or "")
            if not database_path.is_absolute():
                raise ValueError(
                    "Production SQLite DATABASE_URL must use an absolute path."
                )
            if not database_path.parent.is_dir():
                raise ValueError(
                    "Production SQLite database parent directory must already exist."
                )
    return database_url
