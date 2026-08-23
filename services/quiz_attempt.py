from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from backend.app.models import (
    Progress,
    Quiz,
    QuizAttempt,
    QuizAttemptAnswer,
    QuizQuestion,
)
from schemas.quiz import OPTION_BASED_QUESTION_TYPES, QuizQuestionType
from schemas.quiz_attempt import (
    MASTERED_THRESHOLD,
    NEEDS_REVIEW_THRESHOLD,
    CourseProgressResponse,
    MasteryStatus,
    QuizAnswerResult,
    QuizAnswerSubmission,
    QuizAttemptRequest,
    QuizAttemptResponse,
    QuizHistoryItem,
    TopicMastery,
)
from services.credits import ChargeReceipt, CreditService
from services.quiz import QuizService
from services.quiz_grading import ProviderFactory, QuizGradingService
from utils.exceptions import BadRequestException

UNTAGGED_TOPIC = "Untagged"


def _mastery_status(percentage: int) -> MasteryStatus:
    if percentage >= MASTERED_THRESHOLD:
        return MasteryStatus.MASTERED
    if percentage < NEEDS_REVIEW_THRESHOLD:
        return MasteryStatus.NEEDS_REVIEW
    return MasteryStatus.IN_PROGRESS


@dataclass(frozen=True)
class _ProgressAggregate:
    """Everything both the stored ``Progress`` row and the API read derive from.

    The two used to compute this separately from the same queries. Keeping one
    derivation is what stops the materialized row and the response it is meant
    to mirror from drifting apart.
    """

    attempts_count: int
    average_score: float | None
    completion: float
    correct_count: int
    incorrect_count: int
    topic_mastery: list[TopicMastery]
    weak_topics: list[str]
    quiz_history: list[QuizHistoryItem]

    @property
    def total_questions_answered(self) -> int:
        return self.correct_count + self.incorrect_count


