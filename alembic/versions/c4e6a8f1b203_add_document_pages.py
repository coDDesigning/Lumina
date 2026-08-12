"""add raw document pages

Revision ID: c4e6a8f1b203
Revises: d2a7f0c91e35
Create Date: 2026-08-12 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c4e6a8f1b203"
down_revision: str | None = "d2a7f0c91e35"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "document_pages",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("course_id", sa.Integer(), nullable=False),
        sa.Column("content_index", sa.Integer(), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("extraction_method", sa.String(length=20), nullable=True),
        sa.Column(
            "has_images", sa.Boolean(), server_default=sa.false(), nullable=False
        ),
        sa.Column("needs_ocr", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "content_index >= 0",
            name=op.f("ck_document_pages_content_index_nonnegative"),
        ),
        sa.CheckConstraint(
            "page_number IS NULL OR page_number >= 1",
            name=op.f("ck_document_pages_page_number_positive"),
        ),
        sa.CheckConstraint(
            "extraction_method IS NULL OR extraction_method IN ('native', 'decoded')",
            name=op.f("ck_document_pages_extraction_method_valid"),
        ),
        sa.CheckConstraint(
            "NOT needs_ocr OR (page_number IS NOT NULL AND has_images)",
            name=op.f("ck_document_pages_ocr_candidate_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["course_id"],
            ["courses.id"],
            name=op.f("fk_document_pages_course_id_courses"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["document_id", "course_id"],
            ["uploaded_documents.id", "uploaded_documents.course_id"],
            name="fk_document_pages_document_course_uploaded_documents",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_document_pages")),
        sa.UniqueConstraint(
            "document_id",
            "content_index",
            name="uq_document_pages_document_content_index",
        ),
    )
    with op.batch_alter_table("document_pages", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_document_pages_course_id"), ["course_id"], unique=False
        )


def downgrade() -> None:
    op.drop_table("document_pages")
