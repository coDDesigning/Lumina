"""add processing job correlation id

Revision ID: 3e8b1a4c7f20
Revises: f8b4c2d1e7a3
Create Date: 2026-08-25 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "3e8b1a4c7f20"
down_revision: str | Sequence[str] | None = "f8b4c2d1e7a3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("processing_jobs", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "correlation_id",
                sa.String(length=64),
                nullable=True,
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("processing_jobs", schema=None) as batch_op:
        batch_op.drop_column("correlation_id")
