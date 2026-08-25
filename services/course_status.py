from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.models import UploadedDocument
from schemas.progress import CourseStatus
from schemas.quiz_attempt import MASTERED_THRESHOLD

READY_DOCUMENT_STATUS = "ready"
PROCESSING_DOCUMENT_STATUSES = ("uploaded", "processing")


@dataclass(frozen=True)
class DocumentSignals:
    ready_count: int = 0
    processing_count: int = 0


NO_DOCUMENTS = DocumentSignals()


def document_signals(
    db: Session, course_ids: Sequence[int]
) -> dict[int, DocumentSignals]:
    if not course_ids:
        return {}

    counted = db.execute(
        select(
            UploadedDocument.course_id,
            UploadedDocument.status,
            func.count(UploadedDocument.id),
        )
        .where(
            UploadedDocument.course_id.in_(course_ids),
            UploadedDocument.status.in_(
                (READY_DOCUMENT_STATUS, *PROCESSING_DOCUMENT_STATUSES)
            ),
        )
        .group_by(UploadedDocument.course_id, UploadedDocument.status)
    ).all()

    tallies: dict[int, list[int]] = {}
    for course_id, status, count in counted:
        tally = tallies.setdefault(course_id, [0, 0])
        if status == READY_DOCUMENT_STATUS:
            tally[0] += count
        else:
            tally[1] += count

    return {
        course_id: DocumentSignals(ready_count=ready, processing_count=processing)
        for course_id, (ready, processing) in tallies.items()
    }


def derive_status(
    *,
    signals: DocumentSignals,
    attempts_count: int,
    average_score: float | None,
) -> CourseStatus:
    if average_score is not None and round(average_score * 100) >= MASTERED_THRESHOLD:
        return "mastered"
    if attempts_count > 0:
        return "practiced"
    if signals.processing_count > 0:
        return "processing"
    if signals.ready_count > 0:
        return "ready"
    return "no_documents"
