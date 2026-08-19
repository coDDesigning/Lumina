"""Idempotent purge for courses left tombstoned by an unfinished deletion.

``Course.is_deleted`` means one thing only: the course is pending erasure. It is
set by a delete that has not finished yet, by one that crashed part way, and by
the historical soft deletes that predate unconditional hard deletion. All three
want the same treatment, so this command reruns the ordinary hard delete against
every tombstone it finds.

Rerunning is always safe. Storage deletes ignore a missing object, vector
deletes no-op on an empty match, and the tombstone survives every failure, so a
course that could not be finished is simply picked up again by the next run.

The service layer commits each stage itself, so unlike ``embedding_backfill``
this worker must never add a commit of its own. It also keeps going after a
course it cannot finish, because a failure here is per course: one stranded
storage key must not block every other pending erasure.
"""

import argparse
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.database import SessionLocal
from backend.app.models import Course
from services.course import CourseDeletionError, CourseService
from services.vector_store import VectorStore, get_vector_store
from storage.base import Storage
from storage.dependencies import get_storage
from utils.exceptions import NotFoundException

logger = logging.getLogger(__name__)
SessionFactory = Callable[[], Session]


@dataclass
class PurgeReport:
    courses_examined: int = 0
    courses_purged: int = 0
    courses_failed: int = 0

    def summary(self) -> str:
        return (
            f"examined={self.courses_examined} purged={self.courses_purged} "
            f"failed={self.courses_failed}"
        )


def _tombstoned_course_ids(session: Session, *, course_id: int | None) -> list[int]:
    statement = select(Course.id).where(Course.is_deleted.is_(True)).order_by(Course.id)
    if course_id is not None:
        statement = statement.where(Course.id == course_id)
    return list(session.scalars(statement).all())


def run_purge(
    *,
    session_factory: SessionFactory = SessionLocal,
    storage: Storage | None = None,
    vector_store: VectorStore | None = None,
    course_id: int | None = None,
    dry_run: bool = False,
) -> PurgeReport:
    if storage is None:
        storage = get_storage()
    if vector_store is None:
        vector_store = get_vector_store()

    report = PurgeReport()
    with session_factory() as session:
        course_ids = _tombstoned_course_ids(session, course_id=course_id)

    for identifier in course_ids:
        with session_factory() as session:
            course = session.get(Course, identifier)
            if course is None or not course.is_deleted:
                continue
            report.courses_examined += 1
            if dry_run:
                continue
            try:
                CourseService.hard_delete_course(
                    session, identifier, storage, vector_store
                )
            except NotFoundException:
                continue
            except CourseDeletionError:
                logger.exception(
                    "Course %s could not be purged; its tombstone is retained",
                    identifier,
                )
                report.courses_failed += 1
                continue
            report.courses_purged += 1

    logger.info("Course purge finished: %s", report.summary())
    return report


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Purge courses left tombstoned by an unfinished deletion.",
    )
    parser.add_argument("--course-id", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    arguments = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO)
    report = run_purge(course_id=arguments.course_id, dry_run=arguments.dry_run)
    print(report.summary())
    if report.courses_failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
