from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session

from backend.app.config import settings
from backend.app.models import Course
from schemas.ai_usage import ErrorCategory, GenerationType
from schemas.course_qa import CourseQAResponse
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
)


class CourseQAError(RuntimeError):
    """Course Q&A answer generation failed."""


class NoReadyCourseMaterialError(CourseQAError, CourseMaterialUnavailableError):
    """No processed course material is available for Course Q&A."""


@dataclass(frozen=True)
class CourseQAGeneration:
    response: CourseQAResponse
    material: CourseMaterial
    model_used: str


class CourseQAService:
    PROMPT_TEMPLATE_NAME = "course_qa"
    PROMPT_PATH = (
        Path(__file__).resolve().parents[1] / "app" / "prompts" / "course_qa.json"
    )

    @staticmethod
    def get_course_material(
        db: Session,
        course_id: int,
    ) -> CourseMaterial:
        return load_course_material(
            db,
            course_id,
            max_characters=settings.course_qa_material_max_chars,
        )

    @classmethod
    def build_prompt(
        cls,
        course_material: str,
        question: str,
    ) -> str:
        return PromptLoader.render(
            cls.PROMPT_TEMPLATE_NAME,
            {
                "COURSE_MATERIAL": course_material,
                "QUESTION": question,
            },
        )

    @classmethod
    def generate(
        cls,
        db: Session,
        course_id: int,
        question: str,
        provider: TextGenerationProvider,
        user_id: int | None = None,
    ) -> CourseQAGeneration:
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
                    generation_type=GenerationType.COURSE_QA,
                    error_category=ErrorCategory.NO_READY_MATERIAL,
                )
            raise NoReadyCourseMaterialError(NO_READY_MATERIAL_MESSAGE)

        prompt = cls.build_prompt(
            material.text,
            question,
        )
        metadata = None

        try:
            if hasattr(provider, "generate_text_with_metadata"):
                answer, metadata = provider.generate_text_with_metadata(prompt)
            else:
                answer = provider.generate_text(prompt)
        except TextGenerationError as exc:
            if resolved_user_id:
                AiUsageLogger.log_failure(
                    db,
                    user_id=resolved_user_id,
                    course_id=course_id,
                    generation_type=GenerationType.COURSE_QA,
                    error_category=getattr(
                        exc, "error_category", ErrorCategory.PROVIDER_ERROR
                    ),
                )
            raise CourseQAError("Text generation provider failed.") from exc

        if resolved_user_id:
            AiUsageLogger.log_success(
                db,
                user_id=resolved_user_id,
                course_id=course_id,
                generation_type=GenerationType.COURSE_QA,
                metadata=metadata,
            )

        return CourseQAGeneration(
            response=CourseQAResponse(answer=answer),
            material=material,
            model_used=model_identifier(metadata),
        )
