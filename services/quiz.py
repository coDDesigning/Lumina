from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError
from sqlalchemy.orm import Session

from backend.app.config import settings
from backend.app.models import (
    Course,
    Quiz,
    QuizQuestion,
)
from schemas.ai_usage import ErrorCategory, GenerationType
from schemas.quiz import (
    TRUE_FALSE_OPTION_COUNT,
    QuizDifficulty,
    QuizGenerationResponse,
    QuizQuestionType,
    QuizQuestionView,
    QuizRequest,
    QuizView,
)
from services.ai_usage_logger import AiUsageLogger
from services.course_material import CourseMaterial, load_course_material
from services.prompt_loader import PromptLoader
from services.text_generation import (
    TextGenerationError,
    TextGenerationProvider,
    model_identifier,
)
from services.user import UserService
from utils.ai_errors import (
    NO_READY_MATERIAL_MESSAGE,
    CourseMaterialUnavailableError,
    InsufficientCreditsError,
    InvalidGeneratedStructureError,
)


QUESTION_TYPE_DIRECTIVES: dict[QuizQuestionType, str] = {
    QuizQuestionType.MULTIPLE_CHOICE: (
        "Every question must be multiple choice with exactly 4 answer choices, and "
        "correct_option_index must be an integer from 0 to 3."
    ),
    QuizQuestionType.TRUE_FALSE: (
        "Every question must be a true/false question whose options are exactly "
        '["True", "False"] in that order, and correct_option_index must be 0 for True '
        "or 1 for False."
    ),
}

DIFFICULTY_DIRECTIVES: dict[QuizDifficulty, str] = {
    QuizDifficulty.EASY: (
        "Every question must be easy: direct recall or straightforward comprehension "
        "of the lecture material."
    ),
    QuizDifficulty.MEDIUM: (
        "Every question must be of medium difficulty: applying or comparing ideas from "
        "the lecture material."
    ),
    QuizDifficulty.HARD: (
        "Every question must be hard: analysis, synthesis, or reasoning that combines "
        "several parts of the lecture material."
    ),
}

EXPECTED_OPTION_COUNTS: dict[QuizQuestionType, int] = {
    QuizQuestionType.MULTIPLE_CHOICE: 4,
    QuizQuestionType.TRUE_FALSE: TRUE_FALSE_OPTION_COUNT,
}


class QuizGenerationError(RuntimeError):
    """Quiz generation failed."""


class NoReadyCourseMaterialError(QuizGenerationError, CourseMaterialUnavailableError):
    """No processed course material is available for quiz generation."""


class InvalidQuizStructureError(QuizGenerationError, InvalidGeneratedStructureError):
    """The provider returned something that is not a valid quiz."""


@dataclass(frozen=True)
class QuizGeneration:
    quiz: QuizGenerationResponse
    material: CourseMaterial
    model_used: str


