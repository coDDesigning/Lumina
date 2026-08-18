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
from schemas.quiz import QuizGenerationResponse
from services.ai_usage_logger import AiUsageLogger
from services.course_material import CourseMaterial, load_course_material
from services.prompt_loader import PromptLoader
from services.text_generation import (
    TextGenerationError,
    TextGenerationProvider,
    model_identifier,
)
from utils.ai_errors import (
    NO_READY_MATERIAL_MESSAGE,
    CourseMaterialUnavailableError,
    InvalidGeneratedStructureError,
)


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
    ) -> str:
        return PromptLoader.render(cls.PROMPT_TEMPLATE_NAME, {"TEXT": course_material})

    @classmethod
    def generate(
        cls,
        db: Session,
        course_id: int,
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

        prompt = cls.build_prompt(material.text)
        metadata = None

        try:
            if hasattr(provider, "generate_json_with_metadata"):
                result, metadata = provider.generate_json_with_metadata(prompt)
            else:
                result = provider.generate_json(prompt)
        except TextGenerationError as exc:
            if resolved_user_id:
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

        try:
            validated = QuizGenerationResponse.model_validate(result)
        except ValidationError as exc:
            if resolved_user_id:
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
                    question_text=question.question,
                    options=question.options,
                    correct_option_index=question.correct_option_index,
                )
            )

        db.commit()
        db.refresh(quiz)

        return quiz
