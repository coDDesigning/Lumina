from logging.config import fileConfig

from alembic import context
from sqlalchemy import Connection
from sqlalchemy.engine import make_url

from backend.app import models
from backend.app.config import settings
from backend.app.database import create_database_engine

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = models.Base.metadata


def _database_url() -> str:
    return config.attributes.get("database_url", settings.database_url)


def _configure(connection: Connection | None = None) -> None:
    options = {
        "target_metadata": target_metadata,
        "compare_server_default": True,
        "compare_type": True,
        "render_as_batch": (
            connection.dialect.name == "sqlite"
            if connection is not None
            else make_url(_database_url()).get_backend_name() == "sqlite"
        ),
    }
    if connection is None:
        context.configure(
            url=_database_url(),
            literal_binds=True,
            dialect_opts={"paramstyle": "named"},
            **options,
        )
    else:
        context.configure(connection=connection, **options)


def run_migrations_offline() -> None:
    _configure()
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    supplied_connection = config.attributes.get("connection")
    if supplied_connection is not None:
        _configure(supplied_connection)
        with context.begin_transaction():
            context.run_migrations()
        return

    connectable = create_database_engine(_database_url())
    try:
        with connectable.connect() as connection:
            _configure(connection)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
