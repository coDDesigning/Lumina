"""What a student has recently generated and attempted, across their courses.

One owner-scoped chronological read over two sources. A generated quiz writes
both a ``quizzes`` row and a ``generated_outputs`` row, so generations are taken
from ``generated_outputs`` alone: counting the quiz row as well would report one
piece of work twice. Attempts are separate events, and a retake is legitimately
its own event.

Stored generation documents are read permissively, the way history reads are:
one row whose JSON no longer parses loses its topic, never the whole feed.
"""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models import Course, GeneratedOutput, Quiz, QuizAttempt
from schemas.activity import QUIZ_ATTEMPT_ACTION, ActivityItem
from utils.json_documents import parse_json_object

WHOLE_COURSE_FOCUS = "all topics"

_GENERATION_RANK = 0
_ATTEMPT_RANK = 1


def _document(raw: str | None, *, field: str, row_id: int) -> dict | None:
    return parse_json_object(raw, field=field, table="generated_outputs", row_id=row_id)


def _topic_of(row: GeneratedOutput) -> str | None:
    document = _document(
        row.generation_settings, field="generation_settings", row_id=row.id
    )
    if document is None:
        return None
    focus = document.get("topic_focus")
    if not isinstance(focus, str):
        return None
    trimmed = focus.strip()
    if not trimmed or trimmed.lower() == WHOLE_COURSE_FOCUS:
        return None
    return trimmed


def _quiz_id_of(row: GeneratedOutput) -> int | None:
    if row.output_type != "quiz":
        return None
    document = _document(row.content, field="content", row_id=row.id)
    if document is None:
        return None
    quiz_id = document.get("quiz_id")
    return quiz_id if isinstance(quiz_id, int) else None


class ActivityService:
    @staticmethod
    def _owned_courses(db: Session, user_id: int) -> dict[int, str]:
        return {
            course_id: title
            for course_id, title in db.execute(
                select(Course.id, Course.title).where(
                    Course.owner_id == user_id, Course.is_deleted.is_(False)
                )
            ).all()
        }

    @classmethod
    def list_recent(
        cls, db: Session, *, user_id: int, limit: int
    ) -> list[ActivityItem]:
        courses = cls._owned_courses(db, user_id)
        if not courses:
            return []

        course_ids = list(courses)
        ranked: list[tuple[datetime, int, int, ActivityItem]] = []

        outputs = db.scalars(
            select(GeneratedOutput)
            .where(
                GeneratedOutput.course_id.in_(course_ids),
                GeneratedOutput.user_id == user_id,
            )
            .order_by(GeneratedOutput.created_at.desc(), GeneratedOutput.id.desc())
            .limit(limit)
        ).all()

        for row in outputs:
            ranked.append(
                (
                    row.created_at,
                    _GENERATION_RANK,
                    row.id,
                    ActivityItem(
                        kind="generation",
                        action_type=row.output_type,
                        course_id=row.course_id,
                        course_title=courses[row.course_id],
                        occurred_at=row.created_at,
                        output_id=row.id,
                        quiz_id=_quiz_id_of(row),
                        topic=_topic_of(row),
                    ),
                )
            )

        attempts = db.execute(
            select(
                QuizAttempt.id,
                QuizAttempt.quiz_id,
                QuizAttempt.score,
                QuizAttempt.created_at,
                Quiz.course_id,
            )
            .join(Quiz, Quiz.id == QuizAttempt.quiz_id)
            .where(
                Quiz.course_id.in_(course_ids),
                QuizAttempt.user_id == user_id,
            )
            .order_by(QuizAttempt.created_at.desc(), QuizAttempt.id.desc())
            .limit(limit)
        ).all()

        for attempt_id, quiz_id, score, created_at, course_id in attempts:
            ranked.append(
                (
                    created_at,
                    _ATTEMPT_RANK,
                    attempt_id,
                    ActivityItem(
                        kind="attempt",
                        action_type=QUIZ_ATTEMPT_ACTION,
                        course_id=course_id,
                        course_title=courses[course_id],
                        occurred_at=created_at,
                        quiz_id=quiz_id,
                        attempt_id=attempt_id,
                        score=score,
                    ),
                )
            )

        ranked.sort(key=lambda entry: entry[:3], reverse=True)

        return [item for _, _, _, item in ranked[:limit]]
