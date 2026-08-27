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
import signal
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.config import (
    DEFAULT_COURSE_PURGE_INTERVAL_SECONDS,
    settings,
)
from backend.app.database import SessionLocal
from backend.app.models import Course
from backend.app.observability import configure_logging, emit_emf_metrics
from backend.app.readiness import ReadinessError, check_readiness
from services.course import CourseDeletionError, CourseService
from services.vector_store import VectorStore, get_vector_store
from storage.base import Storage
from storage.dependencies import get_storage
from utils.exceptions import NotFoundException

logger = logging.getLogger(__name__)
SessionFactory = Callable[[], Session]


class StopEvent(Protocol):
    def is_set(self) -> bool: ...

    def wait(self, timeout: float) -> bool: ...


class _SignalStopEvent:
    """Lock-free stop flag written by Python's main-thread signal handler."""

    def __init__(self) -> None:
        self.requested = False

    def is_set(self) -> bool:
        return self.requested

    def wait(self, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while not self.requested:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(0.1, remaining))
        return self.requested


@dataclass
class PurgeReport:
    courses_examined: int = 0
    courses_purged: int = 0
    courses_failed: int = 0
    aged_tombstones: int = 0
    oldest_tombstone_age_seconds: float = 0.0

    def summary(self) -> str:
        return (
            f"examined={self.courses_examined} purged={self.courses_purged} "
            f"failed={self.courses_failed} aged_tombstones={self.aged_tombstones}"
        )


def _tombstoned_course_ids(session: Session, *, course_id: int | None) -> list[int]:
    statement = select(Course.id).where(Course.is_deleted.is_(True)).order_by(Course.id)
    if course_id is not None:
        statement = statement.where(Course.id == course_id)
    return list(session.scalars(statement).all())


def check_purge_ready(
    *,
    session_factory: SessionFactory = SessionLocal,
    storage: Storage | None = None,
) -> None:
    if storage is None:
        storage = get_storage()
    with session_factory() as session:
        check_readiness(session, storage)


def run_purge(
    *,
    session_factory: SessionFactory = SessionLocal,
    storage: Storage | None = None,
    vector_store: VectorStore | None = None,
    course_id: int | None = None,
    dry_run: bool = False,
    stop_event: StopEvent | None = None,
    aged_threshold_seconds: float | None = None,
) -> PurgeReport:
    if storage is None:
        storage = get_storage()
    if vector_store is None:
        vector_store = get_vector_store()
    if aged_threshold_seconds is None:
        aged_threshold_seconds = settings.course_purge_interval_seconds

    report = PurgeReport()
    with session_factory() as session:
        course_ids = _tombstoned_course_ids(session, course_id=course_id)

    utc_now = datetime.now(timezone.utc)
    for identifier in course_ids:
        if stop_event is not None and stop_event.is_set():
            break
        with session_factory() as session:
            course = session.get(Course, identifier)
            if course is None or not course.is_deleted:
                continue
            report.courses_examined += 1

            tombstone_time = course.updated_at or course.created_at
            if tombstone_time is not None:
                if tombstone_time.tzinfo is None:
                    tombstone_time = tombstone_time.replace(tzinfo=timezone.utc)
                age_seconds = max(0.0, (utc_now - tombstone_time).total_seconds())
            else:
                age_seconds = 0.0

            if age_seconds > report.oldest_tombstone_age_seconds:
                report.oldest_tombstone_age_seconds = age_seconds

            if aged_threshold_seconds > 0 and age_seconds >= aged_threshold_seconds:
                report.aged_tombstones += 1
                logger.warning(
                    "Aged course tombstone detected for course %s (owner %s, age: %.1fs)",
                    identifier,
                    course.owner_id,
                    age_seconds,
                    extra={
                        "event": "aged_tombstone_detected",
                        "course_id": identifier,
                        "owner_id": course.owner_id,
                        "duration_ms": round(age_seconds * 1000, 1),
                        "runbook": "docs/runbooks/stranded_tombstone.md",
                    },
                )

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

    emit_emf_metrics(
        {
            "CoursesExamined": report.courses_examined,
            "CoursesPurged": report.courses_purged,
            "CoursesFailed": report.courses_failed,
            "AgedTombstones": report.aged_tombstones,
            "OldestTombstoneAgeSeconds": round(report.oldest_tombstone_age_seconds, 3),
        },
        dimensions={"Service": "course_purge", "Environment": settings.app_env},
        units={"OldestTombstoneAgeSeconds": "Seconds"},
    )
    logger.info("Course purge finished: %s", report.summary())
    return report


def run_purge_worker(
    *,
    interval_seconds: float = DEFAULT_COURSE_PURGE_INTERVAL_SECONDS,
    once: bool = False,
    stop_event: StopEvent | None = None,
    session_factory: SessionFactory = SessionLocal,
    storage: Storage | None = None,
    vector_store: VectorStore | None = None,
    course_id: int | None = None,
    dry_run: bool = False,
) -> None:
    stop = stop_event or threading.Event()
    if stop.is_set():
        return
    if storage is None:
        storage = get_storage()
    if vector_store is None:
        vector_store = get_vector_store()

    check_purge_ready(session_factory=session_factory, storage=storage)
    if stop.is_set():
        return

    logger.info("Course purge worker started (interval=%.1fs)", interval_seconds)
    try:
        while not stop.is_set():
            try:
                run_purge(
                    session_factory=session_factory,
                    storage=storage,
                    vector_store=vector_store,
                    course_id=course_id,
                    dry_run=dry_run,
                    stop_event=stop,
                )
            except Exception:
                logger.exception("Course purge execution failed")
            if once or stop.is_set():
                break
            stop.wait(interval_seconds)
    finally:
        logger.info("Course purge worker stopped")


def _install_shutdown_handlers(stop_event: _SignalStopEvent) -> None:
    def request_shutdown(_signum: int, _frame: object) -> None:
        stop_event.requested = True

    signal.signal(signal.SIGTERM, request_shutdown)
    signal.signal(signal.SIGINT, request_shutdown)


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Purge courses left tombstoned by an unfinished deletion.",
    )
    parser.add_argument("--course-id", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--interval-seconds",
        type=float,
        default=None,
        help="Run continuously with given sleep interval in seconds.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run at most one purge cycle and exit.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Check dependencies and readiness without purging.",
    )
    arguments = parser.parse_args(argv)

    configure_logging(service="maintenance", environment=settings.app_env)

    if arguments.check:
        try:
            check_purge_ready()
        except ReadinessError as exc:
            logger.error("Course purge readiness check failed: %s", exc)
            raise SystemExit(1) from None
        logger.info("Course purge readiness check succeeded")
        return

    if arguments.interval_seconds is not None:
        if arguments.interval_seconds < 0:
            parser.error("--interval-seconds must be a non-negative number")
        stop_event = _SignalStopEvent()
        _install_shutdown_handlers(stop_event)
        try:
            run_purge_worker(
                interval_seconds=arguments.interval_seconds,
                once=arguments.once,
                stop_event=stop_event,
                course_id=arguments.course_id,
                dry_run=arguments.dry_run,
            )
        except ReadinessError as exc:
            logger.error("Course purge readiness check failed: %s", exc)
            raise SystemExit(1) from None
        return

    report = run_purge(course_id=arguments.course_id, dry_run=arguments.dry_run)
    print(report.summary())
    if report.courses_failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
