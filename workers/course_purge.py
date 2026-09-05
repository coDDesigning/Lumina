"""Idempotent purge for courses left tombstoned by an unfinished deletion.

``Course.is_deleted`` means one thing only: the course is pending erasure. It is
set by a delete that has not finished yet, by one that crashed part way, and by
the historical soft deletes that predate unconditional hard deletion. All three
want the same treatment, so this command reruns the ordinary hard delete against
every tombstone it finds.

Rerunning is always safe. Storage deletes ignore a missing object, vector
deletes no-op on an empty match, and the tombstone survives every failure, so a
course that could not be finished is simply picked up again by the next run.

Documents carry the same kind of tombstone. ``UploadedDocument.status ==
'deleting'`` is written by phase one of ``DocumentService.delete_document`` and
survives a storage or vector-store failure in phase two, at which point the row
is hidden from every read endpoint and only a reconciler can finish it, so this
worker reruns that deletion too. A document tombstone is only picked up once it
has aged past a short grace period, because a delete request that is still
inside its own storage call holds exactly the same tombstone.

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
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.config import (
    DEFAULT_COURSE_PURGE_INTERVAL_SECONDS,
    settings,
)
from backend.app.database import SessionLocal
from backend.app.models import Course, ProfileDocument, UploadedDocument
from backend.app.observability import configure_logging, emit_emf_metrics
from backend.app.readiness import ReadinessError, check_readiness
from services.course import CourseDeletionError, CourseService
from services.document import (
    DocumentActiveError,
    DocumentDeletionError,
    DocumentService,
)
from services.profile_document import (
    ProfileDocumentDeletionError,
    ProfileDocumentService,
)
from services.vector_store import VectorStore, get_vector_store
from storage.base import Storage
from storage.dependencies import get_storage
from utils.exceptions import NotFoundException

logger = logging.getLogger(__name__)
SessionFactory = Callable[[], Session]
# A delete request holds the document tombstone across its own storage call, so
# a tombstone younger than this is assumed to belong to a request still running.
DOCUMENT_TOMBSTONE_GRACE_SECONDS = 300.0


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


@dataclass
class DocumentPurgeReport:
    documents_examined: int = 0
    documents_purged: int = 0
    documents_failed: int = 0
    profile_documents_examined: int = 0
    profile_documents_purged: int = 0
    profile_documents_failed: int = 0
    aged_tombstones: int = 0
    oldest_tombstone_age_seconds: float = 0.0

    def summary(self) -> str:
        return (
            f"documents_examined={self.documents_examined} "
            f"documents_purged={self.documents_purged} "
            f"documents_failed={self.documents_failed} "
            f"profile_documents_examined={self.profile_documents_examined} "
            f"profile_documents_purged={self.profile_documents_purged} "
            f"profile_documents_failed={self.profile_documents_failed} "
            f"aged_tombstones={self.aged_tombstones}"
        )


def _tombstone_age_seconds(utc_now: datetime, marked_at: datetime | None) -> float:
    if marked_at is None:
        return 0.0
    if marked_at.tzinfo is None:
        marked_at = marked_at.replace(tzinfo=timezone.utc)
    return max(0.0, (utc_now - marked_at).total_seconds())


def _tombstoned_course_ids(session: Session, *, course_id: int | None) -> list[int]:
    statement = select(Course.id).where(Course.is_deleted.is_(True)).order_by(Course.id)
    if course_id is not None:
        statement = statement.where(Course.id == course_id)
    return list(session.scalars(statement).all())


def _tombstoned_document_ids(
    session: Session,
    *,
    course_id: int | None,
    document_id: UUID | None,
) -> list[UUID]:
    """Select document tombstones whose course is not itself pending erasure.

    A tombstoned course is erased wholesale by ``run_purge``, which removes its
    documents with it, so reconciling them one by one here would only race it.
    """
    statement = (
        select(UploadedDocument.id)
        .join(Course, Course.id == UploadedDocument.course_id)
        .where(
            UploadedDocument.status == "deleting",
            Course.is_deleted.is_(False),
        )
        .order_by(UploadedDocument.id)
    )
    if course_id is not None:
        statement = statement.where(UploadedDocument.course_id == course_id)
    if document_id is not None:
        statement = statement.where(UploadedDocument.id == document_id)
    return list(session.scalars(statement).all())


def _tombstoned_profile_document_ids(
    session: Session,
    *,
    document_id: UUID | None,
) -> list[UUID]:
    statement = (
        select(ProfileDocument.id)
        .where(ProfileDocument.status == "deleting")
        .order_by(ProfileDocument.id)
    )
    if document_id is not None:
        statement = statement.where(ProfileDocument.id == document_id)
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
                    session,
                    identifier,
                    storage,
                    vector_store,
                    operation_timeout_seconds=(
                        settings.course_purge_operation_timeout_seconds
                    ),
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


def run_document_purge(
    *,
    session_factory: SessionFactory = SessionLocal,
    storage: Storage | None = None,
    vector_store: VectorStore | None = None,
    course_id: int | None = None,
    document_id: UUID | None = None,
    dry_run: bool = False,
    stop_event: StopEvent | None = None,
    aged_threshold_seconds: float | None = None,
    grace_seconds: float = DOCUMENT_TOMBSTONE_GRACE_SECONDS,
) -> DocumentPurgeReport:
    """Finish document deletions a storage or vector-store failure left undone.

    ``DocumentService.delete_document`` short-circuits its first phase for a row
    that is already tombstoned and tolerates an object that is already gone, so
    rerunning it is idempotent. ``force`` is used because a failed phase two may
    have left a job requeued against the document, which is not a reason to keep
    a row the owner already asked to delete.

    Profile documents carry the same tombstone through the same two phases and
    are reconciled in a second pass, unless the run is scoped to one course:
    a profile document belongs to a user, not to a course.
    """
    if storage is None:
        storage = get_storage()
    if vector_store is None:
        vector_store = get_vector_store()
    if aged_threshold_seconds is None:
        aged_threshold_seconds = settings.course_purge_interval_seconds

    report = DocumentPurgeReport()
    with session_factory() as session:
        document_ids = _tombstoned_document_ids(
            session,
            course_id=course_id,
            document_id=document_id,
        )

    utc_now = datetime.now(timezone.utc)
    for identifier in document_ids:
        if stop_event is not None and stop_event.is_set():
            break
        with session_factory() as session:
            document = session.get(UploadedDocument, identifier)
            if document is None or document.status != "deleting":
                continue
            owning_course_id = document.course_id
            age_seconds = _tombstone_age_seconds(
                utc_now,
                document.updated_at or document.created_at,
            )
            report.documents_examined += 1
            if age_seconds > report.oldest_tombstone_age_seconds:
                report.oldest_tombstone_age_seconds = age_seconds

            if aged_threshold_seconds > 0 and age_seconds >= aged_threshold_seconds:
                report.aged_tombstones += 1
                logger.warning(
                    "Aged document tombstone detected for document %s "
                    "(course %s, age: %.1fs)",
                    identifier,
                    owning_course_id,
                    age_seconds,
                    extra={
                        "event": "aged_tombstone_detected",
                        "document_id": str(identifier),
                        "course_id": owning_course_id,
                        "duration_ms": round(age_seconds * 1000, 1),
                        "runbook": "docs/runbooks/stranded_tombstone.md",
                    },
                )

            # A delete request owns its tombstone until it returns, so only a
            # tombstone that outlived the request is this worker's to finish.
            if age_seconds < grace_seconds or dry_run:
                continue

            try:
                DocumentService.delete_document(
                    session,
                    storage,
                    identifier,
                    owning_course_id,
                    vector_store,
                    force=True,
                )
            except NotFoundException:
                logger.warning(
                    "Document %s tombstone could not be resolved to a deletable row",
                    identifier,
                )
                report.documents_failed += 1
                continue
            except (DocumentActiveError, DocumentDeletionError):
                logger.exception(
                    "Document %s could not be purged; its tombstone is retained",
                    identifier,
                )
                report.documents_failed += 1
                continue
            report.documents_purged += 1

    if course_id is None:
        _purge_profile_documents(
            report,
            session_factory=session_factory,
            storage=storage,
            vector_store=vector_store,
            document_id=document_id,
            dry_run=dry_run,
            stop_event=stop_event,
            aged_threshold_seconds=aged_threshold_seconds,
            grace_seconds=grace_seconds,
            utc_now=utc_now,
        )

    emit_emf_metrics(
        {
            "DocumentsExamined": report.documents_examined,
            "DocumentsPurged": report.documents_purged,
            "DocumentsFailed": report.documents_failed,
            "ProfileDocumentsExamined": report.profile_documents_examined,
            "ProfileDocumentsPurged": report.profile_documents_purged,
            "ProfileDocumentsFailed": report.profile_documents_failed,
            "AgedDocumentTombstones": report.aged_tombstones,
            "OldestDocumentTombstoneAgeSeconds": round(
                report.oldest_tombstone_age_seconds, 3
            ),
        },
        dimensions={"Service": "document_purge", "Environment": settings.app_env},
        units={"OldestDocumentTombstoneAgeSeconds": "Seconds"},
    )
    logger.info("Document purge finished: %s", report.summary())
    return report


def _purge_profile_documents(
    report: DocumentPurgeReport,
    *,
    session_factory: SessionFactory,
    storage: Storage,
    vector_store: VectorStore,
    document_id: UUID | None,
    dry_run: bool,
    stop_event: StopEvent | None,
    aged_threshold_seconds: float,
    grace_seconds: float,
    utc_now: datetime,
) -> None:
    """Finish profile document deletions stranded the same way, into ``report``."""
    with session_factory() as session:
        document_ids = _tombstoned_profile_document_ids(
            session,
            document_id=document_id,
        )

    for identifier in document_ids:
        if stop_event is not None and stop_event.is_set():
            break
        with session_factory() as session:
            document = session.get(ProfileDocument, identifier)
            if document is None or document.status != "deleting":
                continue
            owner_id = document.user_id
            age_seconds = _tombstone_age_seconds(
                utc_now,
                document.updated_at or document.created_at,
            )
            report.profile_documents_examined += 1
            if age_seconds > report.oldest_tombstone_age_seconds:
                report.oldest_tombstone_age_seconds = age_seconds

            if aged_threshold_seconds > 0 and age_seconds >= aged_threshold_seconds:
                report.aged_tombstones += 1
                logger.warning(
                    "Aged profile document tombstone detected for document %s "
                    "(user %s, age: %.1fs)",
                    identifier,
                    owner_id,
                    age_seconds,
                    extra={
                        "event": "aged_tombstone_detected",
                        "document_id": str(identifier),
                        "user_id": owner_id,
                        "duration_ms": round(age_seconds * 1000, 1),
                        "runbook": "docs/runbooks/stranded_tombstone.md",
                    },
                )

            if age_seconds < grace_seconds or dry_run:
                continue

            try:
                ProfileDocumentService.delete_document(
                    session,
                    storage,
                    owner_id,
                    identifier,
                    vector_store,
                )
            except NotFoundException:
                logger.warning(
                    "Profile document %s tombstone could not be resolved "
                    "to a deletable row",
                    identifier,
                )
                report.profile_documents_failed += 1
                continue
            except ProfileDocumentDeletionError:
                logger.exception(
                    "Profile document %s could not be purged; "
                    "its tombstone is retained",
                    identifier,
                )
                report.profile_documents_failed += 1
                continue
            report.profile_documents_purged += 1


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
            try:
                run_document_purge(
                    session_factory=session_factory,
                    storage=storage,
                    vector_store=vector_store,
                    course_id=course_id,
                    dry_run=dry_run,
                    stop_event=stop,
                )
            except Exception:
                logger.exception("Document purge execution failed")
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
        description=(
            "Purge courses and documents left tombstoned by an unfinished deletion."
        ),
    )
    parser.add_argument("--course-id", type=int, default=None)
    parser.add_argument(
        "--document-id",
        type=UUID,
        default=None,
        help="Scope the document tombstone pass to one document.",
    )
    parser.add_argument(
        "--document-grace-seconds",
        type=float,
        default=DOCUMENT_TOMBSTONE_GRACE_SECONDS,
        help=(
            "Age a document tombstone must reach before it is finished, so a "
            "delete request still in flight is left alone."
        ),
    )
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
    document_report = run_document_purge(
        course_id=arguments.course_id,
        document_id=arguments.document_id,
        dry_run=arguments.dry_run,
        grace_seconds=arguments.document_grace_seconds,
    )
    print(document_report.summary())
    if (
        report.courses_failed
        or document_report.documents_failed
        or document_report.profile_documents_failed
    ):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
