"""add profile documents tables

Revision ID: e1a2b3c4d5e6
Revises: d4a7c19e6b83
Create Date: 2026-08-28 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e1a2b3c4d5e6"
down_revision: str | Sequence[str] | None = "d4a7c19e6b83"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EMBEDDING_DIMENSIONS = 768
_ASCII_WHITESPACE = " \t\n\r\v\f"
_DOCUMENT_PROCESSING_STAGES = (
    "validating",
    "extracting_text",
    "running_ocr",
    "understanding_images",
    "cleaning_text",
    "chunking",
    "generating_embeddings",
)
_STAGES_SQL = ", ".join(f"'{stage}'" for stage in _DOCUMENT_PROCESSING_STAGES)


def _embedding_column(postgresql: bool) -> sa.Column:
    if postgresql:
        from pgvector.sqlalchemy import Vector

        return sa.Column("embedding", Vector(EMBEDDING_DIMENSIONS), nullable=False)
    return sa.Column(
        "embedding",
        sa.LargeBinary(EMBEDDING_DIMENSIONS * 4),
        nullable=False,
    )


def upgrade() -> None:
    postgresql = op.get_bind().dialect.name == "postgresql"

    # 1. profile_documents
    op.create_table(
        "profile_documents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("original_file_name", sa.String(length=255), nullable=False),
        sa.Column("file_type", sa.String(length=50), nullable=False),
        sa.Column("mime_type", sa.String(length=255), nullable=False),
        sa.Column("file_size", sa.BigInteger(), nullable=False),
        sa.Column("file_hash", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("storage_provider", sa.String(length=50), nullable=False),
        sa.Column("storage_key", sa.String(length=500), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="uploaded", nullable=False),
        sa.Column("processing_error", sa.Text(), nullable=True),
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
        sa.CheckConstraint("file_size >= 0", name=op.f("ck_profile_documents_profile_doc_file_size_nonnegative")),
        sa.CheckConstraint("length(file_hash) = 64", name=op.f("ck_profile_documents_profile_doc_file_hash_length")),
        sa.CheckConstraint(
            "status IN ('uploaded', 'processing', 'ready', 'failed', 'deleting')",
            name=op.f("ck_profile_documents_profile_doc_status_valid"),
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_profile_documents_user_id_users"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_profile_documents")),
        sa.UniqueConstraint("id", "user_id", name=op.f("uq_profile_documents_id_user_id")),
        sa.UniqueConstraint("user_id", "file_hash", name=op.f("uq_profile_documents_user_id_file_hash")),
    )
    with op.batch_alter_table("profile_documents", schema=None) as batch_op:
        batch_op.create_index("ix_profile_documents_user_id", ["user_id"], unique=False)
        batch_op.create_index(
            "ix_profile_documents_user_status_created",
            ["user_id", "status", "created_at", "id"],
            unique=False,
        )
        batch_op.create_index(
            "uq_profile_documents_storage_provider_storage_key",
            ["storage_provider", "storage_key"],
            unique=True,
        )

    # 2. profile_document_chunks
    op.create_table(
        "profile_document_chunks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=True),
        sa.Column("end_page_number", sa.Integer(), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=postgresql),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "chunk_index = CAST(chunk_index AS INTEGER) AND chunk_index >= 0",
            name=op.f("ck_profile_document_chunks_profile_chunk_index_nonnegative"),
        ),
        sa.CheckConstraint(
            "page_number IS NULL OR page_number >= 1",
            name=op.f("ck_profile_document_chunks_profile_chunk_page_number_positive"),
        ),
        sa.CheckConstraint(
            "(page_number IS NULL AND end_page_number IS NULL) OR "
            "(page_number IS NOT NULL AND end_page_number IS NOT NULL AND end_page_number >= page_number)",
            name=op.f("ck_profile_document_chunks_profile_chunk_page_range_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["document_id", "user_id"],
            ["profile_documents.id", "profile_documents.user_id"],
            name=op.f("fk_profile_document_chunks_doc_user"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_profile_document_chunks")),
        sa.UniqueConstraint("document_id", "chunk_index", name=op.f("uq_profile_document_chunks_document_id_chunk_index")),
        sa.UniqueConstraint("id", "document_id", "user_id", name=op.f("uq_profile_document_chunks_id_doc_user")),
    )
    with op.batch_alter_table("profile_document_chunks", schema=None) as batch_op:
        batch_op.create_index("ix_profile_document_chunks_document_id", ["document_id"], unique=False)
        batch_op.create_index("ix_profile_document_chunks_user_id", ["user_id"], unique=False)
        batch_op.create_index(
            "ix_profile_document_chunks_user_doc_index",
            ["user_id", "document_id", "chunk_index", "id"],
            unique=False,
        )

    # 3. profile_chunk_embeddings
    op.create_table(
        "profile_chunk_embeddings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("chunk_id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        _embedding_column(postgresql),
        sa.Column("embedding_provider", sa.String(length=50), nullable=False),
        sa.Column("embedding_model", sa.String(length=128), nullable=False),
        sa.Column("dimensions", sa.Integer(), nullable=False),
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
        sa.CheckConstraint(
            "chunk_index = CAST(chunk_index AS INTEGER) AND chunk_index >= 0",
            name=op.f("ck_profile_chunk_embeddings_profile_chunk_emb_index_nonnegative"),
        ),
        sa.CheckConstraint(
            f"dimensions = {EMBEDDING_DIMENSIONS}",
            name=op.f("ck_profile_chunk_embeddings_profile_dimensions_supported"),
        ),
        sa.CheckConstraint(
            f"length(trim(embedding_model, '{_ASCII_WHITESPACE}')) > 0",
            name=op.f("ck_profile_chunk_embeddings_profile_emb_model_nonblank"),
        ),
        sa.CheckConstraint(
            f"length(trim(embedding_provider, '{_ASCII_WHITESPACE}')) > 0",
            name=op.f("ck_profile_chunk_embeddings_profile_emb_provider_nonblank"),
        ),
        sa.ForeignKeyConstraint(
            ["chunk_id", "document_id", "user_id"],
            ["profile_document_chunks.id", "profile_document_chunks.document_id", "profile_document_chunks.user_id"],
            name=op.f("fk_profile_chunk_embeddings_chunk_doc_user"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_profile_chunk_embeddings")),
        sa.UniqueConstraint("chunk_id", name=op.f("uq_profile_chunk_embeddings_chunk_id")),
    )
    with op.batch_alter_table("profile_chunk_embeddings", schema=None) as batch_op:
        batch_op.create_index("ix_profile_chunk_embeddings_document_id", ["document_id"], unique=False)
        batch_op.create_index("ix_profile_chunk_embeddings_user_id", ["user_id"], unique=False)
    if postgresql:
        op.create_index(
            "ix_profile_chunk_embeddings_embedding_hnsw",
            "profile_chunk_embeddings",
            ["embedding"],
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        )

    # 4. profile_document_pages
    op.create_table(
        "profile_document_pages",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("content_index", sa.Integer(), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=True),
        sa.Column("raw_text", sa.Text(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("raw_extraction_method", sa.String(length=20), nullable=True),
        sa.Column("extraction_method", sa.String(length=20), nullable=True),
        sa.Column("has_images", sa.Boolean(), server_default=sa.text("0" if not postgresql else "false"), nullable=False),
        sa.Column("needs_ocr", sa.Boolean(), server_default=sa.text("0" if not postgresql else "false"), nullable=False),
        sa.Column("raw_needs_ocr", sa.Boolean(), server_default=sa.text("0" if not postgresql else "false"), nullable=False),
        sa.Column("ocr_status", sa.String(length=20), server_default="not_required", nullable=False),
        sa.Column("has_visual_content", sa.Boolean(), server_default=sa.text("0" if not postgresql else "false"), nullable=False),
        sa.Column("visual_analysis_status", sa.String(length=20), server_default="not_applicable", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=postgresql),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint("content_index >= 0", name=op.f("ck_profile_document_pages_profile_content_index_nonnegative")),
        sa.CheckConstraint("page_number IS NULL OR page_number >= 1", name=op.f("ck_profile_document_pages_profile_page_number_positive")),
        sa.CheckConstraint(
            "raw_extraction_method IS NULL OR raw_extraction_method IN ('native', 'decoded')",
            name=op.f("ck_profile_document_pages_profile_raw_extraction_method_valid"),
        ),
        sa.CheckConstraint(
            "extraction_method IS NULL OR extraction_method IN ('native', 'decoded', 'ocr')",
            name=op.f("ck_profile_document_pages_profile_extraction_method_valid"),
        ),
        sa.CheckConstraint(
            "NOT needs_ocr OR (page_number IS NOT NULL AND (has_images OR has_visual_content))",
            name=op.f("ck_profile_document_pages_profile_ocr_candidate_valid"),
        ),
        sa.CheckConstraint(
            "NOT raw_needs_ocr OR (page_number IS NOT NULL AND (has_images OR has_visual_content))",
            name=op.f("ck_profile_document_pages_profile_raw_ocr_candidate_valid"),
        ),
        sa.CheckConstraint(
            "ocr_status IN ('not_required', 'pending', 'succeeded', 'no_text')",
            name=op.f("ck_profile_document_pages_profile_ocr_status_valid"),
        ),
        sa.CheckConstraint(
            "visual_analysis_status IN ('not_applicable', 'pending', 'not_configured', 'completed', 'partial', 'failed')",
            name=op.f("ck_profile_document_pages_profile_visual_analysis_status_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["document_id", "user_id"],
            ["profile_documents.id", "profile_documents.user_id"],
            name=op.f("fk_profile_document_pages_doc_user"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_profile_document_pages")),
        sa.UniqueConstraint("document_id", "content_index", name=op.f("uq_profile_document_pages_document_content_index")),
    )
    with op.batch_alter_table("profile_document_pages", schema=None) as batch_op:
        batch_op.create_index("ix_profile_document_pages_user_id", ["user_id"], unique=False)

    # 5. profile_document_visuals
    op.create_table(
        "profile_document_visuals",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("page_id", sa.Integer(), nullable=False),
        sa.Column("visual_index", sa.Integer(), nullable=False),
        sa.Column("visual_type", sa.String(length=20), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("bbox_x0", sa.Float(), nullable=False),
        sa.Column("bbox_y0", sa.Float(), nullable=False),
        sa.Column("bbox_x1", sa.Float(), nullable=False),
        sa.Column("bbox_y1", sa.Float(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("analysis_status", sa.String(length=20), server_default="pending", nullable=False),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=postgresql),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "analysis_status IN ('pending', 'not_configured', 'succeeded', 'skipped', 'failed')",
            name=op.f("ck_profile_document_visuals_profile_analysis_status_valid"),
        ),
        sa.CheckConstraint(
            f"analysis_status <> 'failed' OR (error_code IS NOT NULL AND length(trim(error_code, '{_ASCII_WHITESPACE}')) > 0)",
            name=op.f("ck_profile_document_visuals_profile_failed_error_code_required"),
        ),
        sa.CheckConstraint(
            "bbox_x0 >= 0 AND bbox_y0 >= 0 AND bbox_x1 > bbox_x0 AND bbox_y1 > bbox_y0",
            name=op.f("ck_profile_document_visuals_profile_bbox_valid"),
        ),
        sa.CheckConstraint(
            "description IS NULL OR analysis_status = 'succeeded'",
            name=op.f("ck_profile_document_visuals_profile_description_status_valid"),
        ),
        sa.CheckConstraint("source IN ('image', 'table', 'drawing')", name=op.f("ck_profile_document_visuals_profile_visual_source_valid")),
        sa.CheckConstraint(
            "visual_type IN ('diagram', 'table', 'chart', 'screenshot', 'figure', 'flowchart', 'other')",
            name=op.f("ck_profile_document_visuals_profile_visual_type_valid"),
        ),
        sa.CheckConstraint("visual_index >= 0", name=op.f("ck_profile_document_visuals_profile_visual_index_nonnegative")),
        sa.ForeignKeyConstraint(["page_id"], ["profile_document_pages.id"], name=op.f("fk_profile_document_visuals_page_id_profile_document_pages"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_profile_document_visuals")),
        sa.UniqueConstraint("page_id", "visual_index", name=op.f("uq_profile_document_visuals_page_id_visual_index")),
    )
    with op.batch_alter_table("profile_document_visuals", schema=None) as batch_op:
        batch_op.create_index("ix_profile_document_visuals_page_id", ["page_id"], unique=False)

    # 6. profile_processing_jobs
    op.create_table(
        "profile_processing_jobs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("job_type", sa.String(length=50), server_default="extract_document", nullable=False),
        sa.Column("status", sa.String(length=20), server_default="queued", nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default="3", nullable=False),
        sa.Column("correlation_id", sa.String(length=100), nullable=True),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=postgresql),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=postgresql), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=postgresql), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=postgresql), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=postgresql), nullable=True),
        sa.Column("lease_owner", sa.String(length=255), nullable=True),
        sa.Column("claim_token", sa.String(length=64), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=postgresql), nullable=True),
        sa.Column("last_error_code", sa.String(length=100), nullable=True),
        sa.Column("last_error_message", sa.Text(), nullable=True),
        sa.Column("processing_stage", sa.String(length=50), nullable=True),
        sa.Column("failed_stage", sa.String(length=50), nullable=True),
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
        sa.CheckConstraint("attempt_count >= 0", name=op.f("ck_profile_processing_jobs_profile_attempt_count_nonnegative")),
        sa.CheckConstraint("attempt_count <= max_attempts", name=op.f("ck_profile_processing_jobs_profile_attempt_count_within_limit")),
        sa.CheckConstraint("failed_stage IS NULL OR failed_stage IN (" + _STAGES_SQL + ")", name=op.f("ck_profile_processing_jobs_profile_failed_stage_valid")),
        sa.CheckConstraint("failed_stage IS NULL OR status = 'failed'", name=op.f("ck_profile_processing_jobs_profile_failed_stage_status")),
        sa.CheckConstraint(
            "(status IN ('succeeded', 'failed') AND finished_at IS NOT NULL) OR (status IN ('queued', 'running') AND finished_at IS NULL)",
            name=op.f("ck_profile_processing_jobs_profile_finished_state_valid"),
        ),
        sa.CheckConstraint("job_type = 'extract_document'", name=op.f("ck_profile_processing_jobs_profile_job_type_valid")),
        sa.CheckConstraint(
            "(status = 'running' AND attempt_count > 0 AND lease_owner IS NOT NULL AND claim_token IS NOT NULL AND claimed_at IS NOT NULL AND heartbeat_at IS NOT NULL AND lease_expires_at IS NOT NULL AND heartbeat_at >= claimed_at AND lease_expires_at > heartbeat_at AND finished_at IS NULL) OR (status <> 'running' AND lease_owner IS NULL AND claim_token IS NULL AND claimed_at IS NULL AND heartbeat_at IS NULL AND lease_expires_at IS NULL)",
            name=op.f("ck_profile_processing_jobs_profile_lease_state_valid"),
        ),
        sa.CheckConstraint("max_attempts > 0", name=op.f("ck_profile_processing_jobs_profile_max_attempts_positive")),
        sa.CheckConstraint("processing_stage IS NULL OR processing_stage IN (" + _STAGES_SQL + ")", name=op.f("ck_profile_processing_jobs_profile_processing_stage_valid")),
        sa.CheckConstraint("processing_stage IS NULL OR status = 'running'", name=op.f("ck_profile_processing_jobs_profile_processing_stage_status")),
        sa.CheckConstraint("status <> 'queued' OR attempt_count < max_attempts", name=op.f("ck_profile_processing_jobs_profile_queued_attempts_available")),
        sa.CheckConstraint(
            f"status <> 'failed' OR (last_error_code IS NOT NULL AND length(trim(last_error_code, '{_ASCII_WHITESPACE}')) > 0)",
            name=op.f("ck_profile_processing_jobs_profile_failed_last_error_code_present"),
        ),
        sa.CheckConstraint("status IN ('queued', 'running', 'succeeded', 'failed')", name=op.f("ck_profile_processing_jobs_profile_job_status_valid")),
        sa.ForeignKeyConstraint(
            ["document_id", "user_id"],
            ["profile_documents.id", "profile_documents.user_id"],
            name=op.f("fk_profile_processing_jobs_doc_user"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_profile_processing_jobs")),
        sa.UniqueConstraint("document_id", "job_type", name=op.f("uq_profile_processing_jobs_document_id_job_type")),
    )
    with op.batch_alter_table("profile_processing_jobs", schema=None) as batch_op:
        batch_op.create_index("ix_profile_processing_jobs_document_id", ["document_id"], unique=False)
        batch_op.create_index("ix_profile_processing_jobs_user_id", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_table("profile_processing_jobs")
    op.drop_table("profile_document_visuals")
    op.drop_table("profile_document_pages")
    op.drop_table("profile_chunk_embeddings")
    op.drop_table("profile_document_chunks")
    op.drop_table("profile_documents")
