"""add course syllabus and updated at

Revision ID: e5c1a7b39d64
Revises: b7e2a9d1c3f4
Create Date: 2026-08-18 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op


revision: str = "e5c1a7b39d64"
down_revision: str | Sequence[str] | None = "b7e2a9d1c3f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _courses_table() -> sa.Table:
    """Describe ``courses`` as ``a4fd52f56b91`` left it, for the SQLite rebuild."""
    meta = sa.MetaData()
    return sa.Table(
        "courses",
        meta,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("semester", sa.String(length=100), nullable=True),
        sa.Column("exam_date", sa.String(length=20), nullable=True),
        sa.Column("topics", sa.Text(), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column(
            "owner_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Index("ix_courses_owner_id", "owner_id"),
    )


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.add_column("courses", sa.Column("syllabus", sa.Text(), nullable=True))
        op.add_column(
            "courses",
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
        )
    else:
        with op.batch_alter_table(
            "courses", schema=None, copy_from=_courses_table()
        ) as batch_op:
            batch_op.add_column(sa.Column("syllabus", sa.Text(), nullable=True))
            batch_op.add_column(
                sa.Column(
                    "updated_at",
                    sa.DateTime(timezone=True),
                    server_default=sa.func.now(),
                    nullable=False,
                )
            )

    op.execute(
        "UPDATE courses SET syllabus = description WHERE description IS NOT NULL"
    )
    op.execute("UPDATE courses SET updated_at = created_at")


def downgrade() -> None:
    """Downgrade schema."""
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.drop_column("courses", "updated_at")
        op.drop_column("courses", "syllabus")
    else:
        with op.batch_alter_table("courses", schema=None) as batch_op:
            batch_op.drop_column("updated_at")
            batch_op.drop_column("syllabus")
