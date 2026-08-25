"""add ai usage cost estimates

Revision ID: c2a6e9f4d817
Revises: f8b4c2d1e7a3
Create Date: 2026-08-24 21:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c2a6e9f4d817"
down_revision: str | Sequence[str] | None = "f8b4c2d1e7a3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    is_postgresql = op.get_bind().dialect.name == "postgresql"
    constraint_options = {"postgresql_not_valid": True} if is_postgresql else {}
    with op.batch_alter_table("ai_usage_logs", schema=None) as batch_op:
        batch_op.alter_column(
            "model",
            existing_type=sa.String(length=100),
            type_=sa.String(length=128),
            existing_nullable=False,
        )
        batch_op.add_column(sa.Column("estimated_cost_usd", sa.Float(), nullable=True))
        batch_op.add_column(
            sa.Column("pricing_version", sa.String(length=100), nullable=True)
        )
        batch_op.create_check_constraint(
            "ck_ai_usage_logs_estimated_cost_range",
            "estimated_cost_usd IS NULL OR "
            "(estimated_cost_usd >= 0 AND estimated_cost_usd <= 1000000)",
            **constraint_options,
        )
        batch_op.create_check_constraint(
            "ck_ai_usage_logs_pricing_pair",
            "(estimated_cost_usd IS NULL AND pricing_version IS NULL) OR "
            "(estimated_cost_usd IS NOT NULL AND pricing_version IS NOT NULL)",
            **constraint_options,
        )

    if is_postgresql:
        op.execute(
            "ALTER TABLE ai_usage_logs VALIDATE CONSTRAINT "
            "ck_ai_usage_logs_estimated_cost_range"
        )
        op.execute(
            "ALTER TABLE ai_usage_logs VALIDATE CONSTRAINT "
            "ck_ai_usage_logs_pricing_pair"
        )
        with op.get_context().autocommit_block():
            op.create_index(
                "ix_ai_usage_logs_success_created",
                "ai_usage_logs",
                ["success", "created_at"],
                unique=False,
                postgresql_concurrently=True,
            )
    else:
        op.create_index(
            "ix_ai_usage_logs_success_created",
            "ai_usage_logs",
            ["success", "created_at"],
            unique=False,
        )


def downgrade() -> None:
    is_postgresql = op.get_bind().dialect.name == "postgresql"
    if is_postgresql:
        with op.get_context().autocommit_block():
            op.drop_index(
                "ix_ai_usage_logs_success_created",
                table_name="ai_usage_logs",
                postgresql_concurrently=True,
            )
    else:
        op.drop_index("ix_ai_usage_logs_success_created", table_name="ai_usage_logs")

    with op.batch_alter_table("ai_usage_logs", schema=None) as batch_op:
        batch_op.drop_constraint("ck_ai_usage_logs_pricing_pair", type_="check")
        batch_op.drop_constraint("ck_ai_usage_logs_estimated_cost_range", type_="check")
        batch_op.drop_column("pricing_version")
        batch_op.drop_column("estimated_cost_usd")
        batch_op.alter_column(
            "model",
            existing_type=sa.String(length=128),
            type_=sa.String(length=100),
            existing_nullable=False,
        )
