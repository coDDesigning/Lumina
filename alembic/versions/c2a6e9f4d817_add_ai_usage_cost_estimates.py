"""add ai usage cost estimates

Revision ID: c2a6e9f4d817
Revises: 3e8b1a4c7f20
Create Date: 2026-08-24 21:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c2a6e9f4d817"
down_revision: str | Sequence[str] | None = "3e8b1a4c7f20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    is_postgresql = op.get_bind().dialect.name == "postgresql"
    if is_postgresql:
        # autocommit_block commits these changes before the concurrent index. Keep
        # every operation restart-safe so an interrupted index build can be retried.
        op.execute("ALTER TABLE ai_usage_logs ALTER COLUMN model TYPE VARCHAR(128)")
        op.execute(
            "ALTER TABLE ai_usage_logs ADD COLUMN IF NOT EXISTS "
            "estimated_cost_usd DOUBLE PRECISION"
        )
        op.execute(
            "ALTER TABLE ai_usage_logs ADD COLUMN IF NOT EXISTS "
            "pricing_version VARCHAR(100)"
        )
        op.execute(
            """
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_constraint
                    WHERE conname = 'ck_ai_usage_logs_estimated_cost_range'
                      AND conrelid = 'ai_usage_logs'::regclass
                ) THEN
                    ALTER TABLE ai_usage_logs ADD CONSTRAINT
                        ck_ai_usage_logs_estimated_cost_range CHECK (
                            estimated_cost_usd IS NULL OR
                            (estimated_cost_usd >= 0 AND estimated_cost_usd <= 1000000)
                        ) NOT VALID;
                END IF;
            END
            $$
            """
        )
        op.execute(
            """
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_constraint
                    WHERE conname = 'ck_ai_usage_logs_pricing_pair'
                      AND conrelid = 'ai_usage_logs'::regclass
                ) THEN
                    ALTER TABLE ai_usage_logs ADD CONSTRAINT
                        ck_ai_usage_logs_pricing_pair CHECK (
                            (estimated_cost_usd IS NULL AND pricing_version IS NULL) OR
                            (estimated_cost_usd IS NOT NULL AND pricing_version IS NOT NULL)
                        ) NOT VALID;
                END IF;
            END
            $$
            """
        )
        op.execute(
            "ALTER TABLE ai_usage_logs VALIDATE CONSTRAINT "
            "ck_ai_usage_logs_estimated_cost_range"
        )
        op.execute(
            "ALTER TABLE ai_usage_logs VALIDATE CONSTRAINT "
            "ck_ai_usage_logs_pricing_pair"
        )
        with op.get_context().autocommit_block():
            op.execute(
                "DROP INDEX CONCURRENTLY IF EXISTS ix_ai_usage_logs_success_created"
            )
            op.create_index(
                "ix_ai_usage_logs_success_created",
                "ai_usage_logs",
                ["success", "created_at"],
                unique=False,
                postgresql_concurrently=True,
            )
    else:
        with op.batch_alter_table("ai_usage_logs", schema=None) as batch_op:
            batch_op.alter_column(
                "model",
                existing_type=sa.String(length=100),
                type_=sa.String(length=128),
                existing_nullable=False,
            )
            batch_op.add_column(
                sa.Column("estimated_cost_usd", sa.Float(), nullable=True)
            )
            batch_op.add_column(
                sa.Column("pricing_version", sa.String(length=100), nullable=True)
            )
            batch_op.create_check_constraint(
                "ck_ai_usage_logs_estimated_cost_range",
                "estimated_cost_usd IS NULL OR "
                "(estimated_cost_usd >= 0 AND estimated_cost_usd <= 1000000)",
            )
            batch_op.create_check_constraint(
                "ck_ai_usage_logs_pricing_pair",
                "(estimated_cost_usd IS NULL AND pricing_version IS NULL) OR "
                "(estimated_cost_usd IS NOT NULL AND pricing_version IS NOT NULL)",
            )
        op.create_index(
            "ix_ai_usage_logs_success_created",
            "ai_usage_logs",
            ["success", "created_at"],
            unique=False,
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.scalar(
        sa.text("SELECT COUNT(*) FROM ai_usage_logs WHERE length(model) > 100")
    ):
        raise RuntimeError(
            "Cannot downgrade while ai_usage_logs contains model identifiers "
            "longer than 100 characters."
        )

    is_postgresql = bind.dialect.name == "postgresql"
    if is_postgresql:
        with op.get_context().autocommit_block():
            op.execute(
                "DROP INDEX CONCURRENTLY IF EXISTS ix_ai_usage_logs_success_created"
            )
    else:
        op.drop_index("ix_ai_usage_logs_success_created", table_name="ai_usage_logs")

    if is_postgresql:
        op.execute(
            "ALTER TABLE ai_usage_logs DROP CONSTRAINT IF EXISTS "
            "ck_ai_usage_logs_pricing_pair"
        )
        op.execute(
            "ALTER TABLE ai_usage_logs DROP CONSTRAINT IF EXISTS "
            "ck_ai_usage_logs_estimated_cost_range"
        )

    with op.batch_alter_table("ai_usage_logs", schema=None) as batch_op:
        if not is_postgresql:
            batch_op.drop_constraint("ck_ai_usage_logs_pricing_pair", type_="check")
            batch_op.drop_constraint(
                "ck_ai_usage_logs_estimated_cost_range", type_="check"
            )
        batch_op.drop_column("pricing_version")
        batch_op.drop_column("estimated_cost_usd")
        batch_op.alter_column(
            "model",
            existing_type=sa.String(length=128),
            type_=sa.String(length=100),
            existing_nullable=False,
        )
