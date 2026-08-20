"""expand quiz schema for generation attribution and grading

Revision ID: c8d4a1f39e72
Revises: c8e1f5a9b3d2
Create Date: 2026-08-20 16:00:00.000000

``c8e1f5a9b3d2`` already made the non multiple-choice question types
representable: it added ``quiz_questions.question_type`` and relaxed
``options``, ``correct_option_index``, and ``quiz_attempt_answers.is_correct``
to nullable. This revision only adds what generating and grading those
questions needs on top of that.

``quiz_questions`` and ``quiz_attempt_answers`` are grown with plain
``ADD COLUMN`` rather than a batch copy. That is deliberate: on SQLite a batch
alter recreates the table from reflection, and reflection of
``quiz_attempt_answers`` already round-trips its check constraint name
incorrectly, so recreating either table would compound existing damage rather
than repair it. ``quizzes`` does need a batch copy, because its new ``user_id``
carries a foreign key and SQLite cannot add one with ``ALTER TABLE``.

The downgrade drops the added columns. It is lossy by nature -- the stored
answer documents, difficulty, grades, and generation attribution do not survive
it -- but it fabricates no values, because the nullability this feature relies
on is owned by the parent revision.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c8d4a1f39e72"
down_revision: str | Sequence[str] | None = "c8e1f5a9b3d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


MULTIPLE_CHOICE = "multiple_choice"


def _quizzes_table(*, upgraded: bool) -> sa.Table:
    """Describe ``quizzes`` for SQLite batch alter, before or after this revision."""
    meta = sa.MetaData()
    columns: list[sa.schema.SchemaItem] = [
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
    if upgraded:
        columns += [
            sa.Column("user_id", sa.Integer(), nullable=True),
            sa.Column("model_used", sa.String(length=150), nullable=True),
            sa.Column("generation_settings", sa.Text(), nullable=True),
            sa.Column("generation_context", sa.Text(), nullable=True),
        ]
    columns += [
        sa.ForeignKeyConstraint(
            ["course_id"],
            ["courses.id"],
            name="fk_quizzes_course_id_courses",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_quizzes"),
        sa.Index("ix_quizzes_course_id", "course_id"),
    ]
    if upgraded:
        # ``ix_quizzes_user_id`` is deliberately absent: the downgrade drops it
        # before the batch copy, so declaring it here would make batch mode try
        # to rebuild an index over a column it is in the middle of removing.
        columns += [
            sa.ForeignKeyConstraint(
                ["user_id"],
                ["users.id"],
                name="fk_quizzes_user_id_users",
                ondelete="SET NULL",
            ),
        ]
    return sa.Table("quizzes", meta, *columns)


def _backfill_correct_answer() -> None:
    """Give every pre-existing question the answer document its type implies.

    Rows written before this revision are all multiple choice, and their answer
    already lives in ``correct_option_index``. Rebuilding it as the discriminated
    document the application now reads keeps history gradable instead of leaving
    a null the grader would otherwise have to treat as unanswerable.
    """
    document = (
        '\'{"type": "multiple_choice", "option_index": \' '
        "|| correct_option_index || '}'"
    )
    # Postgres has no implicit assignment cast from text to json.
    value = (
        f"CAST({document} AS JSON)"
        if op.get_bind().dialect.name == "postgresql"
        else document
    )
    op.execute(
        sa.text(
            f"UPDATE quiz_questions SET correct_answer = {value} "
            "WHERE correct_answer IS NULL "
            "AND correct_option_index IS NOT NULL "
            f"AND question_type = '{MULTIPLE_CHOICE}'"
        )
    )


def upgrade() -> None:
    """Upgrade schema."""
    if op.get_bind().dialect.name == "postgresql":
        op.add_column("quizzes", sa.Column("user_id", sa.Integer(), nullable=True))
        op.create_foreign_key(
            op.f("fk_quizzes_user_id_users"),
            "quizzes",
            "users",
            ["user_id"],
            ["id"],
            ondelete="SET NULL",
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
        op.create_index(
            op.f("ix_quizzes_user_id"), "quizzes", ["user_id"], unique=False
        )
    else:
        with op.batch_alter_table(
            "quizzes", schema=None, copy_from=_quizzes_table(upgraded=False)
        ) as batch_op:
            batch_op.add_column(sa.Column("user_id", sa.Integer(), nullable=True))
            batch_op.add_column(
                sa.Column("model_used", sa.String(length=150), nullable=True)
            )
            batch_op.add_column(
                sa.Column("generation_settings", sa.Text(), nullable=True)
            )
            batch_op.add_column(
                sa.Column("generation_context", sa.Text(), nullable=True)
            )
            batch_op.create_foreign_key(
                batch_op.f("fk_quizzes_user_id_users"),
                "users",
                ["user_id"],
                ["id"],
                ondelete="SET NULL",
            )
        op.create_index(
            op.f("ix_quizzes_user_id"), "quizzes", ["user_id"], unique=False
        )

    op.add_column(
        "quiz_questions", sa.Column("difficulty", sa.String(length=10), nullable=True)
    )
    op.add_column(
        "quiz_questions", sa.Column("correct_answer", sa.JSON(), nullable=True)
    )
    _backfill_correct_answer()

    op.add_column("quiz_attempt_answers", sa.Column("score", sa.Float(), nullable=True))
    op.add_column(
        "quiz_attempt_answers", sa.Column("feedback", sa.Text(), nullable=True)
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("quiz_attempt_answers", "feedback")
    op.drop_column("quiz_attempt_answers", "score")

    op.drop_column("quiz_questions", "correct_answer")
    op.drop_column("quiz_questions", "difficulty")

    op.drop_index(op.f("ix_quizzes_user_id"), table_name="quizzes")
    if op.get_bind().dialect.name == "postgresql":
        op.drop_constraint(
            op.f("fk_quizzes_user_id_users"), "quizzes", type_="foreignkey"
        )
        op.drop_column("quizzes", "generation_context")
        op.drop_column("quizzes", "generation_settings")
        op.drop_column("quizzes", "model_used")
        op.drop_column("quizzes", "user_id")
        return

    with op.batch_alter_table(
        "quizzes", schema=None, copy_from=_quizzes_table(upgraded=True)
    ) as batch_op:
        batch_op.drop_constraint(
            batch_op.f("fk_quizzes_user_id_users"), type_="foreignkey"
        )
        batch_op.drop_column("generation_context")
        batch_op.drop_column("generation_settings")
        batch_op.drop_column("model_used")
        batch_op.drop_column("user_id")
