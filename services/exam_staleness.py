"""Whether a stored exam plan still describes the course it was built from.

Staleness is computed on read and never written. The plan carries a fingerprint
of its inputs; a read recomputes that fingerprint against the course as it is
now and reports what differs. Nothing here mutates a historical output, because
a plan is the record of a decision and rewriting it would erase the reasoning
the student actually studied from.

A difference is reported, never acted on. Regeneration is always the student's
explicit next step, so the passage of time, a new upload, or another quiz
attempt can make a plan stale without spending a credit or replacing anything.
"""

import hashlib
from collections.abc import Mapping, Sequence
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.models import (
    Course,
    CourseTopic,
    Quiz,
    QuizAttempt,
    QuizAttemptAnswer,
    QuizQuestion,
    UploadedDocument,
)
from schemas.exam_mode import ExamPlanFingerprint
from services.exam_ranking import RANKING_POLICY_VERSION
from services.exam_topics import TOPIC_KEY_VERSION, canonical_topic_key, match_topic_key

FINGERPRINT_VERSION = 1

READY_STATUS = "ready"
PAST_EXAM_MATERIAL_KIND = "past_exam"

DIGEST_SEPARATOR = "\x1f"

REASON_EXAM_DATE_CHANGED = "exam_date_changed"
REASON_SYLLABUS_CHANGED = "syllabus_changed"
REASON_COURSE_TOPICS_CHANGED = "course_topics_changed"
REASON_DOCUMENTS_ADDED = "documents_added"
REASON_DOCUMENTS_REMOVED = "documents_removed"
REASON_DOCUMENTS_CHANGED = "documents_changed"
REASON_DOCUMENTS_REPROCESSED = "documents_reprocessed"
REASON_PAST_EXAMS_CHANGED = "past_exams_changed"
REASON_NEW_QUIZ_RESULTS = "new_quiz_results"
REASON_MASTERY_CHANGED = "mastery_changed"
REASON_SELECTION_CHANGED = "selection_changed"
REASON_RANKING_POLICY_UPDATED = "ranking_policy_updated"
REASON_TOPIC_KEYS_UPDATED = "topic_keys_updated"

# The exam date moving changes the countdown, not the priorities. Reporting it
# as a reason to spend another credit would be a nag, so it makes a plan stale
# without requiring a rescan.
RESCAN_REASONS = frozenset(
    {
        REASON_SYLLABUS_CHANGED,
        REASON_COURSE_TOPICS_CHANGED,
        REASON_DOCUMENTS_ADDED,
        REASON_DOCUMENTS_REMOVED,
        REASON_DOCUMENTS_CHANGED,
        REASON_DOCUMENTS_REPROCESSED,
        REASON_PAST_EXAMS_CHANGED,
        REASON_NEW_QUIZ_RESULTS,
        REASON_MASTERY_CHANGED,
        REASON_SELECTION_CHANGED,
        REASON_RANKING_POLICY_UPDATED,
        REASON_TOPIC_KEYS_UPDATED,
    }
)

UNTAGGED_TOPIC = "Untagged"


