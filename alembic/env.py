from logging.config import fileConfig

from sqlalchemy.pool import NullPool

from alembic import context
from backend.app import models  # noqa: F401
from backend.app.base import Base
from backend.app.database_config import (
    APP_ENV_PRODUCTION,
    load_app_environment,
    load_database_url,
)
from backend.app.database_engine import (
    create_database_engine,
    is_sqlite_database,
    normalize_database_url,
)

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)


app_env = load_app_environment()
configured_database_url = load_database_url(app_env=app_env)
database_url = normalize_database_url(configured_database_url)
target_metadata = Base.metadata
is_postgresql = database_url.get_backend_name() == "postgresql"


POSTGRESQL_ONLY_INDEXES = frozenset({"ix_chunk_embeddings_embedding_hnsw"})


def include_object(object_, name, type_, reflected, compare_to) -> bool:
    """Hide the pgvector index from autogenerate on other databases.

    The HNSW index is declared on the model so PostgreSQL keeps it, but
    `ddl_if` only suppresses emission - autogenerate would still report it as
    missing everywhere else, so `alembic check` needs it filtered out here.
    """
    if type_ == "index" and name in POSTGRESQL_ONLY_INDEXES:
        return is_postgresql
    return True


def run_migrations_offline() -> None:
    context.configure(
        url=database_url.render_as_string(hide_password=True),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
        render_as_batch=is_sqlite_database(database_url),
        include_object=include_object,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = create_database_engine(
        configured_database_url,
        apply_runtime_timeouts=False,
        create_sqlite_parent_directory=app_env != APP_ENV_PRODUCTION,
        poolclass=NullPool,
    )

    with connectable.connect() as connection:
        if is_sqlite_database(database_url):
            cursor = connection.connection.cursor()
            cursor.execute("PRAGMA foreign_keys=OFF")
            cursor.close()

        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
            render_as_batch=is_sqlite_database(database_url),
            include_object=include_object,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
