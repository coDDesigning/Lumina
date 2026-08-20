"""add the auditable credit transaction ledger

Revision ID: d7f3a2c48e15
Revises: c8d4a1f39e72
Create Date: 2026-08-20 18:00:00.000000

Before this revision ``users.credits`` was a bare mutable balance: it reported
what the balance was and never why. ``credit_transactions`` makes every change
attributable, and for every account with a non-null balance the balance becomes
derivable as the sum of that account's deltas.

The revision only adds a table, so it needs no batch copy and is identical on
SQLite and PostgreSQL.

Two unique constraints carry behavior rather than merely describing shape.
``(user_id, grant_period)`` is the idempotency key that makes the lazy monthly
grant grant at most once per account per period; ``refunds_transaction_id``
makes a charge refundable at most once. Both rely on composite and single-column
uniques treating NULL as distinct, which SQLite and PostgreSQL agree on, so the
many rows carrying no period and no refund pointer never collide.

The backfill writes exactly one ``migration_reconciliation`` row per user with a
non-null balance, carrying that whole balance as its delta. It deliberately
invents no history: a balance of 37 does not prove an initial 50 followed by 13
charges, and fabricating that sequence would be false audit data. Administrators
hold a null balance, meaning unmetered, so they are skipped entirely and stay
outside the ledger. ``grant_period`` is left null on these rows so an existing
user still receives their grant during the migration month instead of being
silently skipped by the idempotency key.

Alembic runs a revision once, but the backfill is still written as a guarded
insert so a manual rerun during testing cannot duplicate a baseline.

The downgrade drops the table. That is lossy for audit history by nature, and
truthfully so: a ledger cannot be folded back into one scalar. ``users.credits``
is never touched by either direction, so balances survive the round trip intact.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d7f3a2c48e15"
down_revision: str | Sequence[str] | None = "c8d4a1f39e72"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


MIGRATION_RECONCILIATION = "migration_reconciliation"
ACTOR_MIGRATION = "migration"
RECONCILIATION_NOTE = "Legacy balance reconciled at migration"


def upgrade() -> None:
    op.create_table(
        "credit_transactions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("delta", sa.Float(), nullable=False),
        sa.Column("balance_after", sa.Float(), nullable=False),
        sa.Column("reason", sa.String(length=40), nullable=False),
        sa.Column("actor_type", sa.String(length=20), nullable=False),
        sa.Column("actor_user_id", sa.Integer(), nullable=True),
        sa.Column("actor_label", sa.String(length=255), nullable=True),
        sa.Column("source_type", sa.String(length=50), nullable=True),
        sa.Column("source_id", sa.Integer(), nullable=True),
        sa.Column("refunds_transaction_id", sa.Integer(), nullable=True),
        sa.Column("grant_period", sa.String(length=7), nullable=True),
        sa.Column("note", sa.String(length=500), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_credit_transactions_user_id_users",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            name="fk_credit_transactions_actor_user_id_users",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["refunds_transaction_id"],
            ["credit_transactions.id"],
            name="fk_credit_transactions_refunds_transaction_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_credit_transactions"),
        sa.UniqueConstraint(
            "user_id",
            "grant_period",
            name="uq_credit_transactions_user_grant_period",
        ),
        sa.UniqueConstraint(
            "refunds_transaction_id",
            name="uq_credit_transactions_refunds_transaction_id",
        ),
    )
    op.create_index(
        "ix_credit_transactions_user_id",
        "credit_transactions",
        ["user_id"],
    )
    op.create_index(
        "ix_credit_transactions_user_created",
        "credit_transactions",
        ["user_id", "created_at"],
    )

    _reconcile_existing_balances()


def _reconcile_existing_balances() -> None:
    """Give every metered account one truthful baseline row."""
    connection = op.get_bind()
    users = sa.table(
        "users",
        sa.column("id", sa.Integer),
        sa.column("credits", sa.Float),
    )
    ledger = sa.table(
        "credit_transactions",
        sa.column("user_id", sa.Integer),
        sa.column("delta", sa.Float),
        sa.column("balance_after", sa.Float),
        sa.column("reason", sa.String),
        sa.column("actor_type", sa.String),
        sa.column("note", sa.String),
    )

    already_reconciled = (
        sa.select(ledger.c.user_id)
        .where(ledger.c.reason == MIGRATION_RECONCILIATION)
        .scalar_subquery()
    )
    pending = connection.execute(
        sa.select(users.c.id, users.c.credits)
        .where(users.c.credits.is_not(None))
        .where(users.c.id.not_in(already_reconciled))
        .order_by(users.c.id)
    ).all()
    if not pending:
        return

    connection.execute(
        ledger.insert(),
        [
            {
                "user_id": user_id,
                "delta": float(credits),
                "balance_after": float(credits),
                "reason": MIGRATION_RECONCILIATION,
                "actor_type": ACTOR_MIGRATION,
                "note": RECONCILIATION_NOTE,
            }
            for user_id, credits in pending
        ],
    )


def downgrade() -> None:
    op.drop_index("ix_credit_transactions_user_created", "credit_transactions")
    op.drop_index("ix_credit_transactions_user_id", "credit_transactions")
    op.drop_table("credit_transactions")
