"""add missing model check constraints

Revision ID: 3317a08487dd
Revises: b6d21f4c8a37
Create Date: 2026-09-05 00:00:00.000000

Six CHECK constraints declared on the ORM models (quiz_questions.question_type,
quiz_attempt_answers.time_spent_seconds, and the four progress counters) were
never emitted by any prior revision, so the shipped database is weaker than
what Base.metadata.create_all() produces for the test suite. This closes that
gap.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "3317a08487dd"
down_revision: str | None = "b6d21f4c8a37"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_QUIZ_QUESTION_TYPES = (
    "multiple_choice",
    "true_false",
    "short_answer",
    "open_ended",
)

_POSTGRESQL_CONSTRAINTS = (
    (
        "quiz_questions",
        "ck_quiz_questions_quiz_question_type_valid",
        "question_type IN ("
        + ", ".join(f"'{value}'" for value in _QUIZ_QUESTION_TYPES)
        + ")",
    ),
    (
        "quiz_attempt_answers",
        "ck_quiz_attempt_answers_answer_time_spent_nonnegative",
        "time_spent_seconds IS NULL OR time_spent_seconds >= 0",
    ),
    (
        "progress",
        "ck_progress_quizzes_completed_nonnegative",
        "quizzes_completed >= 0",
    ),
    (
        "progress",
        "ck_progress_correct_answers_count_nonnegative",
        "correct_answers_count >= 0",
    ),
    (
        "progress",
        "ck_progress_incorrect_answers_count_nonnegative",
        "incorrect_answers_count >= 0",
    ),
    (
        "progress",
        "ck_progress_total_questions_answered_nonnegative",
        "total_questions_answered >= 0",
    ),
    (
        "past_exam_questions",
        "ck_past_exam_questions_page_requires_document",
        "document_id IS NOT NULL OR (page_start IS NULL AND page_end IS NULL)",
    ),
)


def _require_no_rows(query: str, message: str) -> None:
    count = op.get_bind().scalar(sa.text(query))
    if count:
        raise RuntimeError(message)


def _preflight() -> None:
    quoted_types = ", ".join(f"'{value}'" for value in _QUIZ_QUESTION_TYPES)
    _require_no_rows(
        f"SELECT COUNT(*) FROM quiz_questions WHERE question_type NOT IN ({quoted_types})",
        "Unrecognized quiz question types require manual correction before hardening.",
    )
    _require_no_rows(
        "SELECT COUNT(*) FROM quiz_attempt_answers "
        "WHERE time_spent_seconds IS NOT NULL AND time_spent_seconds < 0",
        "Negative quiz answer time spent requires manual correction before hardening.",
    )
    _require_no_rows(
        "SELECT COUNT(*) FROM progress WHERE quizzes_completed < 0 "
        "OR correct_answers_count < 0 OR incorrect_answers_count < 0 "
        "OR total_questions_answered < 0",
        "Negative progress counters require manual correction before hardening.",
    )
    _require_no_rows(
        "SELECT COUNT(*) FROM past_exam_questions "
        "WHERE document_id IS NULL AND (page_start IS NOT NULL OR page_end IS NOT NULL)",
        "Past exam questions with pages but no document require manual correction before hardening.",
    )


def _postgresql_add_constraint_not_valid(table: str, name: str, condition: str) -> None:
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = '{name}'
                  AND conrelid = '{table}'::regclass
            ) THEN
                ALTER TABLE {table} ADD CONSTRAINT {name} CHECK ({condition}) NOT VALID;
            END IF;
        END
        $$
        """
    )


def upgrade() -> None:
    _preflight()

    if op.get_bind().dialect.name == "postgresql":
        for table, name, condition in _POSTGRESQL_CONSTRAINTS:
            _postgresql_add_constraint_not_valid(table, name, condition)
        for table, name, _condition in _POSTGRESQL_CONSTRAINTS:
            op.execute(f"ALTER TABLE {table} VALIDATE CONSTRAINT {name}")

        with op.get_context().autocommit_block():
            op.execute(
                "CREATE INDEX IF NOT EXISTS ix_generated_outputs_user_course_created "
                "ON generated_outputs (user_id, course_id, created_at, id)"
            )
            op.execute(
                "CREATE INDEX IF NOT EXISTS ix_ai_usage_logs_success_created "
                "ON ai_usage_logs (success, created_at)"
            )
        return

    with op.batch_alter_table("quiz_questions", schema=None) as batch_op:
        batch_op.create_check_constraint(
            batch_op.f("ck_quiz_questions_quiz_question_type_valid"),
            "question_type IN ("
            + ", ".join(f"'{value}'" for value in _QUIZ_QUESTION_TYPES)
            + ")",
        )
    with op.batch_alter_table("quiz_attempt_answers", schema=None) as batch_op:
        batch_op.create_check_constraint(
            batch_op.f("ck_quiz_attempt_answers_answer_time_spent_nonnegative"),
            "time_spent_seconds IS NULL OR time_spent_seconds >= 0",
        )
    with op.batch_alter_table("progress", schema=None) as batch_op:
        batch_op.create_check_constraint(
            batch_op.f("ck_progress_quizzes_completed_nonnegative"),
            "quizzes_completed >= 0",
        )
        batch_op.create_check_constraint(
            batch_op.f("ck_progress_correct_answers_count_nonnegative"),
            "correct_answers_count >= 0",
        )
        batch_op.create_check_constraint(
            batch_op.f("ck_progress_incorrect_answers_count_nonnegative"),
            "incorrect_answers_count >= 0",
        )
        batch_op.create_check_constraint(
            batch_op.f("ck_progress_total_questions_answered_nonnegative"),
            "total_questions_answered >= 0",
        )
    with op.batch_alter_table("past_exam_questions", schema=None) as batch_op:
        batch_op.create_check_constraint(
            batch_op.f("ck_past_exam_questions_page_requires_document"),
            "document_id IS NOT NULL OR (page_start IS NULL AND page_end IS NULL)",
        )

    op.create_index(
        "ix_generated_outputs_user_course_created",
        "generated_outputs",
        ["user_id", "course_id", "created_at", "id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_ai_usage_logs_success_created",
        "ai_usage_logs",
        ["success", "created_at"],
        unique=False,
        if_not_exists=True,
    )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        for table, name, _condition in _POSTGRESQL_CONSTRAINTS:
            op.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {name}")
        return

    with op.batch_alter_table("past_exam_questions", schema=None) as batch_op:
        batch_op.drop_constraint(
            batch_op.f("ck_past_exam_questions_page_requires_document"),
            type_="check",
        )
    with op.batch_alter_table("progress", schema=None) as batch_op:
        batch_op.drop_constraint(
            batch_op.f("ck_progress_total_questions_answered_nonnegative"),
            type_="check",
        )
        batch_op.drop_constraint(
            batch_op.f("ck_progress_incorrect_answers_count_nonnegative"),
            type_="check",
        )
        batch_op.drop_constraint(
            batch_op.f("ck_progress_correct_answers_count_nonnegative"),
            type_="check",
        )
        batch_op.drop_constraint(
            batch_op.f("ck_progress_quizzes_completed_nonnegative"),
            type_="check",
        )
    with op.batch_alter_table("quiz_attempt_answers", schema=None) as batch_op:
        batch_op.drop_constraint(
            batch_op.f("ck_quiz_attempt_answers_answer_time_spent_nonnegative"),
            type_="check",
        )
    with op.batch_alter_table("quiz_questions", schema=None) as batch_op:
        batch_op.drop_constraint(
            batch_op.f("ck_quiz_questions_quiz_question_type_valid"),
            type_="check",
        )
