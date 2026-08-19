"""add profile knowledge updated at

Revision ID: e4a7b1c90d52
Revises: d3f8b21a6c40
Create Date: 2026-08-19 14:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e4a7b1c90d52"
down_revision: str | Sequence[str] | None = "d3f8b21a6c40"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _profile_knowledge_table() -> sa.Table:
    """Describe ``profile_knowledge`` before this revision for SQLite batch alter."""
    meta = sa.MetaData()
    return sa.Table(
        "profile_knowledge",
        meta,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("topic", sa.String(length=200), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Index("ix_profile_knowledge_user_id", "user_id"),
    )


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.add_column(
            "profile_knowledge",
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
        )
    else:
        with op.batch_alter_table(
            "profile_knowledge", schema=None, copy_from=_profile_knowledge_table()
        ) as batch_op:
            batch_op.add_column(
                sa.Column(
                    "updated_at",
                    sa.DateTime(timezone=True),
                    server_default=sa.func.now(),
                    nullable=False,
                )
            )

    op.execute("UPDATE profile_knowledge SET updated_at = created_at")


def downgrade() -> None:
    """Downgrade schema."""
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.drop_column("profile_knowledge", "updated_at")
    else:
        with op.batch_alter_table("profile_knowledge", schema=None) as batch_op:
            batch_op.drop_column("updated_at")
