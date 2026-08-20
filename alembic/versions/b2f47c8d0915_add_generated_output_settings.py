"""add generated output settings

Revision ID: b2f47c8d0915
Revises: 910e2719d549
Create Date: 2026-08-20 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b2f47c8d0915"
down_revision: str | Sequence[str] | None = "910e2719d549"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _generated_outputs_table() -> sa.Table:
    """Describe ``generated_outputs`` before this revision for SQLite batch alter."""
    meta = sa.MetaData()
    return sa.Table(
        "generated_outputs",
        meta,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("course_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("model_used", sa.String(length=150), nullable=True),
        sa.Column("output_type", sa.String(length=30), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["course_id"],
            ["courses.id"],
            name="fk_generated_outputs_course_id_courses",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_generated_outputs_user_id_users",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_generated_outputs"),
        sa.Index("ix_generated_outputs_course_id", "course_id"),
        sa.Index("ix_generated_outputs_user_id", "user_id"),
    )


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.add_column(
            "generated_outputs",
            sa.Column("generation_settings", sa.Text(), nullable=True),
        )
        op.add_column(
            "generated_outputs",
            sa.Column("generation_context", sa.Text(), nullable=True),
        )
        return

    with op.batch_alter_table(
        "generated_outputs", schema=None, copy_from=_generated_outputs_table()
    ) as batch_op:
        batch_op.add_column(sa.Column("generation_settings", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("generation_context", sa.Text(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.drop_column("generated_outputs", "generation_context")
        op.drop_column("generated_outputs", "generation_settings")
        return

    with op.batch_alter_table("generated_outputs", schema=None) as batch_op:
        batch_op.drop_column("generation_context")
        batch_op.drop_column("generation_settings")
