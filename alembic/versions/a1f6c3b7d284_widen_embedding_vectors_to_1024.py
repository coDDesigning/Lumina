"""widen embedding vectors to 1024

Revision ID: a1f6c3b7d284
Revises: 9f4c1d7b2e83
Create Date: 2026-08-30 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op


revision: str = "a1f6c3b7d284"
down_revision: str | Sequence[str] | None = "9f4c1d7b2e83"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NEW_DIMENSIONS = 1024
OLD_DIMENSIONS = 768

_VECTOR_TABLES = (
    (
        "chunk_embeddings",
        "ck_chunk_embeddings_ck_chunk_embeddings_dimensions_supported",
        "ix_chunk_embeddings_embedding_hnsw",
    ),
    (
        "profile_chunk_embeddings",
        "ck_profile_chunk_embeddings_profile_dimensions_supported",
        "ix_profile_chunk_embeddings_embedding_hnsw",
    ),
    (
        "profile_knowledge_embeddings",
        "ck_profile_knowledge_embeddings_dimensions_supported",
        None,
    ),
)

_FIND_DIMENSION_CHECKS = sa.text(
    "SELECT c.conname FROM pg_constraint c "
    "JOIN pg_class t ON t.oid = c.conrelid "
    "WHERE t.relname = :table AND c.contype = 'c' "
    "AND pg_get_constraintdef(c.oid) LIKE '%dimensions%'"
)


def _rewidth_postgresql(width: int) -> None:
    bind = op.get_bind()
    for table, check_name, index_name in _VECTOR_TABLES:
        if index_name is not None:
            op.execute(sa.text(f"DROP INDEX IF EXISTS {index_name}"))
        existing = (
            bind.execute(_FIND_DIMENSION_CHECKS, {"table": table}).scalars().all()
        )
        for name in existing:
            op.execute(sa.text(f'ALTER TABLE {table} DROP CONSTRAINT "{name}"'))
        op.execute(
            sa.text(f"ALTER TABLE {table} ALTER COLUMN embedding TYPE vector({width})")
        )
        op.create_check_constraint(op.f(check_name), table, f"dimensions = {width}")
        if index_name is not None:
            op.execute(
                sa.text(
                    f"CREATE INDEX {index_name} ON {table} "
                    "USING hnsw (embedding vector_cosine_ops)"
                )
            )


def _rewidth_sqlite(width: int) -> None:
    for table, check_name, _ in _VECTOR_TABLES:
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.drop_constraint(batch_op.f(check_name), type_="check")
            batch_op.create_check_constraint(
                batch_op.f(check_name), f"dimensions = {width}"
            )


def _rewidth(width: int) -> None:
    for table, _, _ in _VECTOR_TABLES:
        op.execute(sa.text(f"DELETE FROM {table}"))

    if op.get_bind().dialect.name == "postgresql":
        _rewidth_postgresql(width)
    else:
        _rewidth_sqlite(width)


def upgrade() -> None:
    _rewidth(NEW_DIMENSIONS)


def downgrade() -> None:
    _rewidth(OLD_DIMENSIONS)
