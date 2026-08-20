"""add conversation history

Revision ID: 910e2719d549
Revises: f4b18c7a2e60
Create Date: 2026-08-20 03:55:27.104055
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op


revision: str = "910e2719d549"
down_revision: str | Sequence[str] | None = "f4b18c7a2e60"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "conversations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("course_id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["course_id"],
            ["courses.id"],
            name=op.f("fk_conversations_course_id_courses"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_conversations_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "id",
            name=op.f("pk_conversations"),
        ),
    )

    with op.batch_alter_table("conversations", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_conversations_course_id"),
            ["course_id"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_conversations_user_id"),
            ["user_id"],
            unique=False,
        )

    op.create_table(
        "conversation_messages",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("conversation_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "role IN ('user', 'assistant')",
            name=op.f(
                "ck_conversation_messages_conversation_message_role_valid"
            ),
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name=op.f(
                "fk_conversation_messages_conversation_id_conversations"
            ),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "id",
            name=op.f("pk_conversation_messages"),
        ),
    )

    with op.batch_alter_table(
        "conversation_messages",
        schema=None,
    ) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_conversation_messages_conversation_id"),
            ["conversation_id"],
            unique=False,
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table(
        "conversation_messages",
        schema=None,
    ) as batch_op:
        batch_op.drop_index(
            batch_op.f("ix_conversation_messages_conversation_id")
        )

    op.drop_table("conversation_messages")

    with op.batch_alter_table("conversations", schema=None) as batch_op:
        batch_op.drop_index(
            batch_op.f("ix_conversations_user_id")
        )
        batch_op.drop_index(
            batch_op.f("ix_conversations_course_id")
        )

    op.drop_table("conversations")