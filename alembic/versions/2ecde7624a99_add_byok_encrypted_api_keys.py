"""add_byok_encrypted_api_keys

Revision ID: 2ecde7624a99
Revises: e74c4d3649f1
Create Date: 2026-08-30 14:37:17.979433
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op


revision: str = "2ecde7624a99"
down_revision: str | Sequence[str] | None = "e74c4d3649f1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("encrypted_openai_api_key", sa.Text(), nullable=True)
        )
        batch_op.add_column(
            sa.Column("encrypted_gemini_api_key", sa.Text(), nullable=True)
        )
        batch_op.add_column(
            sa.Column("encrypted_anthropic_api_key", sa.Text(), nullable=True)
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_column("encrypted_anthropic_api_key")
        batch_op.drop_column("encrypted_gemini_api_key")
        batch_op.drop_column("encrypted_openai_api_key")
