"""require pgvector 0.8 for filtered HNSW search

Revision ID: f5a7c2d9e104
Revises: c8d4a1f39e72
Create Date: 2026-08-20 22:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f5a7c2d9e104"
down_revision: str | Sequence[str] | None = "c8d4a1f39e72"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade pgvector to the newest version installed by PostgreSQL."""
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    op.execute(sa.text("CREATE EXTENSION IF NOT EXISTS vector"))
    op.execute(sa.text("ALTER EXTENSION vector UPDATE"))
    version = bind.scalar(
        sa.text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
    )
    parts = tuple(int(part) for part in str(version).split(".")[:3])
    if parts < (0, 8, 0):
        raise RuntimeError(
            f"pgvector 0.8.0 or newer is required for filtered HNSW search, got {version}."
        )


def downgrade() -> None:
    """Extension downgrades are unsupported; schema remains compatible."""
