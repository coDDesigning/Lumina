"""harden relational constraints

Revision ID: a1c5e7f9b203
Revises: e5c1a7b39d64
Create Date: 2026-08-14 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op

revision: str = "a1c5e7f9b203"
down_revision: str | None = "e5c1a7b39d64"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_AFFECTED_TABLES = (
    "uploaded_documents",
    "document_chunks",
    "processing_jobs",
    "document_visuals",
    "quiz_questions",
)
_ASCII_WHITESPACE = " \t\n\r\v\f"


def _lock_schema_changes() -> None:
    if context.is_offline_mode():
        raise RuntimeError("Schema hardening requires an online database preflight.")
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        bind.exec_driver_sql("BEGIN IMMEDIATE")
        violation = bind.exec_driver_sql("PRAGMA foreign_key_check").first()
        if violation is not None:
            raise RuntimeError(
                "Foreign key violations require manual correction before hardening."
            )
    elif bind.dialect.name == "postgresql":
        tables = ", ".join(_AFFECTED_TABLES)
        bind.execute(sa.text(f"LOCK TABLE {tables} IN SHARE ROW EXCLUSIVE MODE"))


def _require_no_rows(query: str, message: str) -> None:
    count = op.get_bind().scalar(sa.text(query))
    if count:
        raise RuntimeError(message)


def _preflight() -> None:
    _require_no_rows(
        "SELECT COUNT(*) FROM ("
        "SELECT 1 FROM uploaded_documents "
        "GROUP BY storage_provider, storage_key HAVING COUNT(*) > 1"
        ") AS duplicate_storage",
        "Duplicate document storage locations require manual correction.",
    )
    _require_no_rows(
        "SELECT COUNT(*) FROM document_chunks WHERE chunk_index < 0 OR "
        "chunk_index <> CAST(chunk_index AS INTEGER)",
        "Negative document chunk indexes require manual correction.",
    )
    _require_no_rows(
        "SELECT COUNT(*) FROM quiz_questions "
        "WHERE question_index < 0 OR "
        "question_index <> CAST(question_index AS INTEGER) OR "
        "correct_option_index < 0 OR "
        "correct_option_index <> CAST(correct_option_index AS INTEGER)",
        "Negative quiz question indexes require manual correction.",
    )
    _require_no_rows(
        "SELECT COUNT(*) FROM processing_jobs WHERE job_type <> 'extract_document'",
        "Unknown processing job types require manual correction.",
    )
    _require_no_rows(
        "SELECT COUNT(*) FROM processing_jobs WHERE "
        "(status = 'failed' AND (last_error_code IS NULL OR "
        f"length(trim(last_error_code, '{_ASCII_WHITESPACE}')) = 0)) OR "
        "(status = 'running' AND (lease_owner IS NULL OR "
        f"length(trim(lease_owner, '{_ASCII_WHITESPACE}')) = 0 OR "
        "claim_token IS NULL OR "
        "length(claim_token) <> 36))",
        "Invalid processing job identifiers require manual correction.",
    )
    _require_no_rows(
        "SELECT COUNT(*) FROM document_visuals WHERE analysis_status = 'failed' "
        f"AND (error_code IS NULL OR "
        f"length(trim(error_code, '{_ASCII_WHITESPACE}')) = 0)",
        "Blank failed visual error codes require manual correction.",
    )


def upgrade() -> None:
    _lock_schema_changes()
    _preflight()

    op.create_index(
        "uq_uploaded_documents_storage_provider_storage_key",
        "uploaded_documents",
        ["storage_provider", "storage_key"],
        unique=True,
    )
    with op.batch_alter_table("document_chunks", schema=None) as batch_op:
        batch_op.create_check_constraint(
            batch_op.f("ck_document_chunks_chunk_index_nonnegative"),
            "chunk_index = CAST(chunk_index AS INTEGER) AND chunk_index >= 0",
        )
    with op.batch_alter_table("processing_jobs", schema=None) as batch_op:
        batch_op.create_check_constraint(
            batch_op.f("ck_processing_jobs_job_type_valid"),
            "job_type = 'extract_document'",
        )
        batch_op.create_check_constraint(
            batch_op.f("ck_processing_jobs_failed_error_code_nonblank"),
            "status <> 'failed' OR (last_error_code IS NOT NULL AND "
            f"length(trim(last_error_code, '{_ASCII_WHITESPACE}')) > 0)",
        )
        batch_op.create_check_constraint(
            batch_op.f("ck_processing_jobs_running_lease_owner_nonblank"),
            "status <> 'running' OR (lease_owner IS NOT NULL AND "
            f"length(trim(lease_owner, '{_ASCII_WHITESPACE}')) > 0)",
        )
        batch_op.create_check_constraint(
            batch_op.f("ck_processing_jobs_running_claim_token_length"),
            "status <> 'running' OR (claim_token IS NOT NULL AND "
            "length(claim_token) = 36)",
        )
    with op.batch_alter_table("document_visuals", schema=None) as batch_op:
        batch_op.drop_constraint(
            batch_op.f("ck_document_visuals_failed_error_code_required"),
            type_="check",
        )
        batch_op.create_check_constraint(
            batch_op.f("ck_document_visuals_failed_error_code_required"),
            "analysis_status <> 'failed' OR "
            f"(error_code IS NOT NULL AND "
            f"length(trim(error_code, '{_ASCII_WHITESPACE}')) > 0)",
        )
    with op.batch_alter_table("quiz_questions", schema=None) as batch_op:
        batch_op.create_check_constraint(
            batch_op.f("ck_quiz_questions_question_index_nonnegative"),
            "question_index = CAST(question_index AS INTEGER) AND question_index >= 0",
        )
        batch_op.create_check_constraint(
            batch_op.f("ck_quiz_questions_correct_option_index_nonnegative"),
            "correct_option_index = CAST(correct_option_index AS INTEGER) "
            "AND correct_option_index >= 0",
        )


def downgrade() -> None:
    _lock_schema_changes()
    with op.batch_alter_table("quiz_questions", schema=None) as batch_op:
        batch_op.drop_constraint(
            batch_op.f("ck_quiz_questions_correct_option_index_nonnegative"),
            type_="check",
        )
        batch_op.drop_constraint(
            batch_op.f("ck_quiz_questions_question_index_nonnegative"),
            type_="check",
        )
    with op.batch_alter_table("document_visuals", schema=None) as batch_op:
        batch_op.drop_constraint(
            batch_op.f("ck_document_visuals_failed_error_code_required"),
            type_="check",
        )
        batch_op.create_check_constraint(
            batch_op.f("ck_document_visuals_failed_error_code_required"),
            "analysis_status <> 'failed' OR error_code IS NOT NULL",
        )
    with op.batch_alter_table("processing_jobs", schema=None) as batch_op:
        batch_op.drop_constraint(
            batch_op.f("ck_processing_jobs_running_claim_token_length"),
            type_="check",
        )
        batch_op.drop_constraint(
            batch_op.f("ck_processing_jobs_running_lease_owner_nonblank"),
            type_="check",
        )
        batch_op.drop_constraint(
            batch_op.f("ck_processing_jobs_failed_error_code_nonblank"),
            type_="check",
        )
        batch_op.drop_constraint(
            batch_op.f("ck_processing_jobs_job_type_valid"),
            type_="check",
        )
    with op.batch_alter_table("document_chunks", schema=None) as batch_op:
        batch_op.drop_constraint(
            batch_op.f("ck_document_chunks_chunk_index_nonnegative"),
            type_="check",
        )
    op.drop_index(
        "uq_uploaded_documents_storage_provider_storage_key",
        table_name="uploaded_documents",
    )
