"""add document processing stages

Revision ID: d2a7f0c91e35
Revises: b6d8f2a4c901
Create Date: 2026-08-11 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d2a7f0c91e35"
down_revision: str | None = "b6d8f2a4c901"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_STAGES = (
    "validating",
    "extracting_text",
    "running_ocr",
    "understanding_images",
    "cleaning_text",
    "chunking",
)
_STAGES_SQL = ", ".join(f"'{stage}'" for stage in _STAGES)


def upgrade() -> None:
    sqlite = op.get_bind().dialect.name == "sqlite"
    if sqlite:
        _backup_sqlite_dependents()
    with op.batch_alter_table("uploaded_documents", schema=None) as batch_op:
        batch_op.drop_constraint(
            batch_op.f("ck_uploaded_documents_status_valid"), type_="check"
        )
    op.execute(
        sa.text(
            "UPDATE uploaded_documents SET status = CASE "
            "WHEN status = 'pending' THEN 'uploaded' "
            "WHEN status = 'completed' THEN 'ready' ELSE status END"
        )
    )
    with op.batch_alter_table("uploaded_documents", schema=None) as batch_op:
        batch_op.alter_column(
            "status",
            existing_type=sa.String(length=20),
            existing_nullable=False,
            server_default="uploaded",
        )
        batch_op.create_check_constraint(
            batch_op.f("ck_uploaded_documents_status_valid"),
            "status IN ('uploaded', 'processing', 'ready', 'failed', 'deleting')",
        )
    if sqlite:
        _restore_sqlite_dependents()

    with op.batch_alter_table("processing_jobs", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("processing_stage", sa.String(length=32), nullable=True)
        )
        batch_op.add_column(
            sa.Column("failed_stage", sa.String(length=32), nullable=True)
        )
        batch_op.create_check_constraint(
            batch_op.f("ck_processing_jobs_processing_stage_valid"),
            f"processing_stage IS NULL OR processing_stage IN ({_STAGES_SQL})",
        )
        batch_op.create_check_constraint(
            batch_op.f("ck_processing_jobs_failed_stage_valid"),
            f"failed_stage IS NULL OR failed_stage IN ({_STAGES_SQL})",
        )
        batch_op.create_check_constraint(
            batch_op.f("ck_processing_jobs_processing_stage_status"),
            "processing_stage IS NULL OR status = 'running'",
        )
        batch_op.create_check_constraint(
            batch_op.f("ck_processing_jobs_failed_stage_status"),
            "failed_stage IS NULL OR status = 'failed'",
        )


def downgrade() -> None:
    with op.batch_alter_table("processing_jobs", schema=None) as batch_op:
        batch_op.drop_constraint(
            batch_op.f("ck_processing_jobs_failed_stage_status"), type_="check"
        )
        batch_op.drop_constraint(
            batch_op.f("ck_processing_jobs_processing_stage_status"), type_="check"
        )
        batch_op.drop_constraint(
            batch_op.f("ck_processing_jobs_failed_stage_valid"), type_="check"
        )
        batch_op.drop_constraint(
            batch_op.f("ck_processing_jobs_processing_stage_valid"), type_="check"
        )
        batch_op.drop_column("failed_stage")
        batch_op.drop_column("processing_stage")

    sqlite = op.get_bind().dialect.name == "sqlite"
    if sqlite:
        _backup_sqlite_dependents()
    with op.batch_alter_table("uploaded_documents", schema=None) as batch_op:
        batch_op.drop_constraint(
            batch_op.f("ck_uploaded_documents_status_valid"), type_="check"
        )
    op.execute(
        sa.text(
            "UPDATE uploaded_documents SET status = CASE "
            "WHEN status = 'uploaded' THEN 'pending' "
            "WHEN status = 'ready' THEN 'completed' "
            "WHEN status = 'deleting' THEN 'failed' ELSE status END"
        )
    )
    with op.batch_alter_table("uploaded_documents", schema=None) as batch_op:
        batch_op.alter_column(
            "status",
            existing_type=sa.String(length=20),
            existing_nullable=False,
            server_default="pending",
        )
        batch_op.create_check_constraint(
            batch_op.f("ck_uploaded_documents_status_valid"),
            "status IN ('pending', 'processing', 'completed', 'failed')",
        )
    if sqlite:
        _restore_sqlite_dependents()


def _backup_sqlite_dependents() -> None:
    op.execute(
        sa.text(
            "CREATE TEMPORARY TABLE _scrum35_jobs_backup AS "
            "SELECT * FROM processing_jobs"
        )
    )
    op.execute(
        sa.text(
            "CREATE TEMPORARY TABLE _scrum35_chunks_backup AS "
            "SELECT * FROM document_chunks"
        )
    )


def _restore_sqlite_dependents() -> None:
    job_columns = (
        "id, document_id, course_id, job_type, status, attempt_count, "
        "max_attempts, available_at, started_at, claimed_at, heartbeat_at, "
        "lease_expires_at, lease_owner, claim_token, finished_at, "
        "last_error_code, last_error_message, created_at, updated_at"
    )
    chunk_columns = (
        "id, document_id, course_id, chunk_index, page_number, text, created_at"
    )
    op.execute(
        sa.text(
            f"INSERT INTO processing_jobs ({job_columns}) "
            f"SELECT {job_columns} FROM _scrum35_jobs_backup"
        )
    )
    op.execute(
        sa.text(
            f"INSERT INTO document_chunks ({chunk_columns}) "
            f"SELECT {chunk_columns} FROM _scrum35_chunks_backup"
        )
    )
    op.execute(sa.text("DROP TABLE _scrum35_jobs_backup"))
    op.execute(sa.text("DROP TABLE _scrum35_chunks_backup"))
