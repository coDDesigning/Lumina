"""add profile processing jobs queue composite indexes

Revision ID: d8a2b4c6e901
Revises: 3317a08487dd
Create Date: 2026-09-07 00:00:00.000000

Profile processing job claim and recovery queries filter on status with available_at
or lease_expires_at, and order by available_at/id or id.

`CREATE INDEX CONCURRENTLY` avoids locking the table against writes on PostgreSQL;
`autocommit_block()` is required because `CONCURRENTLY` cannot run inside a
transaction. SQLite uses standard `CREATE INDEX`.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "d8a2b4c6e901"
down_revision: str | None = "3317a08487dd"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEXES = (
    (
        "ix_profile_processing_jobs_claimable",
        "profile_processing_jobs",
        ["status", "available_at", "id"],
    ),
    (
        "ix_profile_processing_jobs_recoverable",
        "profile_processing_jobs",
        ["status", "lease_expires_at", "id"],
    ),
)


def upgrade() -> None:
    is_postgresql = op.get_bind().dialect.name == "postgresql"
    if is_postgresql:
        with op.get_context().autocommit_block():
            for name, table, columns in _INDEXES:
                op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")
                op.create_index(
                    name,
                    table,
                    columns,
                    unique=False,
                    postgresql_concurrently=True,
                )
    else:
        for name, table, columns in _INDEXES:
            op.create_index(name, table, columns, unique=False, if_not_exists=True)


def downgrade() -> None:
    is_postgresql = op.get_bind().dialect.name == "postgresql"
    if is_postgresql:
        with op.get_context().autocommit_block():
            for name, _table, _columns in _INDEXES:
                op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")
    else:
        for name, table, _columns in _INDEXES:
            op.drop_index(name, table_name=table, if_exists=True)
