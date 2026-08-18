"""add generated output attribution

Revision ID: c9b3d5e08f27
Revises: a1c5e7f9b203
Create Date: 2026-08-19 10:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op


revision: str = "c9b3d5e08f27"
down_revision: str | Sequence[str] | None = "a1c5e7f9b203"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _generated_outputs_table() -> sa.Table:
    meta = sa.MetaData()
    return sa.Table(
        "generated_outputs",
        meta,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("course_id", sa.Integer(), nullable=False),
        sa.Column("output_type", sa.String(length=30), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["course_id"],
            ["courses.id"],
            name="fk_generated_outputs_course_id_courses",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_generated_outputs"),
        sa.Index("ix_generated_outputs_course_id", "course_id"),
    )


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.add_column(
            "generated_outputs",
            sa.Column("user_id", sa.Integer(), nullable=True),
        )
        op.add_column(
            "generated_outputs",
            sa.Column("model_used", sa.String(length=150), nullable=True),
        )
        op.create_foreign_key(
            "fk_generated_outputs_user_id_users",
            "generated_outputs",
            "users",
            ["user_id"],
            ["id"],
            ondelete="SET NULL",
        )
        op.create_index(
            "ix_generated_outputs_user_id",
            "generated_outputs",
            ["user_id"],
            unique=False,
        )
        return

    with op.batch_alter_table(
        "generated_outputs", schema=None, copy_from=_generated_outputs_table()
    ) as batch_op:
        batch_op.add_column(sa.Column("user_id", sa.Integer(), nullable=True))
        batch_op.add_column(
            sa.Column("model_used", sa.String(length=150), nullable=True)
        )
        batch_op.create_foreign_key(
            "fk_generated_outputs_user_id_users",
            "users",
            ["user_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_index("ix_generated_outputs_user_id", ["user_id"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.drop_index("ix_generated_outputs_user_id", table_name="generated_outputs")
        op.drop_constraint(
            "fk_generated_outputs_user_id_users",
            "generated_outputs",
            type_="foreignkey",
        )
        op.drop_column("generated_outputs", "model_used")
        op.drop_column("generated_outputs", "user_id")
        return

    with op.batch_alter_table("generated_outputs", schema=None) as batch_op:
        batch_op.drop_index("ix_generated_outputs_user_id")
        batch_op.drop_column("model_used")
        batch_op.drop_column("user_id")
