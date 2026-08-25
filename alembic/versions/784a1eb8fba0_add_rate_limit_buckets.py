"""add rate limit buckets

Revision ID: 784a1eb8fba0
Revises: c2a6e9f4d817
Create Date: 2026-08-25 00:00:00.000000

One fixed-window counter per abuse-control key (login/registration per IP and
per account, generation per user and feature). ``key`` is the primary key so a
window rollover updates the existing row in place instead of growing the
table with request volume. ``utils/rate_limit.py`` is the only module that
reads or writes this table. See docs/rate_limiting.md.

The revision only adds a table, so it needs no batch copy and is identical on
SQLite and PostgreSQL. The downgrade drops the table; buckets are ephemeral
counters with no audit value, so nothing is lost by discarding them.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "784a1eb8fba0"
down_revision: str | Sequence[str] | None = "c2a6e9f4d817"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "rate_limit_buckets",
        sa.Column("key", sa.String(length=255), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("violation_streak", sa.Integer(), server_default="0", nullable=False),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("key", name="pk_rate_limit_buckets"),
    )


def downgrade() -> None:
    op.drop_table("rate_limit_buckets")
