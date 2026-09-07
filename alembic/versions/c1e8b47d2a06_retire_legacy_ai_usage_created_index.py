"""drop the legacy AI usage created_at index left behind on older databases

Revision ID: c1e8b47d2a06
Revises: d8a2b4c6e901
Create Date: 2026-09-07 00:00:00.000000

`a6e2c8f41b90` originally created `ix_ai_usage_logs_created_id` and was later
rewritten in place to drop it instead. A database stamped with that revision
before the rewrite keeps the index forever, because the revision never runs
again, and `alembic check` reports it as a removal on every run. This revision
drops it where it survives so those databases converge with fresh ones.

The downgrade is deliberately empty: the index is retired in favour of
`ix_ai_usage_logs_success_created`, and a rollback that recreated it would put
the drift back.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "c1e8b47d2a06"
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
    pass