class QuizAttemptService:
    @staticmethod
    def _validate_submissions(
        request: QuizAttemptRequest, questions: dict[int, QuizQuestion]
    ) -> dict[int, QuizAnswerSubmission]:
        """Match every submitted answer to a question of this quiz and its type.

        An answer given in the wrong form for its question type is rejected
        rather than silently graded as unanswered, so a client bug surfaces as a
        400 instead of a zero the student cannot explain.
        """
        submitted: dict[int, QuizAnswerSubmission] = {}

        for answer in request.answers:
            question = questions.get(answer.question_id)
            if question is None:
                raise BadRequestException(
                    "One of the submitted answers does not belong to this quiz."
                )
            if answer.question_id in submitted:
                raise BadRequestException(
                    "Each question may only be answered once per attempt."
                )

            question_type = QuizQuestionType(question.question_type)
            selected = answer.selected_option_index
            written = answer.text_response

            if question_type in OPTION_BASED_QUESTION_TYPES:
                if written is not None:
                    raise BadRequestException(
                        "A multiple choice or true/false question is answered by "
                        "selecting an option, not by writing text."
                    )
                options = question.options or []
                if selected is not None and selected >= len(options):
                    raise BadRequestException(
                        "One of the submitted answers selects an option that does "
                        "not exist."
                    )
            elif selected is not None:
                raise BadRequestException(
                    "A short answer or open ended question is answered by writing "
                    "text, not by selecting an option."
                )

            submitted[answer.question_id] = answer

        return submitted

    @classmethod
    def record_attempt(
        cls,
        db: Session,
        course_id: int,
        quiz_id: int,
        request: QuizAttemptRequest,
        *,
        user_id: int,
        provider_factory: ProviderFactory | None = None,
    ) -> QuizAttemptResponse:
        quiz = QuizService.get_course_quiz(db, course_id, quiz_id)
        questions = {question.id: question for question in quiz.questions}

        if not questions:
            raise BadRequestException("This quiz has no questions to answer.")

        submitted = cls._validate_submissions(request, questions)

        ordered = sorted(quiz.questions, key=lambda row: (row.question_index, row.id))
        charge_receipts: list[ChargeReceipt] = []
        graded = QuizGradingService.grade(
            db,
            questions=ordered,
            submissions=submitted,
            provider_factory=provider_factory,
            user_id=user_id,
            course_id=course_id,
            charge_receipts=charge_receipts,
        )

        scored = [answer for answer in graded if answer.score is not None]
        correct_count = sum(1 for answer in graded if answer.is_correct)
        score = sum(answer.score for answer in scored) / len(scored) if scored else 0.0

        attempt = QuizAttempt(
            user_id=user_id,
            quiz_id=quiz.id,
            score=min(max(score, 0.0), 1.0),
            time_spent_seconds=request.time_spent_seconds,
        )

        by_question = {question.id: question for question in ordered}

        try:
            db.add(attempt)
            db.flush()

            for answer in graded:
                submission = submitted.get(answer.question_id)
                question = by_question[answer.question_id]
                db.add(
                    QuizAttemptAnswer(
                        attempt_id=attempt.id,
                        quiz_question_id=answer.question_id,
                        selected_option_index=answer.selected_option_index,
                        text_response=answer.text_response,
                        is_correct=answer.is_correct,
                        score=answer.score,
                        feedback=answer.feedback,
                        time_spent_seconds=(
                            submission.time_spent_seconds if submission else None
                        ),
                        # Snapshot the topic so mastery survives the question
                        # being retagged or its quiz regenerated later.
                        topic=question.topic,
                    )
                )

            db.flush()
            cls._update_course_progress_transactional(
                db, course_id=course_id, user_id=user_id
            )
            db.commit()
        except Exception:
            db.rollback()
            for receipt in charge_receipts:
                CreditService.refund(db, receipt)
            raise

        db.refresh(attempt)

        return QuizAttemptResponse(
            attempt_id=attempt.id,
            quiz_id=quiz.id,
            score=attempt.score,
            correct_count=correct_count,
            graded_count=len(scored),
            total_questions=len(ordered),
            time_spent_seconds=attempt.time_spent_seconds,
            created_at=attempt.created_at,
            answers=[
                QuizAnswerResult(
                    question_id=answer.question_id,
                    question_type=answer.question_type,
                    selected_option_index=answer.selected_option_index,
                    text_response=answer.text_response,
                    correct_option_index=answer.correct_option_index,
                    correct_answer=answer.correct_answer,
                    is_correct=answer.is_correct,
                    score=answer.score,
                    feedback=answer.feedback,
                    time_spent_seconds=(
                        submitted[answer.question_id].time_spent_seconds
                        if answer.question_id in submitted
                        else None
                    ),
                    topic=by_question[answer.question_id].topic,
                )
                for answer in graded
            ],
        )

    @staticmethod
    def _aggregate(db: Session, course_id: int, user_id: int) -> _ProgressAggregate:
        attempts = db.scalars(
            select(QuizAttempt)
            .join(Quiz, Quiz.id == QuizAttempt.quiz_id)
            .where(Quiz.course_id == course_id, QuizAttempt.user_id == user_id)
            .options(selectinload(QuizAttempt.answers))
            .order_by(QuizAttempt.created_at.desc(), QuizAttempt.id.desc())
        ).all()

        answered = db.execute(
            select(
                QuizQuestion.topic,
                QuizAttemptAnswer.is_correct,
                QuizAttemptAnswer.topic,
            )
            .join(
                QuizAttemptAnswer,
                QuizAttemptAnswer.quiz_question_id == QuizQuestion.id,
            )
            .join(QuizAttempt, QuizAttempt.id == QuizAttemptAnswer.attempt_id)
            .join(Quiz, Quiz.id == QuizQuestion.quiz_id)
            .where(
                Quiz.course_id == course_id,
                QuizAttempt.user_id == user_id,
                # An answer the grader could not score is not evidence either
                # way, so it must not move mastery in either direction.
                QuizAttemptAnswer.is_correct.is_not(None),
            )
        ).all()

        totals: dict[str, list[int]] = {}
        correct_count = 0
        incorrect_count = 0

        for question_topic, is_correct, answer_topic in answered:
            if is_correct:
                correct_count += 1
            else:
                incorrect_count += 1

            label = (answer_topic or question_topic or "").strip() or UNTAGGED_TOPIC
            bucket = totals.setdefault(label, [0, 0])
            bucket[0] += 1
            if is_correct:
                bucket[1] += 1

        topic_mastery: list[TopicMastery] = []
        weak_topics: list[str] = []
        for label in sorted(totals):
            total, correct = totals[label]
            percentage = round(correct / total * 100) if total else 0
            status = _mastery_status(percentage)
            topic_mastery.append(
                TopicMastery(
                    topic=label,
                    questions_answered=total,
                    questions_correct=correct,
                    mastery_percentage=percentage,
                    status=status,
                )
            )
            if status is MasteryStatus.NEEDS_REVIEW:
                weak_topics.append(label)

        attempts_count = len(attempts)
        average_score = (
            sum(attempt.score for attempt in attempts) / attempts_count
            if attempts_count
            else None
        )
        completion = (
            min(1.0, max(0.0, average_score)) if average_score is not None else 0.0
        )

        return _ProgressAggregate(
            attempts_count=attempts_count,
            average_score=average_score,
            completion=completion,
            correct_count=correct_count,
            incorrect_count=incorrect_count,
            topic_mastery=topic_mastery,
            weak_topics=weak_topics,
            quiz_history=[
                QuizHistoryItem(
                    attempt_id=attempt.id,
                    quiz_id=attempt.quiz_id,
                    score=attempt.score,
                    correct_count=sum(
                        1 for answer in attempt.answers if answer.is_correct is True
                    ),
                    total_questions=len(attempt.answers),
                    time_spent_seconds=attempt.time_spent_seconds,
                    created_at=attempt.created_at,
                )
                for attempt in attempts
            ],
        )

    @classmethod
    def _update_course_progress_transactional(
        cls, db: Session, *, course_id: int, user_id: int
    ) -> Progress:
        """Materialize this user's course progress inside the caller's transaction.

        Recorded from the attempt history rather than incremented, so a replay or
        a rolled-back attempt can never leave the counters overstating reality.
        """
        summary = cls._aggregate(db, course_id, user_id)

        progress = db.scalars(
            select(Progress).where(
                Progress.user_id == user_id,
                Progress.course_id == course_id,
            )
        ).one_or_none()

        if progress is None:
            progress = Progress(user_id=user_id, course_id=course_id)
            db.add(progress)

        progress.completion = summary.completion
        progress.quizzes_completed = summary.attempts_count
        progress.correct_answers_count = summary.correct_count
        progress.incorrect_answers_count = summary.incorrect_count
        progress.total_questions_answered = summary.total_questions_answered
        progress.weak_topics = summary.weak_topics
        progress.quiz_history = [
            item.model_dump(mode="json") for item in summary.quiz_history
        ]

        db.flush()
        return progress

    @classmethod
    def get_course_progress(
        cls,
        db: Session,
        course_id: int,
        *,
        user_id: int,
    ) -> CourseProgressResponse:
        summary = cls._aggregate(db, course_id, user_id)

        return CourseProgressResponse(
            quizzes_completed=summary.attempts_count,
            attempts_count=summary.attempts_count,
            average_score=summary.average_score,
            correct_count=summary.correct_count,
            incorrect_count=summary.incorrect_count,
            total_questions_answered=summary.total_questions_answered,
            completion=summary.completion,
            weak_topics=summary.weak_topics,
            topic_mastery=summary.topic_mastery,
            quiz_history=summary.quiz_history,
        )
