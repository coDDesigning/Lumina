from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models import Quiz, QuizAttempt, QuizAttemptAnswer, QuizQuestion
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
    TopicMastery,
)
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
            written = answer.answer_text

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
        graded = QuizGradingService.grade(
            db,
            questions=ordered,
            submissions=submitted,
            provider_factory=provider_factory,
            user_id=user_id,
            course_id=course_id,
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

        try:
            db.add(attempt)
            db.flush()

            for answer in graded:
                db.add(
                    QuizAttemptAnswer(
                        attempt_id=attempt.id,
                        quiz_question_id=answer.question_id,
                        selected_option_index=answer.selected_option_index,
                        answer_text=answer.answer_text,
                        is_correct=answer.is_correct,
                        score=answer.score,
                        feedback=answer.feedback,
                    )
                )

            db.flush()
            db.commit()
        except Exception:
            db.rollback()
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
                    answer_text=answer.answer_text,
                    correct_option_index=answer.correct_option_index,
                    correct_answer=answer.correct_answer,
                    is_correct=answer.is_correct,
                    score=answer.score,
                    feedback=answer.feedback,
                )
                for answer in graded
            ],
        )

    @staticmethod
    def get_course_progress(
        db: Session,
        course_id: int,
        *,
        user_id: int,
    ) -> CourseProgressResponse:
        attempt_scores = db.scalars(
            select(QuizAttempt.score)
            .join(Quiz, Quiz.id == QuizAttempt.quiz_id)
            .where(Quiz.course_id == course_id, QuizAttempt.user_id == user_id)
        ).all()

        answered = db.execute(
            select(QuizQuestion.topic, QuizAttemptAnswer.is_correct)
            .join(
                QuizAttemptAnswer, QuizAttemptAnswer.quiz_question_id == QuizQuestion.id
            )
            .join(QuizAttempt, QuizAttempt.id == QuizAttemptAnswer.attempt_id)
            .join(Quiz, Quiz.id == QuizQuestion.quiz_id)
            .where(
                Quiz.course_id == course_id,
                QuizAttempt.user_id == user_id,
                QuizAttemptAnswer.is_correct.is_not(None),
            )
        ).all()

        totals: dict[str, list[int]] = {}
        for topic, is_correct in answered:
            label = (topic or "").strip() or UNTAGGED_TOPIC
            bucket = totals.setdefault(label, [0, 0])
            bucket[0] += 1
            if is_correct:
                bucket[1] += 1

        topic_mastery = []
        for label in sorted(totals):
            total, correct = totals[label]
            percentage = round(correct / total * 100)
            topic_mastery.append(
                TopicMastery(
                    topic=label,
                    questions_answered=total,
                    questions_correct=correct,
                    mastery_percentage=percentage,
                    status=_mastery_status(percentage),
                )
            )

        average_score = (
            sum(attempt_scores) / len(attempt_scores) if attempt_scores else None
        )

        return CourseProgressResponse(
            attempts_count=len(attempt_scores),
            average_score=average_score,
            topic_mastery=topic_mastery,
        )
