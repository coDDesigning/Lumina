"""quiz attempt score nullable

Revision ID: c3b8e07a1d95
Revises: a1f6c3b7d284
Create Date: 2026-09-04 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c3b8e07a1d95"
down_revision: str | Sequence[str] | None = "a1f6c3b7d284"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


UNGRADED_ATTEMPTS = """
    SELECT quiz_attempts.id
    FROM quiz_attempts
    WHERE NOT EXISTS (
        SELECT 1
        FROM quiz_attempt_answers
        WHERE quiz_attempt_answers.attempt_id = quiz_attempts.id
          AND quiz_attempt_answers.score IS NOT NULL
    )
"""


def upgrade() -> None:
    with op.batch_alter_table("quiz_attempts", schema=None) as batch_op:
        batch_op.alter_column(
            "score",
            existing_type=sa.Float(),
            nullable=True,
        )

    op.execute(
        sa.text(
            f"UPDATE quiz_attempts SET score = NULL WHERE id IN ({UNGRADED_ATTEMPTS})"
        )
    )


def downgrade() -> None:
    op.execute(sa.text("UPDATE quiz_attempts SET score = 0.0 WHERE score IS NULL"))

    with op.batch_alter_table("quiz_attempts", schema=None) as batch_op:
        batch_op.alter_column(
            "score",
            existing_type=sa.Float(),
            nullable=False,
        )
