"""add ai usage logs

Revision ID: b7e2a9d1c3f4
Revises: a4fd52f56b91
Create Date: 2026-08-18 14:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b7e2a9d1c3f4"
down_revision: str | Sequence[str] | None = "a4fd52f56b91"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ai_usage_logs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("course_id", sa.Integer(), nullable=True),
        sa.Column("generation_type", sa.String(length=50), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("model", sa.String(length=100), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column("total_tokens", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.Column("error_category", sa.String(length=50), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["course_id"],
            ["courses.id"],
            name=op.f("fk_ai_usage_logs_course_id_courses"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_ai_usage_logs_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ai_usage_logs")),
    )
    with op.batch_alter_table("ai_usage_logs", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_ai_usage_logs_course_id"), ["course_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_ai_usage_logs_generation_type"),
            ["generation_type"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_ai_usage_logs_user_id"), ["user_id"], unique=False
        )
        batch_op.create_index(
            "ix_ai_usage_logs_course_created",
            ["course_id", "created_at"],
            unique=False,
        )
        batch_op.create_index(
            "ix_ai_usage_logs_type_created",
            ["generation_type", "created_at"],
            unique=False,
        )
        batch_op.create_index(
            "ix_ai_usage_logs_user_created",
            ["user_id", "created_at"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("ai_usage_logs", schema=None) as batch_op:
        batch_op.drop_index("ix_ai_usage_logs_user_created")
        batch_op.drop_index("ix_ai_usage_logs_type_created")
        batch_op.drop_index("ix_ai_usage_logs_course_created")
        batch_op.drop_index(batch_op.f("ix_ai_usage_logs_user_id"))
        batch_op.drop_index(batch_op.f("ix_ai_usage_logs_generation_type"))
        batch_op.drop_index(batch_op.f("ix_ai_usage_logs_course_id"))

    op.drop_table("ai_usage_logs")
