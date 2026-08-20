"""add course settings

Revision ID: 7b3e1a9c4d28
Revises: 2a7c4e9f8b10
Create Date: 2026-08-20 14:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op


revision: str = "7b3e1a9c4d28"
down_revision: str | Sequence[str] | None = "2a7c4e9f8b10"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    postgresql = op.get_bind().dialect.name == "postgresql"

    op.create_table(
        "course_settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("course_id", sa.Integer(), nullable=False),
        sa.Column(
            "study_mode",
            sa.String(length=50),
            server_default="Exam",
            nullable=False,
        ),
        sa.Column(
            "difficulty",
            sa.String(length=50),
            server_default="Adaptive",
            nullable=False,
        ),
        sa.Column(
            "question_count",
            sa.Integer(),
            server_default="10",
            nullable=False,
        ),
        sa.Column(
            "summary_length",
            sa.String(length=50),
            server_default="Medium",
            nullable=False,
        ),
        sa.Column(
            "detail_level",
            sa.String(length=50),
            server_default="Balanced",
            nullable=False,
        ),
        sa.Column(
            "notifications",
            sa.Boolean(),
            server_default=sa.true(),
            nullable=False,
        ),
        sa.Column(
            "progress_reminders",
            sa.Boolean(),
            server_default=sa.true(),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=postgresql),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=postgresql),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["course_id"],
            ["courses.id"],
            name="fk_course_settings_course_id_courses",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_course_settings"),
        sa.UniqueConstraint("course_id", name="uq_course_settings_course_id"),
    )
    op.create_index(
        op.f("ix_course_settings_course_id"),
        "course_settings",
        ["course_id"],
        unique=False,
    )
    with op.batch_alter_table("users") as batch_op:
        batch_op.alter_column(
            "preferred_model",
            existing_type=sa.String(length=100),
            server_default="gemini:gemini-3.6-flash",
            existing_nullable=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.alter_column(
            "preferred_model",
            existing_type=sa.String(length=100),
            server_default="gpt-4o-mini",
            existing_nullable=False,
        )
    op.drop_index(op.f("ix_course_settings_course_id"), table_name="course_settings")
    op.drop_table("course_settings")
