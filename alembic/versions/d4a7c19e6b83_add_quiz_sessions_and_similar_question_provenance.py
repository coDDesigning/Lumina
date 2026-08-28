"""exam mode: timed quiz sessions, generation idempotency, and question provenance

Revision ID: d4a7c19e6b83
Revises: b5e9a2c7d341
Create Date: 2026-08-28 10:20:00.000000

A mock exam is a quiz sat against a clock, and a clock the candidate's browser
owns is not a clock. ``quiz_sessions`` is where the server keeps the one that
counts: it writes ``started_at`` and ``expires_at`` itself, tells the client the
deadline, and never accepts one back. Expiry is then a comparison against
``expires_at`` in whatever statement reads the row, the way
``processing_jobs.lease_expires_at`` already works, so nothing has to be
scheduled and a sitting can be reported as over before any write has said so.

``quiz_session_answers`` holds the drafts. They exist so that a deadline costs a
student the right to keep answering and nothing else: the answers saved before
it are never deleted, and submission after the deadline finalises exactly those.
Without them, the last request of an examination would be the only one that
mattered, and a slow network would be indistinguishable from a blank paper.

Two constraints carry the anti-double-submit rule. ``attempt_id`` is unique, and
``submitted_state_valid`` forbids a submitted row without one, so a sitting that
produced two attempts is not a state this schema can hold. The race between two
simultaneous submissions is won by a guarded update in the service; these
constraints are what stop a defect from recording the outcome twice anyway.

``uq_quiz_sessions_active_quiz_user`` is partial on ``status = 'active'``, which
both engines support natively. It means a reloaded page rejoins the timer it
already started instead of quietly opening a second one and splitting the drafts
between them, while finished sittings stay out of the way so retakes are free.

``quizzes.generation_request_id`` closes a different hole: a client that resent a
generation after a timeout used to pay twice. Its unique index is scoped to
(course, user, request) and is an index rather than a constraint so SQLite adds
it without rebuilding ``quizzes``. NULL is distinct on both engines, so every
existing row and every generation made without an identifier is unaffected and
nothing needs back-filling.

``quiz_questions.source_past_exam_question_id`` records which past exam question
a similar question was written in the mould of. Like ``exam_plan_output_id``
before it, it carries no foreign key: constraining a column on an existing table
forces a ``batch_alter_table`` rebuild on SQLite, and Alembic cannot express the
one ``ADD COLUMN ... REFERENCES`` statement SQLite would otherwise accept.

The absence is also the safer semantics. A cascade would delete a quiz a student
had already sat as soon as its source paper was removed, and extraction replaces
a paper's questions wholesale on every re-run, so the pointer is expected to stop
resolving. A pointer to a question that no longer exists means the original is
gone -- which a reader must handle regardless -- while the generated question
keeps its own denormalized citations, exactly as a citation outlives the document
it came from.

This revision is add-only: two ``CREATE TABLE``, three ``ADD COLUMN`` without a
default, and their indexes, all native on both supported engines. The only
dialect branch is the timezone flag on the timestamp columns. There is no data to
move, and the downgrade is a real inverse.
"""

import logging
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d4a7c19e6b83"
down_revision: str | Sequence[str] | None = "b5e9a2c7d341"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger("alembic.runtime.migration")

_ACTIVE_ONLY = sa.text("status = 'active'")


