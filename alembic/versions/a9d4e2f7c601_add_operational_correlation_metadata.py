"""add operational correlation metadata

Revision ID: a9d4e2f7c601
Revises: e4c7a1b90d52
Create Date: 2026-09-08 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a9d4e2f7c601"
down_revision: str | None = "e4c7a1b90d52"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("processing_jobs") as batch_op:
        batch_op.add_column(sa.Column("parent_operation_id", sa.String(80)))
        batch_op.create_index(
            "ix_processing_jobs_parent_operation", ["parent_operation_id"]
        )
    with op.batch_alter_table("profile_processing_jobs") as batch_op:
        batch_op.add_column(sa.Column("parent_operation_id", sa.String(80)))
        batch_op.create_index(
            "ix_profile_processing_jobs_parent_operation", ["parent_operation_id"]
        )
    with op.batch_alter_table("generation_jobs") as batch_op:
        batch_op.add_column(sa.Column("parent_operation_id", sa.String(80)))
        batch_op.create_index(
            "ix_generation_jobs_parent_operation", ["parent_operation_id"]
        )
    with op.batch_alter_table("ai_usage_logs") as batch_op:
        batch_op.add_column(sa.Column("request_id", sa.String(64)))
        batch_op.add_column(sa.Column("operation_id", sa.String(80)))
        batch_op.add_column(sa.Column("job_id", sa.Integer()))
        batch_op.add_column(sa.Column("job_type", sa.String(50)))
        batch_op.add_column(sa.Column("attempt_number", sa.Integer()))
        batch_op.create_index(
            "ix_ai_usage_logs_operation_created", ["operation_id", "created_at"]
        )


def downgrade() -> None:
    with op.batch_alter_table("ai_usage_logs") as batch_op:
        batch_op.drop_index("ix_ai_usage_logs_operation_created")
        batch_op.drop_column("attempt_number")
        batch_op.drop_column("job_type")
        batch_op.drop_column("job_id")
        batch_op.drop_column("operation_id")
        batch_op.drop_column("request_id")
    with op.batch_alter_table("generation_jobs") as batch_op:
        batch_op.drop_index("ix_generation_jobs_parent_operation")
        batch_op.drop_column("parent_operation_id")
    with op.batch_alter_table("profile_processing_jobs") as batch_op:
        batch_op.drop_index("ix_profile_processing_jobs_parent_operation")
        batch_op.drop_column("parent_operation_id")
    with op.batch_alter_table("processing_jobs") as batch_op:
        batch_op.drop_index("ix_processing_jobs_parent_operation")
        batch_op.drop_column("parent_operation_id")
