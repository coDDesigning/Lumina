"""Central configuration for Lumina.

Reads environment variables ONCE at import time, validates them, and
exposes a single `settings` object that the rest of the codebase imports.
No other module should call os.getenv for application configuration -
this file is the single source of truth for "what the environment says".
"""

import os
from dataclasses import dataclass

MODE_SELF_HOSTED = "self_hosted"
MODE_HOSTED = "hosted"


@dataclass(frozen=True)
class Settings:
    # Which deployment flavor we are running as - the keystone value.
    deployment_mode: str

    # Structured database connection URL (SQLAlchemy format)
    database_url: str

    # Where ChromaDB persists its vector data (self-hosted mode only)
    chroma_persist_directory: str

    # Where uploaded files are stored on dist (self-hosted mode only)
    upload_directory: str

    @property
    def is_hosted(self) -> bool:
        return self.deployment_mode == MODE_HOSTED

    @property
    def is_self_hosted(self) -> bool:
        return self.deployment_mode == MODE_SELF_HOSTED


def _default_database_url(mode: str) -> str:
    """Return the sensible database default FOR THE GIVEN MODE.

    Self-hosted defaults to a local SQLite file so a fresh clone runs
    with zero setup. Hosted mode has NO safe default - a hosted
    deployment must consciously provide its PostgreSQL URL, so we
    return an empty string and let validation catch the omission
    loudly rather than inventing a database nobody asked for.
    """
    if mode == MODE_SELF_HOSTED:
        return "sqlite:///./data/lumina.db"

    return ""


def load_settings() -> Settings:
    """Read, validate and freeze the configuration from the environment.

    Raises ValueError with a human-actionable message if the
    environment is invalid. Failing HERE, at startup, is the whole
    point: a configuration mistake should kill the application in the
    first second with a clear message
    """

    mode = os.getenv("DEPLOYMENT_MODE", MODE_SELF_HOSTED)

    if mode not in (MODE_SELF_HOSTED, MODE_HOSTED):
        raise ValueError(
            f"DEPLOYMENT_MODE must be '{MODE_SELF_HOSTED}' or "
            f"'{MODE_HOSTED}' , got: '{mode}'"
        )

    database_url = os.getenv("DATABASE_URL") or _default_database_url(mode)

    if not database_url:
        raise ValueError(
            "Hosted mode requires DATABASE_URL to be set(a PostgreSQL connection URL)."
        )

    return Settings(
        deployment_mode=mode,
        database_url=database_url,
        chroma_persist_directory=os.getenv("CHROMA_PERSIST_DIRECTORY", "./data/chroma"),
        upload_directory=os.getenv("UPLOAD_DIRECTORY", "./data/uploads"),
    )


settings = load_settings()
