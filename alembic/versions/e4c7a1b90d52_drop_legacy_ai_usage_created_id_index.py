"""drop the legacy ai_usage_logs created_at/id index left behind on deployed databases

Revision ID: e4c7a1b90d52
Revises: d8a2b4c6e901
Create Date: 2026-09-07 00:00:00.000000

`a6e2c8f41b90` originally created `ix_ai_usage_logs_created_id` and was later
rewritten to drop it instead. Databases that had already stamped that revision
never ran the rewritten body, so the index survives there while the models
declare only `ix_ai_usage_logs_success_created`. `alembic check` reports that
gap as a pending `remove_index` and fails the deployment migration task.

This revision performs the retirement as its own step so every database
converges, whether or not it ran the original body. `DROP INDEX CONCURRENTLY`
avoids locking the table against writes on PostgreSQL; `autocommit_block()` is
required because `CONCURRENTLY` cannot run inside a transaction. SQLite uses a
standard drop.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "e4c7a1b90d52"
down_revision: str | None = "d8a2b4c6e901"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEX_NAME = "ix_ai_usage_logs_created_id"


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {_INDEX_NAME}")
    else:
        op.drop_index(_INDEX_NAME, table_name="ai_usage_logs", if_exists=True)


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            op.create_index(
                _INDEX_NAME,
                "ai_usage_logs",
                ["created_at", "id"],
                unique=False,
                postgresql_concurrently=True,
                if_not_exists=True,
            )
    else:
        op.create_index(
            _INDEX_NAME,
            "ai_usage_logs",
            ["created_at", "id"],
            unique=False,
            if_not_exists=True,
        )
