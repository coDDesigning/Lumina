"""record policy acknowledgements

Revision ID: 6f2a9c4d1e73
Revises: a2e8c6f14b90
Create Date: 2026-09-12 00:00:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "6f2a9c4d1e73"
down_revision: str | None = "a2e8c6f14b90"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "policy_acknowledgements",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("policy_key", sa.String(length=50), nullable=False),
        sa.Column("policy_version", sa.String(length=20), nullable=False),
        sa.Column(
            "acknowledged_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "policy_key",
            "policy_version",
            name="uq_policy_acknowledgements_user_policy_version",
        ),
    )
    op.create_index(
        op.f("ix_policy_acknowledgements_user_id"),
        "policy_acknowledgements",
        ["user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_policy_acknowledgements_user_id"),
        table_name="policy_acknowledgements",
    )
    op.drop_table("policy_acknowledgements")
