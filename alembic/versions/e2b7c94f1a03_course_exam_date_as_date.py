"""course exam_date as a database date

Revision ID: e2b7c94f1a03
Revises: d1f6b3a8c724
Create Date: 2026-08-27 09:00:00.000000

``courses.exam_date`` was ``String(20)``, which accepted anything a client sent
and sorted lexicographically. It becomes a real ``DATE`` so the database
validates it and ``ORDER BY exam_date`` is chronological.

Remediation rule for legacy values. A stored value converts only if it matches
``^\\d{4}-\\d{2}-\\d{2}$`` and parses with ``date.fromisoformat``. The regular
expression runs first deliberately: CPython 3.11 widened ``fromisoformat`` to
accept forms such as ``20260904``, and the conversion rule must not depend on
which interpreter runs the migration.

Every other value -- the empty string, bare years such as ``2026``, partial
dates such as ``2026-09``, impossible dates such as ``2026-02-30``, and free
text -- is set to NULL and logged as

    course id=<id> exam_date=<original> discarded: not an ISO date

An operator re-enters those dates from the migration log. Coercing ``2026`` to
``2026-01-01`` was rejected: it would show a student an exam date they never
entered.

The downgrade converts dates back to ISO text. It cannot resurrect a value this
upgrade nulled, because the original text is gone.

On SQLite the converted values are rewritten after the type change rather than
carried through it. Alembic's batch recreation copies the column with
``CAST(exam_date AS DATE)``, and ``DATE`` carries NUMERIC affinity in SQLite, so
that cast silently turns ``'2026-12-17'`` into the integer ``2026``. Rewriting
from the values this migration already parsed avoids the affinity entirely.
PostgreSQL needs no rewrite: ``USING btrim(exam_date)::date`` converts in place.
"""

import logging
import re
from collections.abc import Sequence
from datetime import date

import sqlalchemy as sa

from alembic import op

revision: str = "e2b7c94f1a03"
down_revision: str | Sequence[str] | None = "d1f6b3a8c724"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger("alembic.runtime.migration")

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

_courses = sa.table(
    "courses",
    sa.column("id", sa.Integer),
    sa.column("exam_date", sa.String),
)

_dated_courses = sa.table(
    "courses",
    sa.column("id", sa.Integer),
    sa.column("exam_date", sa.Date),
)


def _convertible(value: str) -> bool:
    if not _ISO_DATE.match(value):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def upgrade() -> None:
    connection = op.get_bind()
    rows = connection.execute(
        sa.select(_courses.c.id, _courses.c.exam_date).where(
            _courses.c.exam_date.is_not(None)
        )
    ).all()

    converted: list[dict[str, object]] = []
    discarded: list[int] = []
    for row in rows:
        original = str(row.exam_date)
        value = original.strip()
        if _convertible(value):
            converted.append({"course_id": row.id, "value": date.fromisoformat(value)})
            continue
        discarded.append(row.id)
        logger.warning(
            "course id=%s exam_date=%r discarded: not an ISO date", row.id, original
        )

    if discarded:
        connection.execute(
            _courses.update().where(_courses.c.id.in_(discarded)).values(exam_date=None)
        )

    if connection.dialect.name == "postgresql":
        op.alter_column(
            "courses",
            "exam_date",
            existing_type=sa.String(length=20),
            type_=sa.Date(),
            existing_nullable=True,
            postgresql_using="btrim(exam_date)::date",
        )
        return

    with op.batch_alter_table("courses", schema=None) as batch_op:
        batch_op.alter_column(
            "exam_date",
            existing_type=sa.String(length=20),
            type_=sa.Date(),
            existing_nullable=True,
        )

    if converted:
        connection.execute(
            _dated_courses.update()
            .where(_dated_courses.c.id == sa.bindparam("course_id"))
            .values(exam_date=sa.bindparam("value", type_=sa.Date)),
            converted,
        )


def downgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name == "postgresql":
        op.alter_column(
            "courses",
            "exam_date",
            existing_type=sa.Date(),
            type_=sa.String(length=20),
            existing_nullable=True,
            postgresql_using="to_char(exam_date, 'YYYY-MM-DD')",
        )
        return

    with op.batch_alter_table("courses", schema=None) as batch_op:
        batch_op.alter_column(
            "exam_date",
            existing_type=sa.Date(),
            type_=sa.String(length=20),
            existing_nullable=True,
        )
