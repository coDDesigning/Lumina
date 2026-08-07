"""add durable document processing jobs

Revision ID: b6d8f2a4c901
Revises: 97d9fd86a3ba
Create Date: 2026-08-05 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b6d8f2a4c901"
down_revision: str | None = "97d9fd86a3ba"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("document_chunks", schema=None) as batch_op:
        batch_op.add_column(sa.Column("page_number", sa.Integer(), nullable=True))
        batch_op.create_check_constraint(
            batch_op.f("ck_document_chunks_page_number_positive"),
            "page_number IS NULL OR page_number >= 1",
        )

    op.create_table(
        "processing_jobs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("course_id", sa.Integer(), nullable=False),
        sa.Column("job_type", sa.String(length=50), nullable=False),
        sa.Column(
            "status", sa.String(length=20), server_default="queued", nullable=False
        ),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_owner", sa.String(length=255), nullable=True),
        sa.Column("claim_token", sa.String(length=36), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=100), nullable=True),
        sa.Column("last_error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed')",
            name=op.f("ck_processing_jobs_status_valid"),
        ),
        sa.CheckConstraint(
            "attempt_count >= 0",
            name=op.f("ck_processing_jobs_attempt_count_nonnegative"),
        ),
        sa.CheckConstraint(
            "max_attempts > 0",
            name=op.f("ck_processing_jobs_max_attempts_positive"),
        ),
        sa.CheckConstraint(
            "attempt_count <= max_attempts",
            name=op.f("ck_processing_jobs_attempt_count_within_limit"),
        ),
        sa.CheckConstraint(
            "status <> 'queued' OR attempt_count < max_attempts",
            name=op.f("ck_processing_jobs_queued_attempts_available"),
        ),
        sa.CheckConstraint(
            "(status = 'running' AND attempt_count > 0 "
            "AND lease_owner IS NOT NULL AND claim_token IS NOT NULL "
            "AND claimed_at IS NOT NULL AND heartbeat_at IS NOT NULL "
            "AND lease_expires_at IS NOT NULL "
            "AND heartbeat_at >= claimed_at "
            "AND lease_expires_at > heartbeat_at AND finished_at IS NULL) OR "
            "(status <> 'running' AND lease_owner IS NULL "
            "AND claim_token IS NULL AND claimed_at IS NULL "
            "AND heartbeat_at IS NULL AND lease_expires_at IS NULL)",
            name=op.f("ck_processing_jobs_lease_state_valid"),
        ),
        sa.CheckConstraint(
            "(status IN ('succeeded', 'failed') AND finished_at IS NOT NULL) OR "
            "(status IN ('queued', 'running') AND finished_at IS NULL)",
            name=op.f("ck_processing_jobs_finished_state_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["document_id", "course_id"],
            ["uploaded_documents.id", "uploaded_documents.course_id"],
            name="fk_processing_jobs_document_course_uploaded_documents",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_processing_jobs")),
        sa.UniqueConstraint(
            "document_id",
            "job_type",
            name="uq_processing_jobs_document_type",
        ),
    )
    with op.batch_alter_table("processing_jobs", schema=None) as batch_op:
        batch_op.create_index(
            "ix_processing_jobs_claimable",
            ["status", "available_at", "id"],
            unique=False,
        )
        batch_op.create_index(
            "ix_processing_jobs_recoverable",
            ["status", "lease_expires_at", "id"],
            unique=False,
        )
        batch_op.create_index(
            "ix_processing_jobs_course_created",
            ["course_id", "created_at"],
            unique=False,
        )

    _backfill_processing_jobs()


def _backfill_processing_jobs() -> None:
    documents = sa.table(
        "uploaded_documents",
        sa.column("id", sa.Uuid()),
        sa.column("course_id", sa.Integer()),
        sa.column("status", sa.String()),
        sa.column("processing_error", sa.Text()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    chunks = sa.table(
        "document_chunks",
        sa.column("id", sa.Integer()),
        sa.column("document_id", sa.Uuid()),
        sa.column("text", sa.Text()),
    )
    courses = sa.table(
        "courses",
        sa.column("id", sa.Integer()),
        sa.column("is_deleted", sa.Boolean()),
    )
    jobs = sa.table(
        "processing_jobs",
        sa.column("document_id", sa.Uuid()),
        sa.column("course_id", sa.Integer()),
        sa.column("job_type", sa.String()),
        sa.column("status", sa.String()),
        sa.column("attempt_count", sa.Integer()),
        sa.column("max_attempts", sa.Integer()),
        sa.column("available_at", sa.DateTime(timezone=True)),
        sa.column("started_at", sa.DateTime(timezone=True)),
        sa.column("finished_at", sa.DateTime(timezone=True)),
        sa.column("last_error_code", sa.String()),
        sa.column("last_error_message", sa.Text()),
    )

    has_chunks = sa.exists(
        sa.select(chunks.c.id).where(
            chunks.c.document_id == documents.c.id,
            sa.func.length(sa.func.trim(chunks.c.text)) > 0,
        )
    )
    course_deleted = sa.exists(
        sa.select(courses.c.id).where(
            courses.c.id == documents.c.course_id,
            courses.c.is_deleted.is_(True),
        )
    )
    completed_with_chunks = sa.and_(
        documents.c.status == "completed",
        has_chunks,
    )
    needs_processing = sa.or_(
        documents.c.status.in_(("pending", "processing")),
        sa.and_(documents.c.status == "completed", ~has_chunks),
    )
    failed_job = sa.or_(
        documents.c.status == "failed",
        sa.and_(course_deleted, needs_processing),
    )
    terminal = sa.or_(completed_with_chunks, failed_job)
    op.execute(
        sa.insert(jobs).from_select(
            [
                "document_id",
                "course_id",
                "job_type",
                "status",
                "attempt_count",
                "max_attempts",
                "available_at",
                "started_at",
                "finished_at",
                "last_error_code",
                "last_error_message",
            ],
            sa.select(
                documents.c.id,
                documents.c.course_id,
                sa.literal("extract_document"),
                sa.case(
                    (completed_with_chunks, "succeeded"),
                    (failed_job, "failed"),
                    else_="queued",
                ),
                sa.case(
                    (completed_with_chunks, 1),
                    (failed_job, 3),
                    else_=0,
                ),
                sa.literal(3),
                documents.c.created_at,
                sa.case(
                    (completed_with_chunks, documents.c.created_at),
                    else_=sa.null(),
                ),
                sa.case((terminal, documents.c.updated_at), else_=sa.null()),
                sa.case(
                    (documents.c.status == "failed", "LEGACY_PROCESSING_FAILED"),
                    (course_deleted, "COURSE_DELETED"),
                    else_=sa.null(),
                ),
                sa.case(
                    (
                        documents.c.status == "failed",
                        "Legacy document processing failed.",
                    ),
                    (
                        course_deleted,
                        "The course was deleted before document processing completed.",
                    ),
                    else_=sa.null(),
                ),
            ),
        )
    )
    op.execute(
        sa.update(documents)
        .where(needs_processing, ~course_deleted)
        .values(status="pending", processing_error=None)
    )
    op.execute(
        sa.update(documents)
        .where(needs_processing, course_deleted)
        .values(
            status="failed",
            processing_error=(
                "The course was deleted before document processing completed."
            ),
        )
    )


def downgrade() -> None:
    documents = sa.table(
        "uploaded_documents",
        sa.column("id", sa.Uuid()),
        sa.column("status", sa.String()),
        sa.column("processing_error", sa.Text()),
    )
    jobs = sa.table(
        "processing_jobs",
        sa.column("document_id", sa.Uuid()),
        sa.column("status", sa.String()),
    )
    running_document_ids = sa.select(jobs.c.document_id).where(
        jobs.c.status == "running"
    )
    op.execute(
        sa.update(documents)
        .where(documents.c.id.in_(running_document_ids))
        .values(status="pending", processing_error=None)
    )

    with op.batch_alter_table("processing_jobs", schema=None) as batch_op:
        batch_op.drop_index("ix_processing_jobs_course_created")
        batch_op.drop_index("ix_processing_jobs_recoverable")
        batch_op.drop_index("ix_processing_jobs_claimable")
    op.drop_table("processing_jobs")

    with op.batch_alter_table("document_chunks", schema=None) as batch_op:
        batch_op.drop_constraint(
            batch_op.f("ck_document_chunks_page_number_positive"), type_="check"
        )
        batch_op.drop_column("page_number")
