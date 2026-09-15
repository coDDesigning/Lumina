"""allow describe visuals jobs

Revision ID: c1d7e94b3a20
Revises: a9d4e2f7c601
Create Date: 2026-09-09 21:40:00.000000

Describing a document's visuals costs tens of seconds per image, so it no longer
runs inside the extraction attempt that must also parse, OCR, clean and chunk the
whole document within one bounded budget. It becomes its own job, claimed against
a document that is already ``ready``, and both job tables reject it until their
job_type CHECK admits the new value.

The rebuild is driven from ``copy_from`` rather than reflection because SQLite
recreates the whole table to change one CHECK, and these tables carry fourteen of
them plus four indexes that a reflected definition must not be trusted to carry
across. The tables below are the frozen ``a9d4e2f7c601`` shape; they are
deliberately not read from the models, which move on.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

import backend

revision: str = "c1d7e94b3a20"
down_revision: str | Sequence[str] | None = "a9d4e2f7c601"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JOB_TYPE_CHECK = "ck_processing_jobs_job_type_valid"
PROFILE_JOB_TYPE_CHECK = "ck_profile_processing_jobs_profile_job_type_valid"
WIDENED_JOB_TYPES = "job_type IN ('extract_document', 'describe_visuals')"
FROZEN_JOB_TYPES = "job_type = 'extract_document'"

LEASE_STATE = (
    "(status = 'running' AND attempt_count > 0 AND lease_owner IS NOT NULL AND "
    "claim_token IS NOT NULL AND claimed_at IS NOT NULL AND heartbeat_at IS NOT "
    "NULL AND lease_expires_at IS NOT NULL AND heartbeat_at >= claimed_at AND "
    "lease_expires_at > heartbeat_at AND finished_at IS NULL) OR (status <> "
    "'running' AND lease_owner IS NULL AND claim_token IS NULL AND claimed_at IS "
    "NULL AND heartbeat_at IS NULL AND lease_expires_at IS NULL)"
)
FINISHED_STATE = (
    "(status IN ('succeeded', 'failed') AND finished_at IS NOT NULL) OR (status "
    "IN ('queued', 'running') AND finished_at IS NULL)"
)
STAGE_VOCABULARY = (
    "IN ('validating', 'extracting_text', 'running_ocr', 'understanding_images', "
    "'cleaning_text', 'chunking', 'generating_embeddings')"
)
BLANK_CHARACTERS = " \t\n\r\x0b\x0c"


def _processing_jobs_table() -> sa.Table:
    """The table exactly as ``a9d4e2f7c601`` left it."""
    return sa.Table(
        "processing_jobs",
        sa.MetaData(),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("course_id", sa.Integer(), nullable=False),
        sa.Column("job_type", sa.String(length=50), nullable=False),
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
        sa.Column("processing_stage", sa.String(length=32), nullable=True),
        sa.Column("failed_stage", sa.String(length=32), nullable=True),
        sa.Column("correlation_id", sa.String(length=64), nullable=True),
        sa.Column("parent_operation_id", sa.String(length=80), nullable=True),
        sa.CheckConstraint(
            LEASE_STATE, name=op.f("ck_processing_jobs_lease_state_valid")
        ),
        sa.CheckConstraint(
            FINISHED_STATE, name=op.f("ck_processing_jobs_finished_state_valid")
        ),
        sa.CheckConstraint(FROZEN_JOB_TYPES, name=op.f(JOB_TYPE_CHECK)),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed')",
            name=op.f("ck_processing_jobs_status_valid"),
        ),
        sa.CheckConstraint(
            "status <> 'failed' OR (last_error_code IS NOT NULL AND "
            f"length(trim(last_error_code, '{BLANK_CHARACTERS}')) > 0)",
            name=op.f("ck_processing_jobs_failed_error_code_nonblank"),
        ),
        sa.CheckConstraint(
            "status <> 'queued' OR attempt_count < max_attempts",
            name=op.f("ck_processing_jobs_queued_attempts_available"),
        ),
        sa.CheckConstraint(
            "status <> 'running' OR (claim_token IS NOT NULL AND "
            "length(claim_token) = 36)",
            name=op.f("ck_processing_jobs_running_claim_token_length"),
        ),
        sa.CheckConstraint(
            "status <> 'running' OR (lease_owner IS NOT NULL AND "
            f"length(trim(lease_owner, '{BLANK_CHARACTERS}')) > 0)",
            name=op.f("ck_processing_jobs_running_lease_owner_nonblank"),
        ),
        sa.CheckConstraint(
            "attempt_count <= max_attempts",
            name=op.f("ck_processing_jobs_attempt_count_within_limit"),
        ),
        sa.CheckConstraint(
            "attempt_count >= 0",
            name=op.f("ck_processing_jobs_attempt_count_nonnegative"),
        ),
        sa.CheckConstraint(
            "max_attempts > 0", name=op.f("ck_processing_jobs_max_attempts_positive")
        ),
        sa.CheckConstraint(
            "processing_stage IS NULL OR status = 'running'",
            name=op.f("ck_processing_jobs_processing_stage_status"),
        ),
        sa.CheckConstraint(
            "failed_stage IS NULL OR status = 'failed'",
            name=op.f("ck_processing_jobs_failed_stage_status"),
        ),
        sa.CheckConstraint(
            f"processing_stage IS NULL OR processing_stage {STAGE_VOCABULARY}",
            name=op.f("ck_processing_jobs_processing_stage_valid"),
        ),
        sa.CheckConstraint(
            f"failed_stage IS NULL OR failed_stage {STAGE_VOCABULARY}",
            name=op.f("ck_processing_jobs_failed_stage_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["document_id", "course_id"],
            ["uploaded_documents.id", "uploaded_documents.course_id"],
            name="fk_processing_jobs_document_course_uploaded_documents",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_processing_jobs")),
        sa.UniqueConstraint(
            "document_id", "job_type", name="uq_processing_jobs_document_type"
        ),
        sa.Index("ix_processing_jobs_claimable", "status", "available_at", "id"),
        sa.Index("ix_processing_jobs_course_created", "course_id", "created_at"),
        sa.Index("ix_processing_jobs_recoverable", "status", "lease_expires_at", "id"),
        sa.Index("ix_processing_jobs_parent_operation", "parent_operation_id"),
    )


def _profile_processing_jobs_table() -> sa.Table:
    """The table exactly as ``a9d4e2f7c601`` left it."""
    return sa.Table(
        "profile_processing_jobs",
        sa.MetaData(),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column(
            "job_type",
            sa.String(length=50),
            server_default="extract_document",
            nullable=False,
        ),
        sa.Column(
            "status", sa.String(length=20), server_default="queued", nullable=False
        ),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("correlation_id", sa.String(length=64), nullable=True),
        sa.Column(
            "available_at",
            backend.app.models.UTCDateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column("started_at", backend.app.models.UTCDateTime(), nullable=True),
        sa.Column("claimed_at", backend.app.models.UTCDateTime(), nullable=True),
        sa.Column("heartbeat_at", backend.app.models.UTCDateTime(), nullable=True),
        sa.Column("lease_expires_at", backend.app.models.UTCDateTime(), nullable=True),
        sa.Column("lease_owner", sa.String(length=255), nullable=True),
        sa.Column("claim_token", sa.String(length=36), nullable=True),
        sa.Column("finished_at", backend.app.models.UTCDateTime(), nullable=True),
        sa.Column("last_error_code", sa.String(length=100), nullable=True),
        sa.Column("last_error_message", sa.Text(), nullable=True),
        sa.Column("processing_stage", sa.String(length=32), nullable=True),
        sa.Column("failed_stage", sa.String(length=32), nullable=True),
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
        sa.Column("parent_operation_id", sa.String(length=80), nullable=True),
        sa.CheckConstraint(
            LEASE_STATE,
            name=op.f("ck_profile_processing_jobs_profile_lease_state_valid"),
        ),
        sa.CheckConstraint(
            FINISHED_STATE,
            name=op.f("ck_profile_processing_jobs_profile_finished_state_valid"),
        ),
        sa.CheckConstraint(FROZEN_JOB_TYPES, name=op.f(PROFILE_JOB_TYPE_CHECK)),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed')",
            name=op.f("ck_profile_processing_jobs_profile_job_status_valid"),
        ),
        sa.CheckConstraint(
            "status <> 'failed' OR (last_error_code IS NOT NULL AND "
            f"length(trim(last_error_code, '{BLANK_CHARACTERS}')) > 0)",
            name=op.f(
                "ck_profile_processing_jobs_profile_failed_last_error_code_present"
            ),
        ),
        sa.CheckConstraint(
            "status <> 'queued' OR attempt_count < max_attempts",
            name=op.f("ck_profile_processing_jobs_profile_queued_attempts_available"),
        ),
        sa.CheckConstraint(
            "attempt_count <= max_attempts",
            name=op.f("ck_profile_processing_jobs_profile_attempt_count_within_limit"),
        ),
        sa.CheckConstraint(
            "attempt_count >= 0",
            name=op.f("ck_profile_processing_jobs_profile_attempt_count_nonnegative"),
        ),
        sa.CheckConstraint(
            "max_attempts > 0",
            name=op.f("ck_profile_processing_jobs_profile_max_attempts_positive"),
        ),
        sa.CheckConstraint(
            "processing_stage IS NULL OR status = 'running'",
            name=op.f("ck_profile_processing_jobs_profile_processing_stage_status"),
        ),
        sa.CheckConstraint(
            "failed_stage IS NULL OR status = 'failed'",
            name=op.f("ck_profile_processing_jobs_profile_failed_stage_status"),
        ),
        sa.CheckConstraint(
            f"processing_stage IS NULL OR processing_stage {STAGE_VOCABULARY}",
            name=op.f("ck_profile_processing_jobs_profile_processing_stage_valid"),
        ),
        sa.CheckConstraint(
            f"failed_stage IS NULL OR failed_stage {STAGE_VOCABULARY}",
            name=op.f("ck_profile_processing_jobs_profile_failed_stage_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["document_id", "user_id"],
            ["profile_documents.id", "profile_documents.user_id"],
            name="fk_profile_processing_jobs_doc_user",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_profile_processing_jobs")),
        sa.UniqueConstraint(
            "document_id", "job_type", name="uq_profile_processing_jobs_doc_type"
        ),
        sa.Index(
            "ix_profile_processing_jobs_claimable", "status", "available_at", "id"
        ),
        sa.Index(
            "ix_profile_processing_jobs_recoverable",
            "status",
            "lease_expires_at",
            "id",
        ),
        sa.Index(op.f("ix_profile_processing_jobs_document_id"), "document_id"),
        sa.Index(op.f("ix_profile_processing_jobs_user_id"), "user_id"),
        sa.Index("ix_profile_processing_jobs_parent_operation", "parent_operation_id"),
    )


def upgrade() -> None:
    with op.batch_alter_table(
        "processing_jobs", schema=None, copy_from=_processing_jobs_table()
    ) as batch_op:
        batch_op.drop_constraint(op.f(JOB_TYPE_CHECK), type_="check")
        batch_op.create_check_constraint(op.f(JOB_TYPE_CHECK), WIDENED_JOB_TYPES)

    with op.batch_alter_table(
        "profile_processing_jobs",
        schema=None,
        copy_from=_profile_processing_jobs_table(),
    ) as batch_op:
        batch_op.drop_constraint(op.f(PROFILE_JOB_TYPE_CHECK), type_="check")
        batch_op.create_check_constraint(
            op.f(PROFILE_JOB_TYPE_CHECK), WIDENED_JOB_TYPES
        )


def downgrade() -> None:
    op.execute(
        sa.text("DELETE FROM processing_jobs WHERE job_type = 'describe_visuals'")
    )
    op.execute(
        sa.text(
            "DELETE FROM profile_processing_jobs WHERE job_type = 'describe_visuals'"
        )
    )

    table = _processing_jobs_table()
    table.constraints = {
        constraint
        for constraint in table.constraints
        if constraint.name != op.f(JOB_TYPE_CHECK)
    }
    table.append_constraint(
        sa.CheckConstraint(WIDENED_JOB_TYPES, name=op.f(JOB_TYPE_CHECK))
    )
    with op.batch_alter_table(
        "processing_jobs", schema=None, copy_from=table
    ) as batch_op:
        batch_op.drop_constraint(op.f(JOB_TYPE_CHECK), type_="check")
        batch_op.create_check_constraint(op.f(JOB_TYPE_CHECK), FROZEN_JOB_TYPES)

    profile_table = _profile_processing_jobs_table()
    profile_table.constraints = {
        constraint
        for constraint in profile_table.constraints
        if constraint.name != op.f(PROFILE_JOB_TYPE_CHECK)
    }
    profile_table.append_constraint(
        sa.CheckConstraint(WIDENED_JOB_TYPES, name=op.f(PROFILE_JOB_TYPE_CHECK))
    )
    with op.batch_alter_table(
        "profile_processing_jobs", schema=None, copy_from=profile_table
    ) as batch_op:
        batch_op.drop_constraint(op.f(PROFILE_JOB_TYPE_CHECK), type_="check")
        batch_op.create_check_constraint(op.f(PROFILE_JOB_TYPE_CHECK), FROZEN_JOB_TYPES)
