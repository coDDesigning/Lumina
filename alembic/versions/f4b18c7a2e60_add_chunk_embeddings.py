"""add chunk embeddings

Revision ID: f4b18c7a2e60
Revises: e4a7b1c90d52
Create Date: 2026-08-19 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op


revision: str = "f4b18c7a2e60"
down_revision: str | Sequence[str] | None = "e4a7b1c90d52"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EMBEDDING_DIMENSIONS = 768
_ASCII_WHITESPACE = " \t\n\r\v\f"

_PREVIOUS_STAGES = (
    "validating",
    "extracting_text",
    "running_ocr",
    "understanding_images",
    "cleaning_text",
    "chunking",
)
_CURRENT_STAGES = _PREVIOUS_STAGES + ("generating_embeddings",)


def _stages_sql(stages: Sequence[str]) -> str:
    return ", ".join(f"'{stage}'" for stage in stages)


def _embedding_column(postgresql: bool) -> sa.Column:
    if postgresql:
        from pgvector.sqlalchemy import Vector

        return sa.Column("embedding", Vector(EMBEDDING_DIMENSIONS), nullable=False)
    return sa.Column(
        "embedding",
        sa.LargeBinary(EMBEDDING_DIMENSIONS * 4),
        nullable=False,
    )


def _replace_stage_constraints(stages: Sequence[str]) -> None:
    stages_sql = _stages_sql(stages)
    with op.batch_alter_table("processing_jobs", schema=None) as batch_op:
        batch_op.drop_constraint(
            batch_op.f("ck_processing_jobs_failed_stage_valid"), type_="check"
        )
        batch_op.drop_constraint(
            batch_op.f("ck_processing_jobs_processing_stage_valid"), type_="check"
        )
        batch_op.create_check_constraint(
            batch_op.f("ck_processing_jobs_processing_stage_valid"),
            f"processing_stage IS NULL OR processing_stage IN ({stages_sql})",
        )
        batch_op.create_check_constraint(
            batch_op.f("ck_processing_jobs_failed_stage_valid"),
            f"failed_stage IS NULL OR failed_stage IN ({stages_sql})",
        )


def upgrade() -> None:
    postgresql = op.get_bind().dialect.name == "postgresql"

    _replace_stage_constraints(_CURRENT_STAGES)

    with op.batch_alter_table("document_chunks", schema=None) as batch_op:
        batch_op.create_unique_constraint(
            batch_op.f("uq_document_chunks_id_document_id_course_id"),
            ["id", "document_id", "course_id"],
        )

    if postgresql:
        op.execute(sa.text("CREATE EXTENSION IF NOT EXISTS vector"))

    op.create_table(
        "chunk_embeddings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("chunk_id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("course_id", sa.Integer(), nullable=False),
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
            f"dimensions = {EMBEDDING_DIMENSIONS}",
            name="ck_chunk_embeddings_dimensions_supported",
        ),
        sa.CheckConstraint(
            "chunk_index = CAST(chunk_index AS INTEGER) AND chunk_index >= 0",
            name="ck_chunk_embeddings_chunk_index_nonnegative",
        ),
        sa.CheckConstraint(
            f"length(trim(embedding_provider, '{_ASCII_WHITESPACE}')) > 0",
            name="ck_chunk_embeddings_embedding_provider_nonblank",
        ),
        sa.CheckConstraint(
            f"length(trim(embedding_model, '{_ASCII_WHITESPACE}')) > 0",
            name="ck_chunk_embeddings_embedding_model_nonblank",
        ),
        sa.ForeignKeyConstraint(
            ["chunk_id", "document_id", "course_id"],
            [
                "document_chunks.id",
                "document_chunks.document_id",
                "document_chunks.course_id",
            ],
            name="fk_chunk_embeddings_chunk_document_course_document_chunks",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_chunk_embeddings"),
        sa.UniqueConstraint("chunk_id", name="uq_chunk_embeddings_chunk_id"),
    )
    op.create_index(
        op.f("ix_chunk_embeddings_document_id"),
        "chunk_embeddings",
        ["document_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_chunk_embeddings_course_id"),
        "chunk_embeddings",
        ["course_id"],
        unique=False,
    )

    if postgresql:
        # Cosine is the metric embedding generation and retrieval both agree on.
        op.execute(
            sa.text(
                "CREATE INDEX ix_chunk_embeddings_embedding_hnsw "
                "ON chunk_embeddings USING hnsw (embedding vector_cosine_ops)"
            )
        )


def downgrade() -> None:
    postgresql = op.get_bind().dialect.name == "postgresql"

    if postgresql:
        op.execute(sa.text("DROP INDEX IF EXISTS ix_chunk_embeddings_embedding_hnsw"))
    op.drop_index(op.f("ix_chunk_embeddings_course_id"), table_name="chunk_embeddings")
    op.drop_index(
        op.f("ix_chunk_embeddings_document_id"), table_name="chunk_embeddings"
    )
    op.drop_table("chunk_embeddings")

    with op.batch_alter_table("document_chunks", schema=None) as batch_op:
        batch_op.drop_constraint(
            batch_op.f("uq_document_chunks_id_document_id_course_id"), type_="unique"
        )

    op.execute(
        sa.text(
            "UPDATE processing_jobs SET processing_stage = 'chunking' "
            "WHERE processing_stage = 'generating_embeddings'"
        )
    )
    op.execute(
        sa.text(
            "UPDATE processing_jobs SET failed_stage = 'chunking' "
            "WHERE failed_stage = 'generating_embeddings'"
        )
    )
    _replace_stage_constraints(_PREVIOUS_STAGES)