def _digest(parts: Sequence[str]) -> str | None:
    """A stable digest of already-normalized parts, or ``None`` for nothing.

    ``hashlib`` rather than the builtin ``hash``, whose seed is randomized per
    process: that would report every plan in the system stale after a restart.
    """
    if not parts:
        return None
    joined = DIGEST_SEPARATOR.join(parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def syllabus_digest(syllabus: str | None) -> str | None:
    """A digest of the syllabus with whitespace collapsed.

    Reflowing a paragraph is not a change to the course, so the digest must not
    see one.
    """
    if not syllabus or not syllabus.strip():
        return None
    return _digest([" ".join(syllabus.split())])


def course_topic_keys(db: Session, course_id: int) -> list[str]:
    """The canonical keys of the course's declared topics, sorted.

    Keys rather than names, so retyping "graph traversal" as "Graph Traversal"
    is correctly not a change, and so the fact that every topic row is deleted
    and reinserted on each course save is invisible here.
    """
    names = db.scalars(
        select(CourseTopic.name).where(CourseTopic.course_id == course_id)
    ).all()
    keys = {canonical_topic_key(name) for name in names}
    return sorted(key for key in keys if key)


def mastery_rows(db: Session, course_id: int, user_id: int) -> list[tuple[str, bool]]:
    """Every graded answer in this course for one user, as (label, correct).

    The predicate mirrors ``QuizAttemptService._aggregate`` exactly, including
    excluding answers the grader could not score: an ungraded answer is not
    evidence in either direction and must not move mastery.
    """
    rows = db.execute(
        select(
            QuizQuestion.topic,
            QuizAttemptAnswer.is_correct,
            QuizAttemptAnswer.topic,
        )
        .join(QuizAttemptAnswer, QuizAttemptAnswer.quiz_question_id == QuizQuestion.id)
        .join(QuizAttempt, QuizAttempt.id == QuizAttemptAnswer.attempt_id)
        .join(Quiz, Quiz.id == QuizQuestion.quiz_id)
        .where(
            Quiz.course_id == course_id,
            QuizAttempt.user_id == user_id,
            QuizAttemptAnswer.is_correct.is_not(None),
        )
    ).all()

    resolved: list[tuple[str, bool]] = []
    for question_topic, is_correct, answer_topic in rows:
        label = (answer_topic or question_topic or "").strip() or UNTAGGED_TOPIC
        resolved.append((label, bool(is_correct)))
    return resolved


def mastery_digest(
    rows: Sequence[tuple[str, bool]], index: Mapping[str, str]
) -> str | None:
    """A digest of per-topic mastery, keyed the way the plan keyed it."""
    totals: dict[str, list[int]] = {}
    for label, correct in rows:
        if label == UNTAGGED_TOPIC:
            continue
        key = match_topic_key(label, index)
        if key is None:
            continue
        bucket = totals.setdefault(key, [0, 0])
        bucket[0] += 1
        bucket[1] += int(correct)
    if not totals:
        return None
    return _digest(
        [
            f"{key}{DIGEST_SEPARATOR}{answered}{DIGEST_SEPARATOR}{correct}"
            for key, (answered, correct) in sorted(totals.items())
        ]
    )


def build_fingerprint(
    db: Session,
    course_id: int,
    *,
    analysis_output_id: int,
    mastery_user_id: int,
    topic_index: Mapping[str, str],
    selected_topic_keys: Sequence[str],
    high_priority_topic_keys: Sequence[str],
) -> ExamPlanFingerprint:
    """Snapshot every input that would change a ranking, for one course."""
    course = db.get(Course, course_id)
    exam_date: date | None = course.exam_date if course is not None else None
    syllabus = course.syllabus if course is not None else None

    documents = db.execute(
        select(
            UploadedDocument.id,
            UploadedDocument.material_kind,
            UploadedDocument.updated_at,
        ).where(
            UploadedDocument.course_id == course_id,
            UploadedDocument.status == READY_STATUS,
        )
    ).all()

    ready_ids = sorted(str(row.id) for row in documents)
    past_exam_ids = sorted(
        str(row.id) for row in documents if row.material_kind == PAST_EXAM_MATERIAL_KIND
    )
    # updated_at carries an onupdate clause, so reprocessing the same document
    # moves the digest without any need to walk its chunks.
    revisions = sorted(
        f"{row.id}{DIGEST_SEPARATOR}"
        f"{row.updated_at.isoformat() if row.updated_at else ''}"
        for row in documents
    )

    rows = mastery_rows(db, course_id, mastery_user_id)

    return ExamPlanFingerprint(
        mastery_user_id=mastery_user_id,
        analysis_output_id=analysis_output_id,
        exam_date=exam_date,
        syllabus_digest=syllabus_digest(syllabus),
        course_topic_keys=course_topic_keys(db, course_id),
        ready_document_ids=ready_ids,
        past_exam_document_ids=past_exam_ids,
        document_revision_digest=_digest(revisions),
        graded_answer_count=len(rows),
        mastery_digest=mastery_digest(rows, topic_index),
        selected_topic_keys=sorted(selected_topic_keys),
        high_priority_topic_keys=sorted(high_priority_topic_keys),
        ranking_policy_version=RANKING_POLICY_VERSION,
        topic_key_version=TOPIC_KEY_VERSION,
    )


def _document_reason(stored: Sequence[str], current: Sequence[str]) -> str | None:
    added = set(current) - set(stored)
    removed = set(stored) - set(current)
    if added and removed:
        return REASON_DOCUMENTS_CHANGED
    if added:
        return REASON_DOCUMENTS_ADDED
    if removed:
        return REASON_DOCUMENTS_REMOVED
    return None


def compare_fingerprints(
    stored: Mapping[str, object] | None, current: ExamPlanFingerprint
) -> tuple[str, ...]:
    """Which recorded inputs have moved since the plan was written.

    Pure. The stored side arrives as a raw mapping parsed permissively, so a
    key it does not carry is skipped rather than treated as a change: a plan
    written before a field existed loses that one check instead of reporting a
    difference it cannot know about.
    """
    if not stored:
        return ()

    reasons: set[str] = set()

    def moved(field: str, value: object) -> bool:
        if field not in stored:
            return False
        return stored.get(field) != value

    if moved("exam_date", current.exam_date.isoformat() if current.exam_date else None):
        reasons.add(REASON_EXAM_DATE_CHANGED)
    if moved("syllabus_digest", current.syllabus_digest):
        reasons.add(REASON_SYLLABUS_CHANGED)
    if moved("course_topic_keys", current.course_topic_keys):
        reasons.add(REASON_COURSE_TOPICS_CHANGED)

    if "ready_document_ids" in stored:
        stored_ready = stored.get("ready_document_ids") or []
        if isinstance(stored_ready, list):
            reason = _document_reason(stored_ready, current.ready_document_ids)
            if reason is not None:
                reasons.add(reason)

    if moved("past_exam_document_ids", current.past_exam_document_ids):
        reasons.add(REASON_PAST_EXAMS_CHANGED)
    if moved("document_revision_digest", current.document_revision_digest):
        reasons.add(REASON_DOCUMENTS_REPROCESSED)

    count_moved = moved("graded_answer_count", current.graded_answer_count)
    if count_moved:
        reasons.add(REASON_NEW_QUIZ_RESULTS)
    elif moved("mastery_digest", current.mastery_digest):
        # Same number of graded answers, different distribution: a regrade or a
        # deleted attempt. Reported on its own so a plain new attempt reads as
        # one reason rather than two.
        reasons.add(REASON_MASTERY_CHANGED)

    if moved("selected_topic_keys", current.selected_topic_keys) or moved(
        "high_priority_topic_keys", current.high_priority_topic_keys
    ):
        reasons.add(REASON_SELECTION_CHANGED)

    if moved("ranking_policy_version", current.ranking_policy_version):
        reasons.add(REASON_RANKING_POLICY_UPDATED)
    if moved("topic_key_version", current.topic_key_version):
        reasons.add(REASON_TOPIC_KEYS_UPDATED)

    return tuple(sorted(reasons))


def requires_rescan(reasons: Sequence[str]) -> bool:
    """Whether any reported difference actually changes a ranking input."""
    return any(reason in RESCAN_REASONS for reason in reasons)


def graded_answer_count(db: Session, course_id: int, user_id: int) -> int:
    """How many graded answers this user has in this course."""
    return int(
        db.scalar(
            select(func.count())
            .select_from(QuizAttemptAnswer)
            .join(QuizQuestion, QuizQuestion.id == QuizAttemptAnswer.quiz_question_id)
            .join(QuizAttempt, QuizAttempt.id == QuizAttemptAnswer.attempt_id)
            .join(Quiz, Quiz.id == QuizQuestion.quiz_id)
            .where(
                Quiz.course_id == course_id,
                QuizAttempt.user_id == user_id,
                QuizAttemptAnswer.is_correct.is_not(None),
            )
        )
        or 0
    )