def upgrade() -> None:
    postgresql = op.get_bind().dialect.name == "postgresql"

    op.add_column(
        "quizzes", sa.Column("time_limit_seconds", sa.Integer(), nullable=True)
    )
    op.add_column(
        "quizzes",
        sa.Column("generation_request_id", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "uq_quizzes_course_id_user_id_generation_request_id",
        "quizzes",
        ["course_id", "user_id", "generation_request_id"],
        unique=True,
    )

    op.add_column(
        "quiz_questions",
        sa.Column("source_past_exam_question_id", sa.Integer(), nullable=True),
    )
    op.create_index(
        op.f("ix_quiz_questions_source_past_exam_question_id"),
        "quiz_questions",
        ["source_past_exam_question_id"],
    )

    op.create_table(
        "quiz_sessions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("quiz_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("attempt_id", sa.Integer(), nullable=True),
        sa.Column(
            "status", sa.String(length=20), server_default="active", nullable=False
        ),
        sa.Column("time_limit_seconds", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=postgresql), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=postgresql), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=postgresql), nullable=True),
        sa.Column("expired_at", sa.DateTime(timezone=postgresql), nullable=True),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_quiz_sessions")),
        sa.UniqueConstraint("attempt_id", name=op.f("uq_quiz_sessions_attempt_id")),
        sa.CheckConstraint(
            "status IN ('active', 'submitted', 'expired')",
            name=op.f("ck_quiz_sessions_status_valid"),
        ),
        sa.CheckConstraint(
            "time_limit_seconds > 0 AND time_limit_seconds <= 86400",
            name=op.f("ck_quiz_sessions_time_limit_seconds_bounded"),
        ),
        sa.CheckConstraint(
            "expires_at > started_at",
            name=op.f("ck_quiz_sessions_expires_after_start"),
        ),
        sa.CheckConstraint(
            "(status = 'submitted' AND submitted_at IS NOT NULL "
            "AND attempt_id IS NOT NULL) OR "
            "(status <> 'submitted' AND submitted_at IS NULL "
            "AND attempt_id IS NULL)",
            name=op.f("ck_quiz_sessions_submitted_state_valid"),
        ),
        sa.CheckConstraint(
            "(status = 'active' AND expired_at IS NULL) OR "
            "(status = 'expired' AND expired_at IS NOT NULL) OR "
            "status = 'submitted'",
            name=op.f("ck_quiz_sessions_expired_state_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["quiz_id"],
            ["quizzes.id"],
            name=op.f("fk_quiz_sessions_quiz_id_quizzes"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_quiz_sessions_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["attempt_id"],
            ["quiz_attempts.id"],
            name=op.f("fk_quiz_sessions_attempt_id_quiz_attempts"),
            ondelete="CASCADE",
        ),
    )
    op.create_index(op.f("ix_quiz_sessions_quiz_id"), "quiz_sessions", ["quiz_id"])
    op.create_index(op.f("ix_quiz_sessions_user_id"), "quiz_sessions", ["user_id"])
    op.create_index(
        "ix_quiz_sessions_expirable", "quiz_sessions", ["status", "expires_at", "id"]
    )
    op.create_index(
        "ix_quiz_sessions_user_quiz_started",
        "quiz_sessions",
        ["user_id", "quiz_id", "started_at"],
    )
    op.create_index(
        "uq_quiz_sessions_active_quiz_user",
        "quiz_sessions",
        ["quiz_id", "user_id"],
        unique=True,
        sqlite_where=_ACTIVE_ONLY,
        postgresql_where=_ACTIVE_ONLY,
    )

    op.create_table(
        "quiz_session_answers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.Integer(), nullable=False),
        sa.Column("quiz_question_id", sa.Integer(), nullable=False),
        sa.Column("selected_option_index", sa.Integer(), nullable=True),
        sa.Column("text_response", sa.Text(), nullable=True),
        sa.Column("time_spent_seconds", sa.Integer(), nullable=True),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_quiz_session_answers")),
        sa.UniqueConstraint(
            "session_id",
            "quiz_question_id",
            name=op.f("uq_quiz_session_answers_session_question"),
        ),
        sa.CheckConstraint(
            "selected_option_index IS NULL OR selected_option_index >= 0",
            name=op.f("ck_quiz_session_answers_selected_option_index_nonnegative"),
        ),
        sa.CheckConstraint(
            "time_spent_seconds IS NULL OR time_spent_seconds >= 0",
            name=op.f("ck_quiz_session_answers_answer_time_spent_nonnegative"),
        ),
        sa.CheckConstraint(
            "NOT (selected_option_index IS NOT NULL AND text_response IS NOT NULL)",
            name=op.f("ck_quiz_session_answers_answer_form_exclusive"),
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["quiz_sessions.id"],
            name=op.f("fk_quiz_session_answers_session_id_quiz_sessions"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["quiz_question_id"],
            ["quiz_questions.id"],
            name=op.f("fk_quiz_session_answers_quiz_question_id_quiz_questions"),
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        op.f("ix_quiz_session_answers_session_id"),
        "quiz_session_answers",
        ["session_id"],
    )
    op.create_index(
        op.f("ix_quiz_session_answers_quiz_question_id"),
        "quiz_session_answers",
        ["quiz_question_id"],
    )

    logger.info("Timed quiz sessions, generation idempotency, and provenance added")


def downgrade() -> None:
    op.drop_index(
        op.f("ix_quiz_session_answers_quiz_question_id"),
        table_name="quiz_session_answers",
    )
    op.drop_index(
        op.f("ix_quiz_session_answers_session_id"), table_name="quiz_session_answers"
    )
    op.drop_table("quiz_session_answers")

    op.drop_index("uq_quiz_sessions_active_quiz_user", table_name="quiz_sessions")
    op.drop_index("ix_quiz_sessions_user_quiz_started", table_name="quiz_sessions")
    op.drop_index("ix_quiz_sessions_expirable", table_name="quiz_sessions")
    op.drop_index(op.f("ix_quiz_sessions_user_id"), table_name="quiz_sessions")
    op.drop_index(op.f("ix_quiz_sessions_quiz_id"), table_name="quiz_sessions")
    op.drop_table("quiz_sessions")

    op.drop_index(
        op.f("ix_quiz_questions_source_past_exam_question_id"),
        table_name="quiz_questions",
    )
    op.drop_column("quiz_questions", "source_past_exam_question_id")

    op.drop_index(
        "uq_quizzes_course_id_user_id_generation_request_id", table_name="quizzes"
    )
    op.drop_column("quizzes", "generation_request_id")
    op.drop_column("quizzes", "time_limit_seconds")
