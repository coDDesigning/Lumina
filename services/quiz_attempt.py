from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from backend.app.models import Quiz, QuizAttempt, QuizAttemptAnswer, QuizQuestion
from schemas.quiz_attempt import (
    MASTERED_THRESHOLD,
    NEEDS_REVIEW_THRESHOLD,
    CourseProgressResponse,
    MasteryStatus,
    QuizAnswerResult,
    QuizAttemptRequest,
    QuizAttemptResponse,
    TopicMastery,
)
from utils.exceptions import BadRequestException, NotFoundException

UNTAGGED_TOPIC = "Untagged"


def _mastery_status(percentage: int) -> MasteryStatus:
    if percentage >= MASTERED_THRESHOLD:
        return MasteryStatus.MASTERED
    if percentage < NEEDS_REVIEW_THRESHOLD:
        return MasteryStatus.NEEDS_REVIEW
    return MasteryStatus.IN_PROGRESS


class QuizAttemptService:
    @staticmethod
    def _load_course_quiz(db: Session, course_id: int, quiz_id: int) -> Quiz:
        quiz = db.scalars(
            select(Quiz)
            .where(Quiz.id == quiz_id, Quiz.course_id == course_id)
            .options(selectinload(Quiz.questions))
        ).one_or_none()

        if quiz is None:
            raise NotFoundException("Quiz not found")

        return quiz

    @classmethod
    def record_attempt(
        cls,
        db: Session,
        course_id: int,
        quiz_id: int,
        request: QuizAttemptRequest,
        *,
        user_id: int,
    ) -> QuizAttemptResponse:
        quiz = cls._load_course_quiz(db, course_id, quiz_id)
        questions = {question.id: question for question in quiz.questions}

        if not questions:
            raise BadRequestException("This quiz has no questions to answer.")

        submitted: dict[int, int | None] = {}
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
            selected = answer.selected_option_index
            if selected is not None and selected >= len(question.options):
                raise BadRequestException(
                    "One of the submitted answers selects an option that does not exist."
                )
            submitted[answer.question_id] = selected

        attempt = QuizAttempt(
            user_id=user_id,
            quiz_id=quiz.id,
            score=0.0,
            time_spent_seconds=request.time_spent_seconds,
        )
        db.add(attempt)
        db.flush()

        results: list[QuizAnswerResult] = []
        correct_count = 0

        for question in sorted(quiz.questions, key=lambda row: row.question_index):
            selected = submitted.get(question.id)
            is_correct = selected == question.correct_option_index
            if is_correct:
                correct_count += 1

            db.add(
                QuizAttemptAnswer(
                    attempt_id=attempt.id,
                    quiz_question_id=question.id,
                    selected_option_index=selected,
                    is_correct=is_correct,
                )
            )
            results.append(
                QuizAnswerResult(
                    question_id=question.id,
                    selected_option_index=selected,
                    correct_option_index=question.correct_option_index,
                    is_correct=is_correct,
                )
            )

        total_questions = len(quiz.questions)
        attempt.score = correct_count / total_questions
        db.flush()
        db.refresh(attempt)
        db.commit()

        return QuizAttemptResponse(
            attempt_id=attempt.id,
            quiz_id=quiz.id,
            score=attempt.score,
            correct_count=correct_count,
            total_questions=total_questions,
            time_spent_seconds=attempt.time_spent_seconds,
            created_at=attempt.created_at,
            answers=results,
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
            .where(Quiz.course_id == course_id, QuizAttempt.user_id == user_id)
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
