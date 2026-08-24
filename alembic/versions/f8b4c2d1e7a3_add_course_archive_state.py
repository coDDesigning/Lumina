"""add course archive state

Revision ID: f8b4c2d1e7a3
Revises: e7c1d4a8b203
Create Date: 2026-08-24 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f8b4c2d1e7a3"
down_revision: str | Sequence[str] | None = "e7c1d4a8b203"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("courses", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "is_archived",
                sa.Boolean(),
                server_default=sa.false(),
                nullable=False,
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("courses", schema=None) as batch_op:
        batch_op.drop_column("is_archived")
