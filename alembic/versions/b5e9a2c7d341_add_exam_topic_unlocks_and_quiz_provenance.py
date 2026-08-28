"""exam mode: per-topic unlocks and quiz provenance

Revision ID: b5e9a2c7d341
Revises: a6d3f81c9b47
Create Date: 2026-08-27 18:10:00.000000

Exam Mode charges per topic rather than per artifact. A student who unlocks
"Graph Traversal" gets its guide, its summary, its practice questions, its
topic exam, and its similar questions for one price, and pays it the first
time they ask for any of them rather than up front for a plan they may not
finish. ``exam_topic_unlocks`` is the record of that payment: one row per
(course, student, topic), which is why the unique key is exactly those three
columns and why regenerating a plan over the same topics costs nothing.

``credit_transaction_id`` is nullable because an unmetered account pays
nothing and therefore has no ledger row to point at. A null there means "no
credit moved", not "the row is unfinished"; ``ChargeReceipt.is_exempt`` is the
same distinction one layer up.

The three columns added to ``quizzes`` are provenance. Exam Mode's practice
questions, topic exams, and mock exams are real quizzes -- they must be, or
attempts, grading, mastery, and course progress would all need parallel
implementations -- so the question is only how to tell them apart afterwards.
``purpose`` is that discriminator, ``exam_plan_output_id`` names the plan a
quiz was generated for, and ``exam_topic_key`` names the topic, so "the exams
belonging to this plan" and "this topic's practice" are plain SQL rather than
a scan through generation settings JSON.

None of the three carries a ``CHECK`` or a foreign key. Constraining a column
on an existing table would force a ``batch_alter_table`` rebuild of
``quizzes`` on SQLite, and ``generated_outputs.output_type`` is the standing
precedent for a discriminator the application owns. A null ``purpose`` is a
quiz that predates Exam Mode, which is the truth for every existing row and
the reason the column is nullable rather than defaulted: back-filling
``'practice'`` would assert something about rows nobody classified.

This revision is add-only. It creates one table, three indexes on it, and adds
three nullable columns with three indexes to ``quizzes``: ``CREATE TABLE``,
``CREATE INDEX``, and ``ADD COLUMN`` without a default are native on both
supported engines, so no ``batch_alter_table`` is needed. The only dialect
branch is the timezone flag on the timestamp column. There is no data to move,
and the downgrade is a real inverse.
"""

import logging
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b5e9a2c7d341"
down_revision: str | Sequence[str] | None = "a6d3f81c9b47"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger("alembic.runtime.migration")

_ASCII_WHITESPACE = " \t\n\r\v\f"


def upgrade() -> None:
    postgresql = op.get_bind().dialect.name == "postgresql"

    op.create_table(
        "exam_topic_unlocks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("course_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("topic_key", sa.String(length=120), nullable=False),
        sa.Column("credit_transaction_id", sa.Integer(), nullable=True),
        sa.Column("amount", sa.Float(), server_default="0", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=postgresql),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_exam_topic_unlocks")),
        sa.UniqueConstraint(
            "course_id",
            "user_id",
            "topic_key",
            name=op.f("uq_exam_topic_unlocks_course_id_user_id_topic_key"),
        ),
        sa.CheckConstraint(
            f"length(trim(topic_key, '{_ASCII_WHITESPACE}')) > 0",
            name=op.f("ck_exam_topic_unlocks_topic_key_nonblank"),
        ),
        sa.CheckConstraint(
            "amount >= 0", name=op.f("ck_exam_topic_unlocks_amount_nonnegative")
        ),
        sa.ForeignKeyConstraint(
            ["course_id"],
            ["courses.id"],
            name=op.f("fk_exam_topic_unlocks_course_id_courses"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_exam_topic_unlocks_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["credit_transaction_id"],
            ["credit_transactions.id"],
            name=op.f("fk_exam_topic_unlocks_credit_transaction_id"),
            ondelete="SET NULL",
        ),
    )
    op.create_index(
        op.f("ix_exam_topic_unlocks_course_id"), "exam_topic_unlocks", ["course_id"]
    )
    op.create_index(
        op.f("ix_exam_topic_unlocks_user_id"), "exam_topic_unlocks", ["user_id"]
    )
    op.create_index(
        op.f("ix_exam_topic_unlocks_topic_key"), "exam_topic_unlocks", ["topic_key"]
    )

    op.add_column("quizzes", sa.Column("purpose", sa.String(length=30), nullable=True))
    op.add_column(
        "quizzes", sa.Column("exam_plan_output_id", sa.Integer(), nullable=True)
    )
    op.add_column(
        "quizzes", sa.Column("exam_topic_key", sa.String(length=120), nullable=True)
    )
    op.create_index(op.f("ix_quizzes_purpose"), "quizzes", ["purpose"])
    op.create_index(
        op.f("ix_quizzes_exam_plan_output_id"), "quizzes", ["exam_plan_output_id"]
    )
    op.create_index(op.f("ix_quizzes_exam_topic_key"), "quizzes", ["exam_topic_key"])

    logger.info("Exam Mode topic unlocks and quiz provenance added")


def downgrade() -> None:
    op.drop_index(op.f("ix_quizzes_exam_topic_key"), table_name="quizzes")
    op.drop_index(op.f("ix_quizzes_exam_plan_output_id"), table_name="quizzes")
    op.drop_index(op.f("ix_quizzes_purpose"), table_name="quizzes")
    op.drop_column("quizzes", "exam_topic_key")
    op.drop_column("quizzes", "exam_plan_output_id")
    op.drop_column("quizzes", "purpose")

    op.drop_index(
        op.f("ix_exam_topic_unlocks_topic_key"), table_name="exam_topic_unlocks"
    )
    op.drop_index(
        op.f("ix_exam_topic_unlocks_user_id"), table_name="exam_topic_unlocks"
    )
    op.drop_index(
        op.f("ix_exam_topic_unlocks_course_id"), table_name="exam_topic_unlocks"
    )
    op.drop_table("exam_topic_unlocks")
