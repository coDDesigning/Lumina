"""add composite indexes for growing course and progress reads

Revision ID: 15bb8ad6d0f1
Revises: f3c8d05a2b16
Create Date: 2026-08-26 00:00:00.000000

Course material reads filter ready documents and chunks by course before
streaming them in corpus order. Progress, activity, and history reads filter
generated outputs, conversations, and attempts by user/course or quiz and
then aggregate or order by their timestamps. Single-column foreign-key
indexes do not provide those combined access paths.

`CREATE INDEX CONCURRENTLY` avoids locking these tables against writes while
the index builds on PostgreSQL; `autocommit_block()` is required because
`CONCURRENTLY` cannot run inside a transaction. SQLite has no equivalent
concern and no `CONCURRENTLY` keyword, so it uses a plain `CREATE INDEX`.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "15bb8ad6d0f1"
down_revision: str | Sequence[str] | None = "f3c8d05a2b16"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEXES = (
    (
        "ix_uploaded_documents_course_status_created",
        "uploaded_documents",
        ["course_id", "status", "created_at", "id"],
    ),
    (
        "ix_document_chunks_course_document_index",
        "document_chunks",
        ["course_id", "document_id", "chunk_index", "id"],
    ),
    (
        "ix_generated_outputs_user_course_created",
        "generated_outputs",
        ["user_id", "course_id", "created_at", "id"],
    ),
    (
        "ix_generated_outputs_user_created",
        "generated_outputs",
        ["user_id", "created_at", "id"],
    ),
    (
        "ix_conversations_user_course_updated",
        "conversations",
        ["user_id", "course_id", "updated_at", "id"],
    ),
    (
        "ix_quiz_attempts_quiz_user_created",
        "quiz_attempts",
        ["quiz_id", "user_id", "created_at", "id"],
    ),
    (
        "ix_quiz_attempts_user_created",
        "quiz_attempts",
        ["user_id", "created_at", "id"],
    ),
    (
        "ix_quiz_attempts_quiz_created",
        "quiz_attempts",
        ["quiz_id", "created_at", "id"],
    ),
)

_REPLACED_PREFIX_INDEXES = (
    ("ix_quiz_attempts_user_id", "quiz_attempts", ["user_id"]),
    ("ix_quiz_attempts_quiz_id", "quiz_attempts", ["quiz_id"]),
)


def upgrade() -> None:
    is_postgresql = op.get_bind().dialect.name == "postgresql"
    if is_postgresql:
        with op.get_context().autocommit_block():
            for name, table, columns in _INDEXES:
                op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")
                op.create_index(
                    name,
                    table,
                    columns,
                    unique=False,
                    postgresql_concurrently=True,
                )
            for name, _table, _columns in _REPLACED_PREFIX_INDEXES:
                op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")
    else:
        for name, table, columns in _INDEXES:
            op.create_index(name, table, columns, unique=False, if_not_exists=True)
        for name, table, _columns in _REPLACED_PREFIX_INDEXES:
            op.drop_index(name, table_name=table, if_exists=True)


def downgrade() -> None:
    is_postgresql = op.get_bind().dialect.name == "postgresql"
    if is_postgresql:
        with op.get_context().autocommit_block():
            for name, _table, _columns in _INDEXES:
                op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")
            for name, table, columns in _REPLACED_PREFIX_INDEXES:
                op.create_index(
                    name,
                    table,
                    columns,
                    unique=False,
                    postgresql_concurrently=True,
                    if_not_exists=True,
                )
    else:
        for name, table, _columns in _INDEXES:
            op.drop_index(name, table_name=table, if_exists=True)
        for name, table, columns in _REPLACED_PREFIX_INDEXES:
            op.create_index(name, table, columns, unique=False, if_not_exists=True)
