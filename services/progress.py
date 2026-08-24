from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.models import (
    Conversation,
    Course,
    GeneratedOutput,
    Quiz,
    QuizAttempt,
)
from schemas.progress import CourseProgressSummary


class ProgressService:
    @staticmethod
    def _owned_course_ids(db: Session, user_id: int) -> list[int]:
        return list(
            db.scalars(
                select(Course.id)
                .where(Course.owner_id == user_id, Course.is_deleted.is_(False))
                .order_by(Course.id)
            ).all()
        )

    @classmethod
    def list_course_summaries(
        cls, db: Session, *, user_id: int
    ) -> list[CourseProgressSummary]:
        course_ids = cls._owned_course_ids(db, user_id)
        if not course_ids:
            return []

        attempts = {
            course_id: (count, average, last_attempt)
            for course_id, count, average, last_attempt in db.execute(
                select(
                    Quiz.course_id,
                    func.count(QuizAttempt.id),
                    func.avg(QuizAttempt.score),
                    func.max(QuizAttempt.created_at),
                )
                .join(Quiz, Quiz.id == QuizAttempt.quiz_id)
                .where(
                    Quiz.course_id.in_(course_ids),
                    QuizAttempt.user_id == user_id,
                )
                .group_by(Quiz.course_id)
            ).all()
        }

        outputs = dict(
            db.execute(
                select(
                    GeneratedOutput.course_id,
                    func.max(GeneratedOutput.created_at),
                )
                .where(
                    GeneratedOutput.course_id.in_(course_ids),
                    GeneratedOutput.user_id == user_id,
                )
                .group_by(GeneratedOutput.course_id)
            ).all()
        )

        conversations = dict(
            db.execute(
                select(Conversation.course_id, func.max(Conversation.updated_at))
                .where(
                    Conversation.course_id.in_(course_ids),
                    Conversation.user_id == user_id,
                )
                .group_by(Conversation.course_id)
            ).all()
        )

        summaries: list[CourseProgressSummary] = []
        for course_id in course_ids:
            count, average, last_attempt = attempts.get(course_id, (0, None, None))
            average_score = float(average) if average is not None else None
            stamps = [
                stamp
                for stamp in (
                    last_attempt,
                    outputs.get(course_id),
                    conversations.get(course_id),
                )
                if stamp is not None
            ]
            summaries.append(
                CourseProgressSummary(
                    course_id=course_id,
                    attempts_count=count,
                    average_score=average_score,
                    completion=(
                        min(1.0, max(0.0, average_score))
                        if average_score is not None
                        else None
                    ),
                    last_activity=max(stamps) if stamps else None,
                )
            )

        return summaries
