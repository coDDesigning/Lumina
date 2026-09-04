"""add generation job dismissed at

Revision ID: f2d90b4c7168
Revises: c3b8e07a1d95
Create Date: 2026-09-04 12:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f2d90b4c7168"
down_revision: str | Sequence[str] | None = "c3b8e07a1d95"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("generation_jobs", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "dismissed_at",
                sa.DateTime(timezone=True),
                nullable=True,
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("generation_jobs", schema=None) as batch_op:
        batch_op.drop_column("dismissed_at")
