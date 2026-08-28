"""authentication hardening: email verification state and tokens

Revision ID: c7a2e5b91d63
Revises: e1a2b3c4d5e6
Create Date: 2026-08-28 12:00:00.000000

``users.email_verified_at`` records when an address was proven reachable rather
than a boolean saying that it was, because the instant is what makes the fact
auditable afterwards. It is nullable with no default and existing rows are left
null: every account that registered before this revision reached its balance
without proving anything, and back-filling a verification timestamp onto them
would be a claim the database cannot support. Self-hosted deployments never set
it at all, which is why nothing downstream may read null as "not yet allowed"
without also checking whether this deployment asks.

``email_verification_tokens`` stores the SHA-256 digest of each issued link and
never the link. ``token_hash`` is unique because a digest identifies exactly one
issued token, and the lookup that redeems a link is a point read on it.
``consumed_at`` carries single use and ``expires_at`` carries expiry, both as
row state compared inside the statement that claims the row, so redemption needs
no scheduler and two clicks on one link cannot both win.

This revision is add-only: one ``CREATE TABLE`` and one ``ADD COLUMN`` without a
default, both native on SQLite and PostgreSQL. There is no data move and the
downgrade is a real inverse.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
import backend.app.models

revision: str = "c7a2e5b91d63"
down_revision: str | Sequence[str] | None = "e1a2b3c4d5e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("email_verified_at", backend.app.models.UTCDateTime(), nullable=True),
    )

    op.create_table(
        "email_verification_tokens",
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
            name=op.f("fk_email_verification_tokens_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_email_verification_tokens")),
        sa.UniqueConstraint(
            "token_hash", name=op.f("uq_email_verification_tokens_token_hash")
        ),
    )
    with op.batch_alter_table("email_verification_tokens", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_email_verification_tokens_user_id"),
            ["user_id"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("email_verification_tokens", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_email_verification_tokens_user_id"))

    op.drop_table("email_verification_tokens")

    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_column("email_verified_at")
