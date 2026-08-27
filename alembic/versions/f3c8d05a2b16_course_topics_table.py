"""course topics as rows instead of a comma-joined blob

Revision ID: f3c8d05a2b16
Revises: e2b7c94f1a03
Create Date: 2026-08-27 09:05:00.000000

``courses.topics`` was a free-text column that the frontend split on ``,`` and
rejoined with ``", "``. That convention could not express a topic containing a
comma and made a topic lookup a substring match. Topics become rows in
``course_topics``, so a boundary is a row boundary and a course-scoped topic
query is ordinary SQL.

``position`` preserves the order the student wrote, and is deliberately not
unique: the course form replaces the whole set, so the service deletes and
reinserts rather than reordering in place. Uniqueness is ``(course_id, name)``
rather than a functional index on ``lower(name)`` so the constraint is
identical on SQLite and PostgreSQL; case-insensitive de-duplication happens in
the service layer.

The backfill splits the legacy column on ``,`` one final time, strips each
part, drops empties, de-duplicates case-insensitively keeping the first
casing, truncates a name over 100 characters, and caps a course at 50 topics.
Truncations and drops are logged.

The downgrade recreates the text column and rejoins with ``", "``. That is
lossy for a topic containing a comma, which is exactly the defect this
revision removes; it is the price of a reversible schema.
"""

import logging
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f3c8d05a2b16"
down_revision: str | Sequence[str] | None = "e2b7c94f1a03"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger("alembic.runtime.migration")

MAX_TOPICS = 50
MAX_TOPIC_LENGTH = 100

_courses = sa.table(
    "courses",
    sa.column("id", sa.Integer),
    sa.column("topics", sa.Text),
)

_course_topics = sa.table(
    "course_topics",
    sa.column("id", sa.Integer),
    sa.column("course_id", sa.Integer),
    sa.column("position", sa.Integer),
    sa.column("name", sa.String),
)


def _split(course_id: int, blob: str) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for part in blob.split(","):
        topic = part.strip()
        if not topic:
            continue
        if len(topic) > MAX_TOPIC_LENGTH:
            logger.warning(
                "course id=%s topic truncated to %s characters: %r",
                course_id,
                MAX_TOPIC_LENGTH,
                topic,
            )
            topic = topic[:MAX_TOPIC_LENGTH]
        key = topic.casefold()
        if key in seen:
            continue
        seen.add(key)
        names.append(topic)
    if len(names) > MAX_TOPICS:
        logger.warning(
            "course id=%s has %s topics; keeping the first %s",
            course_id,
            len(names),
            MAX_TOPICS,
        )
        names = names[:MAX_TOPICS]
    return names


def upgrade() -> None:
    op.create_table(
        "course_topics",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("course_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_course_topics")),
        sa.UniqueConstraint(
            "course_id", "name", name=op.f("uq_course_topics_course_id_name")
        ),
        sa.CheckConstraint(
            "position >= 0", name=op.f("ck_course_topics_position_nonnegative")
        ),
        sa.ForeignKeyConstraint(
            ["course_id"],
            ["courses.id"],
            name=op.f("fk_course_topics_course_id_courses"),
            ondelete="CASCADE",
        ),
    )
    op.create_index(op.f("ix_course_topics_course_id"), "course_topics", ["course_id"])

    connection = op.get_bind()
    rows = connection.execute(
        sa.select(_courses.c.id, _courses.c.topics).where(
            _courses.c.topics.is_not(None)
        )
    ).all()

    payload = [
        {"course_id": row.id, "position": position, "name": name}
        for row in rows
        for position, name in enumerate(_split(row.id, str(row.topics)))
    ]
    if payload:
        connection.execute(sa.insert(_course_topics), payload)

    op.drop_column("courses", "topics")


def downgrade() -> None:
    op.add_column("courses", sa.Column("topics", sa.Text(), nullable=True))

    connection = op.get_bind()
    rows = connection.execute(
        sa.select(_course_topics.c.course_id, _course_topics.c.name).order_by(
            _course_topics.c.course_id, _course_topics.c.position
        )
    ).all()

    joined: dict[int, list[str]] = {}
    for row in rows:
        joined.setdefault(row.course_id, []).append(str(row.name))

    for course_id, names in joined.items():
        connection.execute(
            _courses.update()
            .where(_courses.c.id == course_id)
            .values(topics=", ".join(names))
        )

    op.drop_index(op.f("ix_course_topics_course_id"), table_name="course_topics")
    op.drop_table("course_topics")
