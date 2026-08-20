from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from backend.app.models import (
    Progress,
    Quiz,
    QuizAttempt,
    QuizAttemptAnswer,
    QuizQuestion,
)
from schemas.quiz import QuizQuestionType
from schemas.quiz_attempt import (
    MASTERED_THRESHOLD,
    NEEDS_REVIEW_THRESHOLD,
    CourseProgressResponse,
    MasteryStatus,
    QuizAnswerResult,
    QuizAttemptRequest,
    QuizAttemptResponse,
    QuizHistoryItem,
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


def _is_objective_question(question: QuizQuestion) -> bool:
    """Return True if question is auto-gradable (multiple choice or true/false)."""
    q_type = question.question_type or QuizQuestionType.MULTIPLE_CHOICE.value
    if q_type in (
        QuizQuestionType.MULTIPLE_CHOICE.value,
        QuizQuestionType.TRUE_FALSE.value,
        QuizQuestionType.MULTIPLE_CHOICE,
        QuizQuestionType.TRUE_FALSE,
    ):
        return True
    return question.correct_option_index is not None and bool(question.options)


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

        submitted: dict[int, dict] = {}
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
            if (
                selected is not None
                and question.options is not None
                and selected >= len(question.options)
            ):
                raise BadRequestException(
                    "One of the submitted answers selects an option that does not exist."
                )
            submitted[answer.question_id] = {
                "selected_option_index": selected,
                "text_response": answer.text_response,
                "time_spent_seconds": answer.time_spent_seconds,
            }

        attempt = QuizAttempt(
            user_id=user_id,
            quiz_id=quiz.id,
            score=0.0,
            time_spent_seconds=request.time_spent_seconds,
        )
        db.add(attempt)
        db.flush()

        results: list[QuizAnswerResult] = []
        gradable_correct_count = 0
        gradable_total_count = 0

        for question in sorted(quiz.questions, key=lambda row: row.question_index):
            sub = submitted.get(question.id)
            is_gradable = _is_objective_question(question)
            selected = sub["selected_option_index"] if sub else None
            text_response = sub["text_response"] if sub else None
            time_spent = sub["time_spent_seconds"] if sub else None
            topic = question.topic

            if is_gradable:
                gradable_total_count += 1
                is_correct = (
                    selected is not None
                    and question.correct_option_index is not None
                    and selected == question.correct_option_index
                )
                if is_correct:
                    gradable_correct_count += 1
                correct_option_idx = question.correct_option_index
            else:
                # Written / open-ended questions are stored with explicit ungraded state
                is_correct = None
                correct_option_idx = question.correct_option_index

            db.add(
                QuizAttemptAnswer(
                    attempt_id=attempt.id,
                    quiz_question_id=question.id,
                    selected_option_index=selected,
                    text_response=text_response,
                    is_correct=is_correct,
                    time_spent_seconds=time_spent,
                    topic=topic,
                )
            )
            results.append(
                QuizAnswerResult(
                    question_id=question.id,
                    selected_option_index=selected,
                    text_response=text_response,
                    correct_option_index=correct_option_idx,
                    is_correct=is_correct,
                    time_spent_seconds=time_spent,
                    topic=topic,
                )
            )

        # Compute attempt score from gradable questions only.
        # If there are no gradable questions, default score is 0.0.
        if gradable_total_count > 0:
            attempt.score = gradable_correct_count / gradable_total_count
        else:
            attempt.score = 0.0

        db.flush()

        # Update course progress transactionally
        cls._update_course_progress_transactional(
            db,
            course_id=course_id,
            user_id=user_id,
        )

        db.refresh(attempt)
        db.commit()

        return QuizAttemptResponse(
            attempt_id=attempt.id,
            quiz_id=quiz.id,
            score=attempt.score,
            correct_count=gradable_correct_count,
            total_questions=len(quiz.questions),
            time_spent_seconds=attempt.time_spent_seconds,
            created_at=attempt.created_at,
            answers=results,
        )

    @classmethod
    def _update_course_progress_transactional(
        cls,
        db: Session,
        *,
        course_id: int,
        user_id: int,
    ) -> Progress:
        attempts = db.scalars(
            select(QuizAttempt)
            .join(Quiz, Quiz.id == QuizAttempt.quiz_id)
            .where(Quiz.course_id == course_id, QuizAttempt.user_id == user_id)
            .options(selectinload(QuizAttempt.answers))
            .order_by(QuizAttempt.created_at.desc())
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
            .where(Quiz.course_id == course_id, QuizAttempt.user_id == user_id)
        ).all()

        totals: dict[str, list[int]] = {}
        correct_count = 0
        incorrect_count = 0

        for q_topic, is_correct, ans_topic in answered:
            if is_correct is None:
                # Ungraded written responses do not skew objective mastery
                continue
            if is_correct:
                correct_count += 1
            else:
                incorrect_count += 1

            topic_label = (ans_topic or q_topic or "").strip() or UNTAGGED_TOPIC
            bucket = totals.setdefault(topic_label, [0, 0])
            bucket[0] += 1
            if is_correct:
                bucket[1] += 1

        topic_mastery: list[TopicMastery] = []
        weak_topics: list[str] = []
        for label in sorted(totals):
            total, correct = totals[label]
            percentage = round(correct / total * 100) if total > 0 else 0
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
            if status == MasteryStatus.NEEDS_REVIEW:
                weak_topics.append(label)

        quizzes_completed = len(attempts)
        average_score = (
            sum(att.score for att in attempts) / quizzes_completed
            if quizzes_completed > 0
            else None
        )
        completion = (
            min(1.0, max(0.0, average_score)) if average_score is not None else 0.0
        )

        quiz_history_payload = [
            {
                "attempt_id": att.id,
                "quiz_id": att.quiz_id,
                "score": att.score,
                "correct_count": sum(1 for a in att.answers if a.is_correct is True),
                "total_questions": len(att.answers),
                "time_spent_seconds": att.time_spent_seconds,
                "created_at": att.created_at.isoformat()
                if hasattr(att.created_at, "isoformat")
                else str(att.created_at),
            }
            for att in attempts
        ]

        progress = db.scalars(
            select(Progress).where(
                Progress.user_id == user_id,
                Progress.course_id == course_id,
            )
        ).one_or_none()

        if progress is None:
            progress = Progress(
                user_id=user_id,
                course_id=course_id,
                completion=completion,
                quizzes_completed=quizzes_completed,
                correct_answers_count=correct_count,
                incorrect_answers_count=incorrect_count,
                total_questions_answered=correct_count + incorrect_count,
                weak_topics=weak_topics,
                quiz_history=quiz_history_payload,
            )
            db.add(progress)
        else:
            progress.completion = completion
            progress.quizzes_completed = quizzes_completed
            progress.correct_answers_count = correct_count
            progress.incorrect_answers_count = incorrect_count
            progress.total_questions_answered = correct_count + incorrect_count
            progress.weak_topics = weak_topics
            progress.quiz_history = quiz_history_payload

        db.flush()
        return progress

    @staticmethod
    def get_course_progress(
        db: Session,
        course_id: int,
        *,
        user_id: int,
    ) -> CourseProgressResponse:
        attempts = db.scalars(
            select(QuizAttempt)
            .join(Quiz, Quiz.id == QuizAttempt.quiz_id)
            .where(Quiz.course_id == course_id, QuizAttempt.user_id == user_id)
            .options(selectinload(QuizAttempt.answers))
            .order_by(QuizAttempt.created_at.desc())
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
            .where(Quiz.course_id == course_id, QuizAttempt.user_id == user_id)
        ).all()

        totals: dict[str, list[int]] = {}
        correct_count = 0
        incorrect_count = 0

        for q_topic, is_correct, ans_topic in answered:
            if is_correct is None:
                continue
            if is_correct:
                correct_count += 1
            else:
                incorrect_count += 1

            topic_label = (ans_topic or q_topic or "").strip() or UNTAGGED_TOPIC
            bucket = totals.setdefault(topic_label, [0, 0])
            bucket[0] += 1
            if is_correct:
                bucket[1] += 1

        topic_mastery: list[TopicMastery] = []
        weak_topics: list[str] = []
        for label in sorted(totals):
            total, correct = totals[label]
            percentage = round(correct / total * 100) if total > 0 else 0
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
            if status == MasteryStatus.NEEDS_REVIEW:
                weak_topics.append(label)

        attempts_count = len(attempts)
        average_score = (
            sum(att.score for att in attempts) / attempts_count
            if attempts_count > 0
            else None
        )
        completion = (
            min(1.0, max(0.0, average_score)) if average_score is not None else 0.0
        )

        quiz_history = [
            QuizHistoryItem(
                attempt_id=att.id,
                quiz_id=att.quiz_id,
                score=att.score,
                correct_count=sum(1 for a in att.answers if a.is_correct is True),
                total_questions=len(att.answers),
                time_spent_seconds=att.time_spent_seconds,
                created_at=att.created_at,
            )
            for att in attempts
        ]

        return CourseProgressResponse(
            quizzes_completed=attempts_count,
            attempts_count=attempts_count,
            average_score=average_score,
            correct_count=correct_count,
            incorrect_count=incorrect_count,
            total_questions_answered=correct_count + incorrect_count,
            completion=completion,
            weak_topics=weak_topics,
            topic_mastery=topic_mastery,
            quiz_history=quiz_history,
        )
