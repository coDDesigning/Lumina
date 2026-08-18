"""add quiz attempt answers

Revision ID: d3f8b21a6c40
Revises: c9b3d5e08f27
Create Date: 2026-08-19 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op


revision: str = "d3f8b21a6c40"
down_revision: str | Sequence[str] | None = "c9b3d5e08f27"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "quiz_questions",
        sa.Column("topic", sa.String(length=200), nullable=True),
    )
    op.add_column(
        "quiz_questions",
        sa.Column("explanation", sa.Text(), nullable=True),
    )
    op.add_column(
        "quiz_attempts",
        sa.Column("time_spent_seconds", sa.Integer(), nullable=True),
    )

    op.create_table(
        "quiz_attempt_answers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("attempt_id", sa.Integer(), nullable=False),
        sa.Column("quiz_question_id", sa.Integer(), nullable=False),
        sa.Column("selected_option_index", sa.Integer(), nullable=True),
        sa.Column("is_correct", sa.Boolean(), nullable=False),
        sa.CheckConstraint(
            "selected_option_index IS NULL OR selected_option_index >= 0",
            name="ck_quiz_attempt_answers_selected_option_index_nonnegative",
        ),
        sa.ForeignKeyConstraint(
            ["attempt_id"],
            ["quiz_attempts.id"],
            name="fk_quiz_attempt_answers_attempt_id_quiz_attempts",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["quiz_question_id"],
            ["quiz_questions.id"],
            name="fk_quiz_attempt_answers_quiz_question_id_quiz_questions",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_quiz_attempt_answers"),
        sa.UniqueConstraint(
            "attempt_id",
            "quiz_question_id",
            name="uq_attempt_answer_question",
        ),
    )
    op.create_index(
        "ix_quiz_attempt_answers_attempt_id",
        "quiz_attempt_answers",
        ["attempt_id"],
        unique=False,
    )
    op.create_index(
        "ix_quiz_attempt_answers_quiz_question_id",
        "quiz_attempt_answers",
        ["quiz_question_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_quiz_attempt_answers_quiz_question_id",
        table_name="quiz_attempt_answers",
    )
    op.drop_index(
        "ix_quiz_attempt_answers_attempt_id",
        table_name="quiz_attempt_answers",
    )
    op.drop_table("quiz_attempt_answers")
    op.drop_column("quiz_attempts", "time_spent_seconds")
    op.drop_column("quiz_questions", "explanation")
    op.drop_column("quiz_questions", "topic")
