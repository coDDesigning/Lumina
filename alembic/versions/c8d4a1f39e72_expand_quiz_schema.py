"""expand quiz schema for all question types and grading

Revision ID: c8d4a1f39e72
Revises: b2f47c8d0915
Create Date: 2026-08-20 16:00:00.000000

The upgrade is additive except for three relaxations that make the non
multiple-choice question types representable: ``quiz_questions.options`` and
``quiz_questions.correct_option_index`` become nullable, and
``quiz_attempt_answers.is_correct`` becomes nullable so an open-ended answer the
grader could not score is recorded as ungraded rather than as wrong.

The downgrade is deliberately not lossless. Restoring the NOT NULL constraints
requires a value for rows that legitimately have none, so short-answer and
open-ended questions are backfilled with an empty option list and index 0. Their
``correct_answer`` document is dropped with the column, so the answer itself does
not survive a downgrade.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c8d4a1f39e72"
down_revision: str | Sequence[str] | None = "b2f47c8d0915"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


MULTIPLE_CHOICE = "multiple_choice"

QUESTION_INDEX_CHECK = (
    "question_index = CAST(question_index AS INTEGER) AND question_index >= 0"
)
CORRECT_OPTION_INDEX_CHECK_STRICT = (
    "correct_option_index = CAST(correct_option_index AS INTEGER) "
    "AND correct_option_index >= 0"
)
CORRECT_OPTION_INDEX_CHECK_NULLABLE = (
    "correct_option_index IS NULL OR "
    "(correct_option_index = CAST(correct_option_index AS INTEGER) "
    "AND correct_option_index >= 0)"
)
SELECTED_OPTION_INDEX_CHECK = (
    "selected_option_index IS NULL OR selected_option_index >= 0"
)
ANSWER_SCORE_CHECK = "score IS NULL OR (score >= 0 AND score <= 1)"


def _questions() -> sa.Table:
    """A minimal ``quiz_questions`` for data statements.

    Typed columns rather than raw SQL: ``correct_answer`` is a JSON column, and
    on PostgreSQL a computed text expression cannot be assigned to one without
    an explicit cast that SQLite would then reject.
    """
    return sa.table(
        "quiz_questions",
        sa.column("id", sa.Integer),
        sa.column("options", sa.JSON),
        sa.column("correct_option_index", sa.Integer),
        sa.column("correct_answer", sa.JSON),
    )


def _attempt_answers() -> sa.Table:
    return sa.table(
        "quiz_attempt_answers",
        sa.column("is_correct", sa.Boolean),
    )


def _quizzes_table(*, expanded: bool) -> sa.Table:
    """Describe ``quizzes`` for SQLite batch alter.

    ``expanded`` selects the post-upgrade shape, which the downgrade copies from.
    """
    meta = sa.MetaData()
    columns: list[sa.Column] = [
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("course_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
    ]
    constraints: list = [
        sa.ForeignKeyConstraint(
            ["course_id"],
            ["courses.id"],
            name="fk_quizzes_course_id_courses",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_quizzes"),
        sa.Index("ix_quizzes_course_id", "course_id"),
    ]
    if expanded:
        columns += [
            sa.Column("user_id", sa.Integer(), nullable=True),
            sa.Column("model_used", sa.String(length=150), nullable=True),
            sa.Column("generation_settings", sa.Text(), nullable=True),
            sa.Column("generation_context", sa.Text(), nullable=True),
        ]
        constraints += [
            sa.ForeignKeyConstraint(
                ["user_id"],
                ["users.id"],
                name="fk_quizzes_user_id_users",
                ondelete="SET NULL",
            ),
            sa.Index("ix_quizzes_user_id", "user_id"),
        ]

    return sa.Table("quizzes", meta, *columns, *constraints)


def _quiz_questions_table(*, expanded: bool) -> sa.Table:
    """Describe ``quiz_questions`` for SQLite batch alter.

    ``expanded`` selects the post-upgrade shape, which the downgrade copies from.
    """
    meta = sa.MetaData()
    columns: list[sa.Column] = [
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("quiz_id", sa.Integer(), nullable=False),
        sa.Column("question_index", sa.Integer(), nullable=False),
        sa.Column("question_text", sa.Text(), nullable=False),
        sa.Column("options", sa.JSON(), nullable=expanded),
        sa.Column("correct_option_index", sa.Integer(), nullable=expanded),
        sa.Column("topic", sa.String(length=200), nullable=True),
        sa.Column("explanation", sa.Text(), nullable=True),
    ]
    if expanded:
        columns += [
            sa.Column(
                "question_type",
                sa.String(length=20),
                nullable=False,
                server_default=MULTIPLE_CHOICE,
            ),
            sa.Column("difficulty", sa.String(length=10), nullable=True),
            sa.Column("correct_answer", sa.JSON(), nullable=True),
        ]

    return sa.Table(
        "quiz_questions",
        meta,
        *columns,
        sa.ForeignKeyConstraint(
            ["quiz_id"],
            ["quizzes.id"],
            name="fk_quiz_questions_quiz_id_quizzes",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_quiz_questions"),
        sa.UniqueConstraint("quiz_id", "question_index", name="uq_question_quiz_index"),
        sa.CheckConstraint(
            QUESTION_INDEX_CHECK,
            name="ck_quiz_questions_question_index_nonnegative",
        ),
        sa.CheckConstraint(
            CORRECT_OPTION_INDEX_CHECK_NULLABLE
            if expanded
            else CORRECT_OPTION_INDEX_CHECK_STRICT,
            name="ck_quiz_questions_correct_option_index_nonnegative",
        ),
        sa.Index("ix_quiz_questions_quiz_id", "quiz_id"),
    )


def _quiz_attempt_answers_table(*, expanded: bool) -> sa.Table:
    """Describe ``quiz_attempt_answers`` for SQLite batch alter."""
    meta = sa.MetaData()
    columns: list[sa.Column] = [
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("attempt_id", sa.Integer(), nullable=False),
        sa.Column("quiz_question_id", sa.Integer(), nullable=False),
        sa.Column("selected_option_index", sa.Integer(), nullable=True),
        sa.Column("is_correct", sa.Boolean(), nullable=expanded),
    ]
    constraints: list = [
        sa.CheckConstraint(
            SELECTED_OPTION_INDEX_CHECK,
            name="ck_quiz_attempt_answers_selected_option_index_nonnegative",
        ),
    ]
    if expanded:
        columns += [
            sa.Column("answer_text", sa.Text(), nullable=True),
            sa.Column("score", sa.Float(), nullable=True),
            sa.Column("feedback", sa.Text(), nullable=True),
        ]
        constraints.append(
            sa.CheckConstraint(
                ANSWER_SCORE_CHECK,
                name="ck_quiz_attempt_answers_score_fraction",
            )
        )

    return sa.Table(
        "quiz_attempt_answers",
        meta,
        *columns,
        *constraints,
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
            "attempt_id", "quiz_question_id", name="uq_attempt_answer_question"
        ),
        sa.Index("ix_quiz_attempt_answers_attempt_id", "attempt_id"),
        sa.Index("ix_quiz_attempt_answers_quiz_question_id", "quiz_question_id"),
    )


def _backfill_correct_answer() -> None:
    """Give every pre-existing question the answer document its index encodes.

    Every row that exists before this revision is multiple choice by definition,
    so its ``correct_option_index`` is the whole truth about its answer. There
    are only ever a handful of distinct indexes, so one statement per index
    backfills the whole table without loading it.
    """
    questions = _questions()
    bind = op.get_bind()

    pending = (
        questions.c.correct_answer.is_(None),
        questions.c.correct_option_index.is_not(None),
    )
    indexes = (
        bind.execute(
            sa.select(questions.c.correct_option_index).where(*pending).distinct()
        )
        .scalars()
        .all()
    )

    for option_index in indexes:
        bind.execute(
            questions.update()
            .where(*pending, questions.c.correct_option_index == option_index)
            .values(
                correct_answer={
                    "type": MULTIPLE_CHOICE,
                    "option_index": option_index,
                }
            )
        )


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()

    if bind.dialect.name == "postgresql":
        op.add_column("quizzes", sa.Column("user_id", sa.Integer(), nullable=True))
        op.create_foreign_key(
            op.f("fk_quizzes_user_id_users"),
            "quizzes",
            "users",
            ["user_id"],
            ["id"],
            ondelete="SET NULL",
        )
        op.create_index(
            op.f("ix_quizzes_user_id"), "quizzes", ["user_id"], unique=False
        )
        op.add_column(
            "quizzes", sa.Column("model_used", sa.String(length=150), nullable=True)
        )
        op.add_column(
            "quizzes", sa.Column("generation_settings", sa.Text(), nullable=True)
        )
        op.add_column(
            "quizzes", sa.Column("generation_context", sa.Text(), nullable=True)
        )

        op.add_column(
            "quiz_questions",
            sa.Column(
                "question_type",
                sa.String(length=20),
                nullable=False,
                server_default=MULTIPLE_CHOICE,
            ),
        )
        op.add_column(
            "quiz_questions",
            sa.Column("difficulty", sa.String(length=10), nullable=True),
        )
        op.add_column(
            "quiz_questions", sa.Column("correct_answer", sa.JSON(), nullable=True)
        )
        op.alter_column(
            "quiz_questions", "options", existing_type=sa.JSON(), nullable=True
        )
        op.alter_column(
            "quiz_questions",
            "correct_option_index",
            existing_type=sa.Integer(),
            nullable=True,
        )
        op.drop_constraint(
            op.f("ck_quiz_questions_correct_option_index_nonnegative"),
            "quiz_questions",
            type_="check",
        )
        op.create_check_constraint(
            op.f("ck_quiz_questions_correct_option_index_nonnegative"),
            "quiz_questions",
            CORRECT_OPTION_INDEX_CHECK_NULLABLE,
        )

        op.add_column(
            "quiz_attempt_answers", sa.Column("answer_text", sa.Text(), nullable=True)
        )
        op.add_column(
            "quiz_attempt_answers", sa.Column("score", sa.Float(), nullable=True)
        )
        op.add_column(
            "quiz_attempt_answers", sa.Column("feedback", sa.Text(), nullable=True)
        )
        op.alter_column(
            "quiz_attempt_answers",
            "is_correct",
            existing_type=sa.Boolean(),
            nullable=True,
        )
        op.create_check_constraint(
            op.f("ck_quiz_attempt_answers_score_fraction"),
            "quiz_attempt_answers",
            ANSWER_SCORE_CHECK,
        )

        _backfill_correct_answer()
        return

    with op.batch_alter_table(
        "quizzes", schema=None, copy_from=_quizzes_table(expanded=False)
    ) as batch_op:
        batch_op.add_column(sa.Column("user_id", sa.Integer(), nullable=True))
        batch_op.add_column(
            sa.Column("model_used", sa.String(length=150), nullable=True)
        )
        batch_op.add_column(sa.Column("generation_settings", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("generation_context", sa.Text(), nullable=True))
        batch_op.create_foreign_key(
            batch_op.f("fk_quizzes_user_id_users"),
            "users",
            ["user_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_index(
            batch_op.f("ix_quizzes_user_id"), ["user_id"], unique=False
        )

    with op.batch_alter_table(
        "quiz_questions",
        schema=None,
        copy_from=_quiz_questions_table(expanded=False),
    ) as batch_op:
        batch_op.add_column(
            sa.Column(
                "question_type",
                sa.String(length=20),
                nullable=False,
                server_default=MULTIPLE_CHOICE,
            )
        )
        batch_op.add_column(
            sa.Column("difficulty", sa.String(length=10), nullable=True)
        )
        batch_op.add_column(sa.Column("correct_answer", sa.JSON(), nullable=True))
        batch_op.alter_column("options", existing_type=sa.JSON(), nullable=True)
        batch_op.alter_column(
            "correct_option_index", existing_type=sa.Integer(), nullable=True
        )
        batch_op.drop_constraint(
            batch_op.f("ck_quiz_questions_correct_option_index_nonnegative"),
            type_="check",
        )
        batch_op.create_check_constraint(
            batch_op.f("ck_quiz_questions_correct_option_index_nonnegative"),
            CORRECT_OPTION_INDEX_CHECK_NULLABLE,
        )

    with op.batch_alter_table(
        "quiz_attempt_answers",
        schema=None,
        copy_from=_quiz_attempt_answers_table(expanded=False),
    ) as batch_op:
        batch_op.add_column(sa.Column("answer_text", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("score", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("feedback", sa.Text(), nullable=True))
        batch_op.alter_column("is_correct", existing_type=sa.Boolean(), nullable=True)
        batch_op.create_check_constraint(
            batch_op.f("ck_quiz_attempt_answers_score_fraction"), ANSWER_SCORE_CHECK
        )

    _backfill_correct_answer()


def downgrade() -> None:
    """Downgrade schema.

    Not lossless: questions that carry no option list are given an empty one so
    the restored NOT NULL constraints hold, and every ``correct_answer``
    document is dropped with its column.
    """
    questions = _questions()
    answers = _attempt_answers()
    bind = op.get_bind()

    bind.execute(
        questions.update().where(questions.c.options.is_(None)).values(options=[])
    )
    bind.execute(
        questions.update()
        .where(questions.c.correct_option_index.is_(None))
        .values(correct_option_index=0)
    )
    bind.execute(
        answers.update().where(answers.c.is_correct.is_(None)).values(is_correct=False)
    )

    bind = op.get_bind()

    if bind.dialect.name == "postgresql":
        op.drop_constraint(
            op.f("ck_quiz_attempt_answers_score_fraction"),
            "quiz_attempt_answers",
            type_="check",
        )
        op.alter_column(
            "quiz_attempt_answers",
            "is_correct",
            existing_type=sa.Boolean(),
            nullable=False,
        )
        op.drop_column("quiz_attempt_answers", "feedback")
        op.drop_column("quiz_attempt_answers", "score")
        op.drop_column("quiz_attempt_answers", "answer_text")

        op.drop_constraint(
            op.f("ck_quiz_questions_correct_option_index_nonnegative"),
            "quiz_questions",
            type_="check",
        )
        op.create_check_constraint(
            op.f("ck_quiz_questions_correct_option_index_nonnegative"),
            "quiz_questions",
            CORRECT_OPTION_INDEX_CHECK_STRICT,
        )
        op.alter_column(
            "quiz_questions",
            "correct_option_index",
            existing_type=sa.Integer(),
            nullable=False,
        )
        op.alter_column(
            "quiz_questions", "options", existing_type=sa.JSON(), nullable=False
        )
        op.drop_column("quiz_questions", "correct_answer")
        op.drop_column("quiz_questions", "difficulty")
        op.drop_column("quiz_questions", "question_type")

        op.drop_index(op.f("ix_quizzes_user_id"), table_name="quizzes")
        op.drop_constraint(
            op.f("fk_quizzes_user_id_users"), "quizzes", type_="foreignkey"
        )
        op.drop_column("quizzes", "generation_context")
        op.drop_column("quizzes", "generation_settings")
        op.drop_column("quizzes", "model_used")
        op.drop_column("quizzes", "user_id")
        return

    with op.batch_alter_table(
        "quiz_attempt_answers",
        schema=None,
        copy_from=_quiz_attempt_answers_table(expanded=True),
    ) as batch_op:
        batch_op.drop_constraint(
            batch_op.f("ck_quiz_attempt_answers_score_fraction"), type_="check"
        )
        batch_op.alter_column("is_correct", existing_type=sa.Boolean(), nullable=False)
        batch_op.drop_column("feedback")
        batch_op.drop_column("score")
        batch_op.drop_column("answer_text")

    with op.batch_alter_table(
        "quiz_questions",
        schema=None,
        copy_from=_quiz_questions_table(expanded=True),
    ) as batch_op:
        batch_op.drop_constraint(
            batch_op.f("ck_quiz_questions_correct_option_index_nonnegative"),
            type_="check",
        )
        batch_op.create_check_constraint(
            batch_op.f("ck_quiz_questions_correct_option_index_nonnegative"),
            CORRECT_OPTION_INDEX_CHECK_STRICT,
        )
        batch_op.alter_column("options", existing_type=sa.JSON(), nullable=False)
        batch_op.alter_column(
            "correct_option_index", existing_type=sa.Integer(), nullable=False
        )
        batch_op.drop_column("correct_answer")
        batch_op.drop_column("difficulty")
        batch_op.drop_column("question_type")

    with op.batch_alter_table(
        "quizzes", schema=None, copy_from=_quizzes_table(expanded=True)
    ) as batch_op:
        batch_op.drop_index(batch_op.f("ix_quizzes_user_id"))
        batch_op.drop_column("generation_context")
        batch_op.drop_column("generation_settings")
        batch_op.drop_column("model_used")
        batch_op.drop_column("user_id")
