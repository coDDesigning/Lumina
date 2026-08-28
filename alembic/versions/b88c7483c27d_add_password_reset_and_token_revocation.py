"""add_password_reset_and_token_revocation

Revision ID: b88c7483c27d
Revises: c7a2e5b91d63
Create Date: 2026-08-28 14:54:07.902625
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
import backend


revision: str = "b88c7483c27d"
down_revision: str | Sequence[str] | None = "c7a2e5b91d63"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "password_reset_tokens",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", backend.app.models.UTCDateTime(), nullable=False),
        sa.Column("consumed_at", backend.app.models.UTCDateTime(), nullable=True),
        sa.Column(
            "created_at",
            backend.app.models.UTCDateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_password_reset_tokens_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_password_reset_tokens")),
        sa.UniqueConstraint(
            "token_hash", name=op.f("uq_password_reset_tokens_token_hash")
        ),
    )
    with op.batch_alter_table("password_reset_tokens", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_password_reset_tokens_user_id"), ["user_id"], unique=False
        )

    op.create_table(
        "revoked_tokens",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("jti", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("expires_at", backend.app.models.UTCDateTime(), nullable=False),
        sa.Column(
            "created_at",
            backend.app.models.UTCDateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_revoked_tokens_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_revoked_tokens")),
    )
    with op.batch_alter_table("revoked_tokens", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_revoked_tokens_expires_at"), ["expires_at"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_revoked_tokens_jti"), ["jti"], unique=True)
        batch_op.create_index(
            batch_op.f("ix_revoked_tokens_user_id"), ["user_id"], unique=False
        )

    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "tokens_valid_after", backend.app.models.UTCDateTime(), nullable=True
            )
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_column("tokens_valid_after")

    with op.batch_alter_table("revoked_tokens", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_revoked_tokens_user_id"))
        batch_op.drop_index(batch_op.f("ix_revoked_tokens_jti"))
        batch_op.drop_index(batch_op.f("ix_revoked_tokens_expires_at"))
    op.drop_table("revoked_tokens")

    with op.batch_alter_table("password_reset_tokens", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_password_reset_tokens_user_id"))
    op.drop_table("password_reset_tokens")
