"""type persisted conversations

Revision ID: b9c1d4e7f2a6
Revises: d7f3a2c48e15
Create Date: 2026-08-21 12:00:00.000000

Every conversation written before this revision came from Course Q&A because
AI Tutor was stateless. The backfill can therefore classify those rows without
guessing, then make the discriminator mandatory for all future conversations.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b9c1d4e7f2a6"
down_revision: str | Sequence[str] | None = "d7f3a2c48e15"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    is_postgresql = bind.dialect.name == "postgresql"

    op.add_column(
        "conversations",
        sa.Column("conversation_type", sa.String(length=20), nullable=True),
    )
    op.execute(
        sa.text(
            "UPDATE conversations SET conversation_type = 'course_qa' "
            "WHERE conversation_type IS NULL"
        )
    )

    if is_postgresql:
        op.create_check_constraint(
            "ck_conversations_conversation_type_valid",
            "conversations",
            "conversation_type IN ('course_qa', 'ai_tutor')",
        )
        op.alter_column(
            "conversations",
            "conversation_type",
            existing_type=sa.String(length=20),
            nullable=False,
        )
    else:
        with op.batch_alter_table("conversations", schema=None) as batch_op:
            batch_op.create_check_constraint(
                batch_op.f("ck_conversations_conversation_type_valid"),
                "conversation_type IN ('course_qa', 'ai_tutor')",
            )
            batch_op.alter_column(
                "conversation_type",
                existing_type=sa.String(length=20),
                nullable=False,
            )


def downgrade() -> None:
    bind = op.get_bind()
    is_postgresql = bind.dialect.name == "postgresql"

    if is_postgresql:
        op.drop_constraint(
            "ck_conversations_conversation_type_valid",
            "conversations",
            type_="check",
        )
        op.drop_column("conversations", "conversation_type")
    else:
        with op.batch_alter_table("conversations", schema=None) as batch_op:
            batch_op.drop_constraint(
                batch_op.f("ck_conversations_conversation_type_valid"),
                type_="check",
            )
            batch_op.drop_column("conversation_type")
