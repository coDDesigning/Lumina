"""add document generation locks

Revision ID: b6d21f4c8a37
Revises: f2d90b4c7168
Create Date: 2026-09-05 13:40:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
import backend

revision: str = "b6d21f4c8a37"
down_revision: str | Sequence[str] | None = "f2d90b4c7168"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "document_generation_locks",
        sa.Column("document_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("holder_token", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("holder", sa.String(length=255), nullable=False),
        sa.Column(
            "acquired_at",
            backend.app.models.UTCDateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column("expires_at", backend.app.models.UTCDateTime(), nullable=False),
        sa.CheckConstraint(
            "expires_at > acquired_at",
            name=op.f("ck_document_generation_locks_lease_positive"),
        ),
        sa.PrimaryKeyConstraint(
            "document_id",
            "holder_token",
            name=op.f("pk_document_generation_locks"),
        ),
    )
    op.create_index(
        "ix_document_generation_locks_document_expires",
        "document_generation_locks",
        ["document_id", "expires_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_document_generation_locks_document_expires",
        table_name="document_generation_locks",
    )
    op.drop_table("document_generation_locks")
