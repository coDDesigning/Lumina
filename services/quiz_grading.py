"""Scoring one attempt's answers, by question type.

Three of the four question types grade deterministically. Open-ended answers do
not, so they are scored by the text-generation provider against the reference
answer stored with the question.

Grading runs to completion before the attempt is persisted, and a provider
failure is never fatal: the affected answers are recorded ungraded and excluded
from the attempt score. Losing a student's written work because a grading model
timed out would be a much worse outcome than an unscored answer.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass

from pydantic import ValidationError
from sqlalchemy.orm import Session

from backend.app.models import QuizQuestion
from schemas.ai_usage import ErrorCategory, GenerationType
from schemas.quiz import (
    OpenEndedAnswer,
    QuizCorrectAnswer,
    QuizQuestionType,
    ShortAnswerAnswer,
    normalize_answer_text,
)
from schemas.quiz_attempt import (
    OPEN_ENDED_PASS_THRESHOLD,
    OpenEndedGradingResponse,
    QuizAnswerSubmission,
)
from services.ai_usage_logger import AiUsageLogger
from services.prompt_loader import PromptLoader
from services.quiz import parse_correct_answer
from services.text_generation import TextGenerationProvider
from services.credits import CreditService

logger = logging.getLogger(__name__)

GRADING_CREDIT_COST = 1.0

ProviderFactory = Callable[[], TextGenerationProvider]


@dataclass(frozen=True)
class GradedAnswer:
    question_id: int
    question_type: QuizQuestionType
    selected_option_index: int | None
    text_response: str | None
    correct_option_index: int | None
    correct_answer: QuizCorrectAnswer | None
    is_correct: bool | None
    score: float | None
    feedback: str | None


Verdict = tuple[bool | None, float | None]

# A question with no stored answer key cannot be judged either way. That is a
# gap in the question, not a wrong answer, so it is recorded ungraded rather
# than scored zero -- otherwise a student loses marks for a generation-time
# omission they could not have answered correctly.
UNGRADABLE: Verdict = (None, None)


def _option_verdict(
    question: QuizQuestion, submission: QuizAnswerSubmission | None
) -> Verdict:
    if question.correct_option_index is None:
        return UNGRADABLE

    selected = submission.selected_option_index if submission else None
    is_correct = selected is not None and selected == question.correct_option_index
    return is_correct, 1.0 if is_correct else 0.0


def _short_answer_verdict(
    answer: QuizCorrectAnswer | None, submission: QuizAnswerSubmission | None
) -> Verdict:
    if not isinstance(answer, ShortAnswerAnswer):
        return UNGRADABLE

    normalized = normalize_answer_text(
        (submission.text_response if submission else None) or ""
    )
    if not normalized:
        return False, 0.0

    accepted = answer.accepted_answers or [answer.text]
    is_correct = any(
        normalize_answer_text(candidate) == normalized for candidate in accepted
    )
    return is_correct, 1.0 if is_correct else 0.0


class QuizGradingService:
    PROMPT_TEMPLATE_NAME = "quiz_grading"

    @classmethod
    def grade(
        cls,
        db: Session,
        *,
        questions: list[QuizQuestion],
        submissions: dict[int, QuizAnswerSubmission],
        provider_factory: ProviderFactory | None = None,
        user_id: int | None = None,
        course_id: int | None = None,
    ) -> list[GradedAnswer]:
        """Score every question of a quiz, in the order the questions are given.

        ``provider_factory`` is called only when there is an open-ended answer to
        score, so an attempt that needs no grader never builds one.
        """
        graded: list[GradedAnswer] = []
        pending_open_ended: list[tuple[int, QuizQuestion, str, OpenEndedAnswer]] = []

        for question in questions:
            submission = submissions.get(question.id)
            question_type = QuizQuestionType(question.question_type)
            answer = parse_correct_answer(question)
            written = (submission.text_response if submission else None) or ""

            if question_type is QuizQuestionType.OPEN_ENDED:
                if not isinstance(answer, OpenEndedAnswer):
                    is_correct, score = UNGRADABLE
                elif written.strip():
                    pending_open_ended.append(
                        (len(graded), question, written.strip(), answer)
                    )
                    is_correct, score = None, None
                else:
                    is_correct, score = False, 0.0
            elif question_type is QuizQuestionType.SHORT_ANSWER:
                is_correct, score = _short_answer_verdict(answer, submission)
            else:
                is_correct, score = _option_verdict(question, submission)

            graded.append(
                GradedAnswer(
                    question_id=question.id,
                    question_type=question_type,
                    selected_option_index=(
                        submission.selected_option_index if submission else None
                    ),
                    text_response=written.strip() or None,
                    correct_option_index=question.correct_option_index,
                    correct_answer=answer,
                    is_correct=is_correct,
                    score=score,
                    feedback=None,
                )
            )

        if pending_open_ended:
            graded = cls._apply_open_ended_verdicts(
                db,
                graded=graded,
                pending=pending_open_ended,
                provider_factory=provider_factory,
                user_id=user_id,
                course_id=course_id,
            )

        return graded

    @classmethod
    def build_prompt(
        cls, pending: list[tuple[int, QuizQuestion, str, OpenEndedAnswer]]
    ) -> str:
        blocks = []
        for number, (_, question, written, answer) in enumerate(pending, start=1):
            blocks.append(
                f"question_number: {number}\n"
                f"Question:\n{question.question_text}\n\n"
                f"Reference answer:\n{answer.reference_answer}\n\n"
                f"Student answer:\n{written}"
            )
        return PromptLoader.render(
            cls.PROMPT_TEMPLATE_NAME,
            {
                "SUBMISSION_COUNT": str(len(pending)),
                "SUBMISSIONS": "\n\n---\n\n".join(blocks),
            },
        )

    @classmethod
    def _apply_open_ended_verdicts(
        cls,
        db: Session,
        *,
        graded: list[GradedAnswer],
        pending: list[tuple[int, QuizQuestion, str, OpenEndedAnswer]],
        provider_factory: ProviderFactory | None,
        user_id: int | None,
        course_id: int | None,
    ) -> list[GradedAnswer]:
        def log_failure(category: ErrorCategory, **extra) -> None:
            if user_id:
                AiUsageLogger.log_failure(
                    db,
                    user_id=user_id,
                    course_id=course_id,
                    generation_type=GenerationType.QUIZ_GRADING,
                    error_category=category,
                    **extra,
                )

        if provider_factory is None:
            log_failure(ErrorCategory.PROVIDER_ERROR)
            return graded

        # Built before any credit is charged, so a configuration failure needs no
        # refund and still leaves the answers ungraded rather than lost.
        try:
            provider = provider_factory()
        except Exception:
            logger.warning("quiz grading provider could not be built", exc_info=True)
            log_failure(ErrorCategory.PROVIDER_ERROR)
            return graded

        receipt = None
        if user_id:
            receipt = CreditService.charge(
                db, user_id, GRADING_CREDIT_COST, source_type="quiz_grading"
            )
            if receipt is None:
                log_failure(ErrorCategory.INSUFFICIENT_CREDITS)
                return graded

        prompt = cls.build_prompt(pending)
        metadata = None

        try:
            if hasattr(provider, "generate_json_with_metadata"):
                result, metadata = provider.generate_json_with_metadata(prompt)
            else:
                result = provider.generate_json(prompt)
        except Exception as exc:
            if user_id:
                CreditService.refund(db, receipt)
            log_failure(getattr(exc, "error_category", ErrorCategory.PROVIDER_ERROR))
            return graded

        try:
            verdicts = OpenEndedGradingResponse.model_validate(result)
        except ValidationError:
            if user_id:
                CreditService.refund(db, receipt)
            log_failure(
                ErrorCategory.INVALID_STRUCTURE,
                latency_ms=metadata.latency_ms if metadata else None,
            )
            return graded

        by_number = {verdict.question_number: verdict for verdict in verdicts.verdicts}

        for number, (position, _, _, _) in enumerate(pending, start=1):
            verdict = by_number.get(number)
            if verdict is None:
                continue
            answer = graded[position]
            graded[position] = GradedAnswer(
                question_id=answer.question_id,
                question_type=answer.question_type,
                selected_option_index=answer.selected_option_index,
                text_response=answer.text_response,
                correct_option_index=answer.correct_option_index,
                correct_answer=answer.correct_answer,
                is_correct=verdict.score >= OPEN_ENDED_PASS_THRESHOLD,
                score=verdict.score,
                feedback=verdict.feedback or None,
            )

        if user_id:
            AiUsageLogger.log_success(
                db,
                user_id=user_id,
                course_id=course_id,
                generation_type=GenerationType.QUIZ_GRADING,
                metadata=metadata,
            )

        return graded
