"""expand quiz attempts and progress

Revision ID: c8e1f5a9b3d2
Revises: b2f47c8d0915
Create Date: 2026-08-20 16:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c8e1f5a9b3d2"
down_revision: str | Sequence[str] | None = "b2f47c8d0915"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.add_column(
            "quiz_questions",
            sa.Column(
                "question_type",
                sa.String(length=30),
                server_default="multiple_choice",
                nullable=False,
            ),
        )
        op.alter_column(
            "quiz_questions",
            "correct_option_index",
            existing_type=sa.Integer(),
            nullable=True,
        )
        op.alter_column(
            "quiz_questions",
            "options",
            existing_type=sa.JSON(),
            nullable=True,
        )

        op.add_column(
            "quiz_attempt_answers",
            sa.Column("text_response", sa.Text(), nullable=True),
        )
        op.add_column(
            "quiz_attempt_answers",
            sa.Column("time_spent_seconds", sa.Integer(), nullable=True),
        )
        op.add_column(
            "quiz_attempt_answers",
            sa.Column("topic", sa.String(length=200), nullable=True),
        )
        op.alter_column(
            "quiz_attempt_answers",
            "is_correct",
            existing_type=sa.Boolean(),
            nullable=True,
        )

        op.add_column(
            "progress",
            sa.Column(
                "quizzes_completed",
                sa.Integer(),
                server_default="0",
                nullable=False,
            ),
        )
        op.add_column(
            "progress",
            sa.Column(
                "correct_answers_count",
                sa.Integer(),
                server_default="0",
                nullable=False,
            ),
        )
        op.add_column(
            "progress",
            sa.Column(
                "incorrect_answers_count",
                sa.Integer(),
                server_default="0",
                nullable=False,
            ),
        )
        op.add_column(
            "progress",
            sa.Column(
                "total_questions_answered",
                sa.Integer(),
                server_default="0",
                nullable=False,
            ),
        )
        op.add_column(
            "progress",
            sa.Column("weak_topics", sa.JSON(), nullable=True),
        )
        op.add_column(
            "progress",
            sa.Column("quiz_history", sa.JSON(), nullable=True),
        )
        return

    with op.batch_alter_table("quiz_questions", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "question_type",
                sa.String(length=30),
                server_default="multiple_choice",
                nullable=False,
            )
        )
        batch_op.alter_column(
            "correct_option_index",
            existing_type=sa.Integer(),
            nullable=True,
        )
        batch_op.alter_column(
            "options",
            existing_type=sa.JSON(),
            nullable=True,
        )

    with op.batch_alter_table("quiz_attempt_answers", schema=None) as batch_op:
        batch_op.add_column(sa.Column("text_response", sa.Text(), nullable=True))
        batch_op.add_column(
            sa.Column("time_spent_seconds", sa.Integer(), nullable=True)
        )
        batch_op.add_column(sa.Column("topic", sa.String(length=200), nullable=True))
        batch_op.alter_column(
            "is_correct",
            existing_type=sa.Boolean(),
            nullable=True,
        )

    with op.batch_alter_table("progress", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "quizzes_completed",
                sa.Integer(),
                server_default="0",
                nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column(
                "correct_answers_count",
                sa.Integer(),
                server_default="0",
                nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column(
                "incorrect_answers_count",
                sa.Integer(),
                server_default="0",
                nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column(
                "total_questions_answered",
                sa.Integer(),
                server_default="0",
                nullable=False,
            )
        )
        batch_op.add_column(sa.Column("weak_topics", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("quiz_history", sa.JSON(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.drop_column("progress", "quiz_history")
        op.drop_column("progress", "weak_topics")
        op.drop_column("progress", "total_questions_answered")
        op.drop_column("progress", "incorrect_answers_count")
        op.drop_column("progress", "correct_answers_count")
        op.drop_column("progress", "quizzes_completed")

        op.execute(
            "UPDATE quiz_attempt_answers SET is_correct = FALSE WHERE is_correct IS NULL"
        )
        op.alter_column(
            "quiz_attempt_answers",
            "is_correct",
            existing_type=sa.Boolean(),
            nullable=False,
        )
        op.drop_column("quiz_attempt_answers", "topic")
        op.drop_column("quiz_attempt_answers", "time_spent_seconds")
        op.drop_column("quiz_attempt_answers", "text_response")

        op.execute(
            "UPDATE quiz_questions SET correct_option_index = 0 WHERE correct_option_index IS NULL"
        )
        op.execute(
            "UPDATE quiz_questions SET options = '[]'::json WHERE options IS NULL"
        )
        op.alter_column(
            "quiz_questions",
            "options",
            existing_type=sa.JSON(),
            nullable=False,
        )
        op.alter_column(
            "quiz_questions",
            "correct_option_index",
            existing_type=sa.Integer(),
            nullable=False,
        )
        op.drop_column("quiz_questions", "question_type")
        return

    with op.batch_alter_table("progress", schema=None) as batch_op:
        batch_op.drop_column("quiz_history")
        batch_op.drop_column("weak_topics")
        batch_op.drop_column("total_questions_answered")
        batch_op.drop_column("incorrect_answers_count")
        batch_op.drop_column("correct_answers_count")
        batch_op.drop_column("quizzes_completed")

    op.execute(
        "UPDATE quiz_attempt_answers SET is_correct = 0 WHERE is_correct IS NULL"
    )
    with op.batch_alter_table("quiz_attempt_answers", schema=None) as batch_op:
        batch_op.alter_column(
            "is_correct",
            existing_type=sa.Boolean(),
            nullable=False,
        )
        batch_op.drop_column("topic")
        batch_op.drop_column("time_spent_seconds")
        batch_op.drop_column("text_response")

    op.execute(
        "UPDATE quiz_questions SET correct_option_index = 0 WHERE correct_option_index IS NULL"
    )
    op.execute("UPDATE quiz_questions SET options = '[]' WHERE options IS NULL")
    with op.batch_alter_table("quiz_questions", schema=None) as batch_op:
        batch_op.alter_column(
            "options",
            existing_type=sa.JSON(),
            nullable=False,
        )
        batch_op.alter_column(
            "correct_option_index",
            existing_type=sa.Integer(),
            nullable=False,
        )
        batch_op.drop_column("question_type")
