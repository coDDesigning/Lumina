"""user credits and preferred model defaults

Revision ID: 2a7c4e9f8b10
Revises: 910e2719d549
Create Date: 2026-08-20 12:55:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "2a7c4e9f8b10"
down_revision: str | Sequence[str] | None = "910e2719d549"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Update legacy inert default preferred_model for existing users to gemini:gemini-3.6-flash
    op.execute(
        "UPDATE users SET preferred_model = 'gemini:gemini-3.6-flash' WHERE preferred_model = 'gpt-4o-mini'"
    )
    # Ensure regular users (non-admin) with NULL credits have 50.0 credits
    op.execute(
        "UPDATE users SET credits = 50.0 WHERE credits IS NULL AND role_id IN (SELECT id FROM roles WHERE name = 'user')"
    )


def downgrade() -> None:
    pass
