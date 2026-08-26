"""add generated citations

Revision ID: d1f6b3a8c724
Revises: 784a1eb8fba0
Create Date: 2026-08-27 00:00:00.000000

Per-claim source attribution for generated content. A quiz question and an
assistant conversation message each carry the resolved citations of the
passages the generation actually read, so reopening either renders the same
sources without a second provider call. Study guides need no column here:
they already persist as a JSON document in ``generated_outputs.content``.

Both columns are nullable JSON with no foreign key and no constraint, so
``ADD COLUMN`` is correct on SQLite and PostgreSQL alike and this revision
deliberately carries no dialect branch and no batch copy. The branching in
``c8d4a1f39e72`` exists because SQLite cannot add a foreign key in place;
nothing here needs it.

Nothing is backfilled. A question or message written before citations existed
has no truthful citation to record, and ``NULL`` states exactly that. The
downgrade drops both columns, discarding attribution that cannot be recomputed
without regenerating the content it belongs to.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d1f6b3a8c724"
down_revision: str | Sequence[str] | None = "784a1eb8fba0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("quiz_questions", sa.Column("citations", sa.JSON(), nullable=True))
    op.add_column(
        "conversation_messages", sa.Column("citations", sa.JSON(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("conversation_messages", "citations")
    op.drop_column("quiz_questions", "citations")
