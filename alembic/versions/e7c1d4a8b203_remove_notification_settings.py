"""remove notification settings

Revision ID: e7c1d4a8b203
Revises: a3d9e5c17b48
Create Date: 2026-08-22 12:15:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e7c1d4a8b203"
down_revision: str | Sequence[str] | None = "a3d9e5c17b48"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("course_settings", schema=None) as batch_op:
        batch_op.drop_column("progress_reminders")
        batch_op.drop_column("notifications")


def downgrade() -> None:
    with op.batch_alter_table("course_settings", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "notifications",
                sa.Boolean(),
                server_default=sa.true(),
                nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column(
                "progress_reminders",
                sa.Boolean(),
                server_default=sa.true(),
                nullable=False,
            )
        )