class QuizService:
    PROMPT_TEMPLATE_NAME = "quiz"
    PROMPT_PATH = Path(__file__).resolve().parents[1] / "app" / "prompts" / "quiz.json"

    @staticmethod
    def get_course_material(
        db: Session,
        course_id: int,
    ) -> CourseMaterial:
        return load_course_material(
            db,
            course_id,
            max_characters=settings.quiz_material_max_chars,
        )

    @classmethod
    def build_prompt(
        cls,
        course_material: str,
        request: QuizRequest,
    ) -> str:
        return PromptLoader.render(
            cls.PROMPT_TEMPLATE_NAME,
            {
                "TEXT": course_material,
                "QUESTION_COUNT": str(request.question_count),
                "QUESTION_TYPE_DIRECTIVE": QUESTION_TYPE_DIRECTIVES[
                    request.question_type
                ],
                "DIFFICULTY_DIRECTIVE": DIFFICULTY_DIRECTIVES[request.difficulty],
                "TOPIC_FOCUS": request.topic_focus,
            },
        )

    @classmethod
    def generate(
        cls,
        db: Session,
        course_id: int,
        request: QuizRequest,
        provider: TextGenerationProvider,
        user_id: int | None = None,
    ) -> QuizGeneration:
        resolved_user_id = user_id
        if resolved_user_id is None:
            course = db.get(Course, course_id)
            if course is not None:
                resolved_user_id = course.owner_id

        material = cls.get_course_material(
            db,
            course_id,
        )

        if material.is_empty:
            if resolved_user_id:
                AiUsageLogger.log_failure(
                    db,
                    user_id=resolved_user_id,
                    course_id=course_id,
                    generation_type=GenerationType.QUIZ,
                    error_category=ErrorCategory.NO_READY_MATERIAL,
                )
            raise NoReadyCourseMaterialError(NO_READY_MATERIAL_MESSAGE)

        prompt = cls.build_prompt(material.text, request)
        metadata = None

        if resolved_user_id:
            charged = UserService.charge_credits(db, resolved_user_id, 1.0)
            if not charged:
                AiUsageLogger.log_failure(
                    db,
                    user_id=resolved_user_id,
                    course_id=course_id,
                    generation_type=GenerationType.QUIZ,
                    error_category=ErrorCategory.INSUFFICIENT_CREDITS,
                )
                raise InsufficientCreditsError("Insufficient credits.")

        try:
            if hasattr(provider, "generate_json_with_metadata"):
                result, metadata = provider.generate_json_with_metadata(prompt)
            else:
                result = provider.generate_json(prompt)
        except TextGenerationError as exc:
            if resolved_user_id:
                UserService.refund_credits(db, resolved_user_id, 1.0)
                AiUsageLogger.log_failure(
                    db,
                    user_id=resolved_user_id,
                    course_id=course_id,
                    generation_type=GenerationType.QUIZ,
                    error_category=getattr(
                        exc, "error_category", ErrorCategory.PROVIDER_ERROR
                    ),
                )
            raise QuizGenerationError("Text generation provider failed.") from exc
        except Exception:
            if resolved_user_id:
                UserService.refund_credits(db, resolved_user_id, 1.0)
            raise

        try:
            validated = QuizGenerationResponse.model_validate(result)
            cls._assert_matches_request(validated, request)
        except (ValidationError, ValueError) as exc:
            if resolved_user_id:
                UserService.refund_credits(db, resolved_user_id, 1.0)
                AiUsageLogger.log_failure(
                    db,
                    user_id=resolved_user_id,
                    course_id=course_id,
                    generation_type=GenerationType.QUIZ,
                    error_category=ErrorCategory.INVALID_STRUCTURE,
                    latency_ms=metadata.latency_ms if metadata else None,
                )
            raise InvalidQuizStructureError(
                "Generated quiz has an invalid structure."
            ) from exc

        if resolved_user_id:
            AiUsageLogger.log_success(
                db,
                user_id=resolved_user_id,
                course_id=course_id,
                generation_type=GenerationType.QUIZ,
                metadata=metadata,
            )

        return QuizGeneration(
            quiz=validated,
            material=material,
            model_used=model_identifier(metadata),
        )

    @staticmethod
    def _assert_matches_request(
        quiz: QuizGenerationResponse,
        request: QuizRequest,
    ) -> None:
        if len(quiz.questions) != request.question_count:
            raise ValueError(
                "Generated quiz does not contain the requested number of questions."
            )

        expected_options = EXPECTED_OPTION_COUNTS[request.question_type]
        for question in quiz.questions:
            if len(question.options) != expected_options:
                raise ValueError(
                    "Generated quiz does not use the requested question type."
                )

    @classmethod
    def build_quiz_view(cls, quiz: Quiz) -> QuizView:
        questions = sorted(quiz.questions, key=lambda row: row.question_index)
        return QuizView(
            quiz_id=quiz.id,
            title=quiz.title,
            questions=[
                QuizQuestionView(
                    question_id=row.id,
                    question_number=row.question_index + 1,
                    question_type=getattr(
                        row, "question_type", QuizQuestionType.MULTIPLE_CHOICE
                    )
                    or QuizQuestionType.MULTIPLE_CHOICE,
                    topic=row.topic or "",
                    question=row.question_text,
                    options=list(row.options) if row.options else [],
                    correct_option_index=row.correct_option_index,
                    explanation=row.explanation or "",
                )
                for row in questions
            ],
        )

    @staticmethod
    def save_generated_quiz(
        db: Session,
        course_id: int,
        quiz_data: QuizGenerationResponse,
    ) -> Quiz:
        quiz = Quiz(
            course_id=course_id,
            title=quiz_data.title,
        )

        db.add(quiz)
        db.flush()

        for question_index, question in enumerate(quiz_data.questions):
            db.add(
                QuizQuestion(
                    quiz_id=quiz.id,
                    question_index=question_index,
                    question_type="multiple_choice",
                    question_text=question.question,
                    options=question.options,
                    correct_option_index=question.correct_option_index,
                    topic=question.topic,
                    explanation=question.explanation,
                )
            )

        db.commit()
        db.refresh(quiz)

        return quiz
