"""allow flashcard generation jobs

Revision ID: 9f4c1d7b2e83
Revises: ebccfdeadee4
Create Date: 2026-08-30 19:25:00.000000

``ebccfdeadee4`` shipped before flashcards were backgrounded, so its job_type
CHECK admits study guides and quizzes only, and it names no result shape for a
flashcard. Every database already stamped with that revision therefore rejects a
queued flashcard, and Alembic will never re-run a revision to teach it one: the
constraints are replaced by this descendant rather than by editing the published
script.

The rebuild is driven from ``copy_from`` rather than reflection because SQLite
recreates the whole table to change one CHECK, and this table carries fifteen of
them plus six indexes that a reflected definition must not be trusted to carry
across. The table below is the frozen ``ebccfdeadee4`` shape; it is deliberately
not read from the models, which move on.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

import backend


revision: str = "9f4c1d7b2e83"
down_revision: str | Sequence[str] | None = "ebccfdeadee4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JOB_TYPE_CHECK = "ck_generation_jobs_job_type_valid"
FLASHCARD_SHAPE_CHECK = "ck_generation_jobs_flashcard_result_shape"


def _generation_jobs_table() -> sa.Table:
    """The table exactly as ``ebccfdeadee4`` created it."""
    return sa.Table(
        "generation_jobs",
        sa.MetaData(),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("course_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("job_type", sa.String(length=50), nullable=False),
        sa.Column("correlation_id", sa.String(length=64), nullable=True),
        sa.Column("request_payload", sa.Text(), nullable=False),
        sa.Column(
            "status", sa.String(length=20), server_default="queued", nullable=False
        ),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("available_at", backend.app.models.UTCDateTime(), nullable=False),
        sa.Column("started_at", backend.app.models.UTCDateTime(), nullable=True),
        sa.Column("claimed_at", backend.app.models.UTCDateTime(), nullable=True),
        sa.Column("heartbeat_at", backend.app.models.UTCDateTime(), nullable=True),
        sa.Column("lease_expires_at", backend.app.models.UTCDateTime(), nullable=True),
        sa.Column("lease_owner", sa.String(length=255), nullable=True),
        sa.Column("claim_token", sa.String(length=36), nullable=True),
        sa.Column("finished_at", backend.app.models.UTCDateTime(), nullable=True),
        sa.Column("last_error_code", sa.String(length=100), nullable=True),
        sa.Column("last_error_message", sa.Text(), nullable=True),
        sa.Column("charge_amount", sa.Float(), nullable=True),
        sa.Column("charge_transaction_id", sa.Integer(), nullable=True),
        sa.Column(
            "charge_refunded", sa.Boolean(), server_default=sa.false(), nullable=False
        ),
        sa.Column("generated_output_id", sa.Integer(), nullable=True),
        sa.Column("quiz_id", sa.Integer(), nullable=True),
        sa.Column("retry_of_job_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            backend.app.models.UTCDateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            backend.app.models.UTCDateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(status = 'running' AND attempt_count > 0 AND lease_owner IS NOT NULL AND claim_token IS NOT NULL AND claimed_at IS NOT NULL AND heartbeat_at IS NOT NULL AND lease_expires_at IS NOT NULL AND heartbeat_at >= claimed_at AND lease_expires_at > heartbeat_at AND finished_at IS NULL) OR (status <> 'running' AND lease_owner IS NULL AND claim_token IS NULL AND claimed_at IS NULL AND heartbeat_at IS NULL AND lease_expires_at IS NULL)",
            name=op.f("ck_generation_jobs_lease_state_valid"),
        ),
        sa.CheckConstraint(
            "(status IN ('succeeded', 'failed') AND finished_at IS NOT NULL) OR (status IN ('queued', 'running') AND finished_at IS NULL)",
            name=op.f("ck_generation_jobs_finished_state_valid"),
        ),
        sa.CheckConstraint(
            "job_type <> 'generate_quiz' OR generated_output_id IS NULL",
            name=op.f("ck_generation_jobs_quiz_result_shape"),
        ),
        sa.CheckConstraint(
            "job_type <> 'generate_study_guide' OR quiz_id IS NULL",
            name=op.f("ck_generation_jobs_study_guide_result_shape"),
        ),
        sa.CheckConstraint(
            "job_type IN ('generate_study_guide', 'generate_quiz')",
            name=op.f(JOB_TYPE_CHECK),
        ),
        sa.CheckConstraint(
            "status <> 'failed' OR (last_error_code IS NOT NULL AND length(trim(last_error_code, ' \t\n\r\x0b\x0c')) > 0)",
            name=op.f("ck_generation_jobs_failed_error_code_nonblank"),
        ),
        sa.CheckConstraint(
            "status <> 'queued' OR attempt_count < max_attempts",
            name=op.f("ck_generation_jobs_queued_attempts_available"),
        ),
        sa.CheckConstraint(
            "status <> 'running' OR (claim_token IS NOT NULL AND length(claim_token) = 36)",
            name=op.f("ck_generation_jobs_running_claim_token_length"),
        ),
        sa.CheckConstraint(
            "status <> 'running' OR (lease_owner IS NOT NULL AND length(trim(lease_owner, ' \t\n\r\x0b\x0c')) > 0)",
            name=op.f("ck_generation_jobs_running_lease_owner_nonblank"),
        ),
        sa.CheckConstraint(
            "status <> 'succeeded' OR generated_output_id IS NOT NULL OR quiz_id IS NOT NULL",
            name=op.f("ck_generation_jobs_succeeded_has_result"),
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed')",
            name=op.f("ck_generation_jobs_status_valid"),
        ),
        sa.CheckConstraint(
            "attempt_count <= max_attempts",
            name=op.f("ck_generation_jobs_attempt_count_within_limit"),
        ),
        sa.CheckConstraint(
            "attempt_count >= 0",
            name=op.f("ck_generation_jobs_attempt_count_nonnegative"),
        ),
        sa.CheckConstraint(
            "charge_amount IS NULL OR charge_amount > 0",
            name=op.f("ck_generation_jobs_charge_amount_positive"),
        ),
        sa.CheckConstraint(
            "charge_refunded = false OR charge_amount IS NOT NULL",
            name=op.f("ck_generation_jobs_refund_needs_charge"),
        ),
        sa.CheckConstraint(
            "charge_transaction_id IS NULL OR charge_amount IS NOT NULL",
            name=op.f("ck_generation_jobs_charge_transaction_needs_amount"),
        ),
        sa.CheckConstraint(
            "max_attempts > 0", name=op.f("ck_generation_jobs_max_attempts_positive")
        ),
        sa.ForeignKeyConstraint(
            ["charge_transaction_id"],
            ["credit_transactions.id"],
            name=op.f("fk_generation_jobs_charge_transaction_id_credit_transactions"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["course_id"],
            ["courses.id"],
            name=op.f("fk_generation_jobs_course_id_courses"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["generated_output_id"],
            ["generated_outputs.id"],
            name=op.f("fk_generation_jobs_generated_output_id_generated_outputs"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["quiz_id"],
            ["quizzes.id"],
            name=op.f("fk_generation_jobs_quiz_id_quizzes"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["retry_of_job_id"],
            ["generation_jobs.id"],
            name=op.f("fk_generation_jobs_retry_of_job_id_generation_jobs"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_generation_jobs_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_generation_jobs")),
        sa.UniqueConstraint(
            "retry_of_job_id", name="uq_generation_jobs_retry_of_job_id"
        ),
        sa.Index("ix_generation_jobs_claimable", "status", "available_at", "id"),
        sa.Index("ix_generation_jobs_course_created", "course_id", "created_at", "id"),
        sa.Index(op.f("ix_generation_jobs_course_id"), "course_id"),
        sa.Index("ix_generation_jobs_recoverable", "status", "lease_expires_at", "id"),
        sa.Index(op.f("ix_generation_jobs_user_id"), "user_id"),
        sa.Index("ix_generation_jobs_user_status", "user_id", "status"),
    )


def upgrade() -> None:
    with op.batch_alter_table(
        "generation_jobs", schema=None, copy_from=_generation_jobs_table()
    ) as batch_op:
        batch_op.drop_constraint(op.f(JOB_TYPE_CHECK), type_="check")
        batch_op.create_check_constraint(
            op.f(JOB_TYPE_CHECK),
            "job_type IN ('generate_study_guide', 'generate_quiz', "
            "'generate_flashcard')",
        )
        # A flashcard deck is a generated output, so the row that records one
        # must leave quiz_id empty, exactly as a study guide does.
        batch_op.create_check_constraint(
            op.f(FLASHCARD_SHAPE_CHECK),
            "job_type <> 'generate_flashcard' OR quiz_id IS NULL",
        )


def downgrade() -> None:
    # The restored CHECK cannot hold a flashcard job, so the rows it forbids go
    # first. Their charges are already reconciled on the row itself, and a queue
    # entry is not the deck: a succeeded job's generated output survives this.
    op.execute(
        sa.text("DELETE FROM generation_jobs WHERE job_type = 'generate_flashcard'")
    )

    table = _generation_jobs_table()
    table.constraints = {
        constraint
        for constraint in table.constraints
        if constraint.name != JOB_TYPE_CHECK
    }
    table.append_constraint(
        sa.CheckConstraint(
            "job_type IN ('generate_study_guide', 'generate_quiz', "
            "'generate_flashcard')",
            name=op.f(JOB_TYPE_CHECK),
        )
    )
    table.append_constraint(
        sa.CheckConstraint(
            "job_type <> 'generate_flashcard' OR quiz_id IS NULL",
            name=op.f(FLASHCARD_SHAPE_CHECK),
        )
    )

    with op.batch_alter_table(
        "generation_jobs", schema=None, copy_from=table
    ) as batch_op:
        batch_op.drop_constraint(op.f(FLASHCARD_SHAPE_CHECK), type_="check")
        batch_op.drop_constraint(op.f(JOB_TYPE_CHECK), type_="check")
        batch_op.create_check_constraint(
            op.f(JOB_TYPE_CHECK),
            "job_type IN ('generate_study_guide', 'generate_quiz')",
        )
