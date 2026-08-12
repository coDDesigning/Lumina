"""add document visual enrichment

Revision ID: f7a3c9d2e541
Revises: c4e6a8f1b203
Create Date: 2026-08-12 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f7a3c9d2e541"
down_revision: str | None = "c4e6a8f1b203"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("document_pages", schema=None) as batch_op:
        batch_op.add_column(sa.Column("raw_text", sa.Text(), nullable=True))
        batch_op.add_column(
            sa.Column("raw_extraction_method", sa.String(length=20), nullable=True)
        )
        batch_op.add_column(
            sa.Column(
                "has_visual_content",
                sa.Boolean(),
                server_default=sa.false(),
                nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column(
                "ocr_status",
                sa.String(length=20),
                server_default="not_required",
                nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column(
                "raw_needs_ocr",
                sa.Boolean(),
                server_default=sa.false(),
                nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column(
                "visual_analysis_status",
                sa.String(length=20),
                server_default="not_applicable",
                nullable=False,
            )
        )

    op.execute(
        sa.text(
            "UPDATE document_pages SET raw_text = text, "
            "raw_extraction_method = extraction_method, "
            "raw_needs_ocr = needs_ocr, "
            "ocr_status = CASE WHEN needs_ocr THEN 'pending' ELSE 'not_required' END"
        )
    )

    with op.batch_alter_table("document_pages", schema=None) as batch_op:
        batch_op.drop_constraint(
            batch_op.f("ck_document_pages_extraction_method_valid"), type_="check"
        )
        batch_op.drop_constraint(
            batch_op.f("ck_document_pages_ocr_candidate_valid"), type_="check"
        )
        batch_op.alter_column("raw_text", existing_type=sa.Text(), nullable=False)
        batch_op.create_check_constraint(
            batch_op.f("ck_document_pages_raw_extraction_method_valid"),
            "raw_extraction_method IS NULL OR "
            "raw_extraction_method IN ('native', 'decoded')",
        )
        batch_op.create_check_constraint(
            batch_op.f("ck_document_pages_extraction_method_valid"),
            "extraction_method IS NULL OR "
            "extraction_method IN ('native', 'decoded', 'ocr')",
        )
        batch_op.create_check_constraint(
            batch_op.f("ck_document_pages_ocr_candidate_valid"),
            "NOT needs_ocr OR (page_number IS NOT NULL AND "
            "(has_images OR has_visual_content))",
        )
        batch_op.create_check_constraint(
            batch_op.f("ck_document_pages_ocr_status_valid"),
            "ocr_status IN ('not_required', 'pending', 'succeeded', 'no_text')",
        )
        batch_op.create_check_constraint(
            batch_op.f("ck_document_pages_raw_ocr_candidate_valid"),
            "NOT raw_needs_ocr OR (page_number IS NOT NULL AND "
            "(has_images OR has_visual_content))",
        )
        batch_op.create_check_constraint(
            batch_op.f("ck_document_pages_visual_analysis_status_valid"),
            "visual_analysis_status IN "
            "('not_applicable', 'pending', 'not_configured', 'completed', "
            "'partial', 'failed')",
        )

    op.create_table(
        "document_visuals",
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
        sa.Column(
            "analysis_status",
            sa.String(length=20),
            server_default="pending",
            nullable=False,
        ),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "visual_index >= 0",
            name=op.f("ck_document_visuals_visual_index_nonnegative"),
        ),
        sa.CheckConstraint(
            "visual_type IN "
            "('diagram', 'table', 'chart', 'screenshot', 'figure', 'flowchart', "
            "'other')",
            name=op.f("ck_document_visuals_visual_type_valid"),
        ),
        sa.CheckConstraint(
            "source IN ('image', 'table', 'drawing')",
            name=op.f("ck_document_visuals_source_valid"),
        ),
        sa.CheckConstraint(
            "bbox_x0 >= 0 AND bbox_y0 >= 0 AND bbox_x1 > bbox_x0 AND bbox_y1 > bbox_y0",
            name=op.f("ck_document_visuals_bbox_valid"),
        ),
        sa.CheckConstraint(
            "analysis_status IN "
            "('pending', 'not_configured', 'succeeded', 'skipped', 'failed')",
            name=op.f("ck_document_visuals_analysis_status_valid"),
        ),
        sa.CheckConstraint(
            "description IS NULL OR analysis_status = 'succeeded'",
            name=op.f("ck_document_visuals_description_status_valid"),
        ),
        sa.CheckConstraint(
            "analysis_status <> 'failed' OR error_code IS NOT NULL",
            name=op.f("ck_document_visuals_failed_error_code_required"),
        ),
        sa.ForeignKeyConstraint(
            ["page_id"],
            ["document_pages.id"],
            name=op.f("fk_document_visuals_page_id_document_pages"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_document_visuals")),
        sa.UniqueConstraint("page_id", "visual_index", name="uq_visual_page_index"),
    )
    with op.batch_alter_table("document_visuals", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_document_visuals_page_id"), ["page_id"], unique=False
        )


def downgrade() -> None:
    op.drop_table("document_visuals")
    op.execute(
        sa.text(
            "UPDATE document_pages SET text = raw_text, "
            "extraction_method = raw_extraction_method, "
            "needs_ocr = CASE WHEN has_images THEN raw_needs_ocr ELSE false END"
        )
    )
    with op.batch_alter_table("document_pages", schema=None) as batch_op:
        batch_op.drop_constraint(
            batch_op.f("ck_document_pages_visual_analysis_status_valid"), type_="check"
        )
        batch_op.drop_constraint(
            batch_op.f("ck_document_pages_ocr_candidate_valid"), type_="check"
        )
        batch_op.drop_constraint(
            batch_op.f("ck_document_pages_ocr_status_valid"), type_="check"
        )
        batch_op.drop_constraint(
            batch_op.f("ck_document_pages_raw_ocr_candidate_valid"), type_="check"
        )
        batch_op.drop_constraint(
            batch_op.f("ck_document_pages_extraction_method_valid"), type_="check"
        )
        batch_op.drop_constraint(
            batch_op.f("ck_document_pages_raw_extraction_method_valid"), type_="check"
        )
        batch_op.create_check_constraint(
            batch_op.f("ck_document_pages_extraction_method_valid"),
            "extraction_method IS NULL OR extraction_method IN ('native', 'decoded')",
        )
        batch_op.create_check_constraint(
            batch_op.f("ck_document_pages_ocr_candidate_valid"),
            "NOT needs_ocr OR (page_number IS NOT NULL AND has_images)",
        )
        batch_op.drop_column("visual_analysis_status")
        batch_op.drop_column("has_visual_content")
        batch_op.drop_column("ocr_status")
        batch_op.drop_column("raw_needs_ocr")
        batch_op.drop_column("raw_extraction_method")
        batch_op.drop_column("raw_text")
