"""add AI usage retention index

Revision ID: a6e2c8f41b90
Revises: 15bb8ad6d0f1
Create Date: 2026-08-27 00:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "a6e2c8f41b90"
down_revision: str | Sequence[str] | None = "15bb8ad6d0f1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEX_NAME = "ix_ai_usage_logs_created_id"


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {_INDEX_NAME}")
            op.create_index(
                _INDEX_NAME,
                "ai_usage_logs",
                ["created_at", "id"],
                unique=False,
                postgresql_concurrently=True,
            )
    else:
        op.create_index(
            _INDEX_NAME,
            "ai_usage_logs",
            ["created_at", "id"],
            unique=False,
            if_not_exists=True,
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {_INDEX_NAME}")
    else:
        op.drop_index(_INDEX_NAME, table_name="ai_usage_logs", if_exists=True)
