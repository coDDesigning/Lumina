"""add account deletion tombstone

Revision ID: a2e8c6f14b90
Revises: c1d7e94b3a20
Create Date: 2026-09-13 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import backend

revision: str = "a2e8c6f14b90"
down_revision: str | Sequence[str] | None = "c1d7e94b3a20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "deletion_requested_at",
            backend.app.models.UTCDateTime(),
            nullable=True,
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "deletion_attempt_count",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "deletion_last_attempt_at",
            backend.app.models.UTCDateTime(),
            nullable=True,
        ),
    )
    op.add_column(
        "users",
        sa.Column("deletion_last_error_code", sa.String(length=100), nullable=True),
    )
    op.create_index(
        "ix_users_deletion_requested_at",
        "users",
        ["deletion_requested_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_users_deletion_requested_at", table_name="users")
    op.drop_column("users", "deletion_last_error_code")
    op.drop_column("users", "deletion_last_attempt_at")
    op.drop_column("users", "deletion_attempt_count")
    op.drop_column("users", "deletion_requested_at")
