"""add learner context fields

Revision ID: a3d9e5c17b48
Revises: b9c1d4e7f2a6
Create Date: 2026-08-21 15:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a3d9e5c17b48"
down_revision: str | Sequence[str] | None = "b9c1d4e7f2a6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_EDUCATION_LEVELS = (
    "high_school",
    "undergraduate",
    "graduate",
    "professional_other",
    "unspecified",
)
_EDUCATION_LEVELS_SQL = ", ".join(f"'{level}'" for level in _EDUCATION_LEVELS)

_MATERIAL_KINDS = (
    "lecture_notes",
    "slides",
    "textbook",
    "syllabus",
    "assignment",
    "past_exam",
    "article",
    "notes",
    "other",
    "unspecified",
)
_MATERIAL_KINDS_SQL = ", ".join(f"'{kind}'" for kind in _MATERIAL_KINDS)


def upgrade() -> None:
    with op.batch_alter_table("courses", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("subject_area", sa.String(length=100), nullable=True)
        )
        batch_op.add_column(
            sa.Column(
                "education_level",
                sa.String(length=20),
                server_default="unspecified",
                nullable=False,
            )
        )
        batch_op.create_check_constraint(
            batch_op.f("ck_courses_education_level_valid"),
            f"education_level IN ({_EDUCATION_LEVELS_SQL})",
        )

    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "education_level",
                sa.String(length=20),
                server_default="unspecified",
                nullable=False,
            )
        )
        batch_op.create_check_constraint(
            batch_op.f("ck_users_education_level_valid"),
            f"education_level IN ({_EDUCATION_LEVELS_SQL})",
        )

    with op.batch_alter_table("uploaded_documents", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "material_kind",
                sa.String(length=20),
                server_default="unspecified",
                nullable=False,
            )
        )
        batch_op.create_check_constraint(
            batch_op.f("ck_uploaded_documents_material_kind_valid"),
            f"material_kind IN ({_MATERIAL_KINDS_SQL})",
        )


def downgrade() -> None:
    with op.batch_alter_table("uploaded_documents", schema=None) as batch_op:
        batch_op.drop_constraint(
            batch_op.f("ck_uploaded_documents_material_kind_valid"), type_="check"
        )
        batch_op.drop_column("material_kind")

    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_constraint(
            batch_op.f("ck_users_education_level_valid"), type_="check"
        )
        batch_op.drop_column("education_level")

    with op.batch_alter_table("courses", schema=None) as batch_op:
        batch_op.drop_constraint(
            batch_op.f("ck_courses_education_level_valid"), type_="check"
        )
        batch_op.drop_column("education_level")
        batch_op.drop_column("subject_area")
