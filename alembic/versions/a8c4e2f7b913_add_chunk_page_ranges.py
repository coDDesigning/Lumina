"""add chunk page ranges

Revision ID: a8c4e2f7b913
Revises: f7a3c9d2e541
Create Date: 2026-08-14 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a8c4e2f7b913"
down_revision: str | None = "f7a3c9d2e541"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("document_chunks", schema=None) as batch_op:
        batch_op.add_column(sa.Column("end_page_number", sa.Integer(), nullable=True))

    op.execute(
        sa.text(
            "UPDATE document_chunks SET end_page_number = page_number "
            "WHERE page_number IS NOT NULL"
        )
    )

    with op.batch_alter_table("document_chunks", schema=None) as batch_op:
        batch_op.create_check_constraint(
            batch_op.f("ck_document_chunks_page_range_valid"),
            "(page_number IS NULL AND end_page_number IS NULL) OR "
            "(page_number IS NOT NULL AND end_page_number IS NOT NULL AND "
            "end_page_number >= page_number)",
        )


def downgrade() -> None:
    with op.batch_alter_table("document_chunks", schema=None) as batch_op:
        batch_op.drop_constraint(
            batch_op.f("ck_document_chunks_page_range_valid"), type_="check"
        )
        batch_op.drop_column("end_page_number")
