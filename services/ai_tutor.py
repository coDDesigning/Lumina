from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session

from backend.app.config import settings
from backend.app.models import Course
from schemas.ai_tutor import AiTutorResponse
from schemas.ai_usage import ErrorCategory, GenerationType
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
)


class AiTutorError(RuntimeError):
    """AI tutor response generation failed."""


class NoReadyCourseMaterialError(AiTutorError, CourseMaterialUnavailableError):
    """No processed course material is available for AI tutor chat."""


@dataclass(frozen=True)
class AiTutorGeneration:
    response: AiTutorResponse
    material: CourseMaterial
    model_used: str


class AiTutorService:
    PROMPT_TEMPLATE_NAME = "ai_tutor"
    PROMPT_PATH = (
        Path(__file__).resolve().parents[1] / "app" / "prompts" / "ai_tutor.json"
    )

    @staticmethod
    def get_course_material(
        db: Session,
        course_id: int,
    ) -> CourseMaterial:
        return load_course_material(
            db,
            course_id,
            max_characters=settings.ai_tutor_material_max_chars,
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
    ) -> AiTutorGeneration:
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
                    generation_type=GenerationType.AI_TUTOR,
                    error_category=ErrorCategory.NO_READY_MATERIAL,
                )
            raise NoReadyCourseMaterialError(NO_READY_MATERIAL_MESSAGE)

        prompt = cls.build_prompt(
            material.text,
            question,
        )
        metadata = None

        if resolved_user_id:
            charged = UserService.charge_credits(db, resolved_user_id, 1.0)
            if not charged:
                AiUsageLogger.log_failure(
                    db,
                    user_id=resolved_user_id,
                    course_id=course_id,
                    generation_type=GenerationType.AI_TUTOR,
                    error_category=ErrorCategory.INSUFFICIENT_CREDITS,
                )
                raise InsufficientCreditsError("Insufficient credits.")

        try:
            if hasattr(provider, "generate_text_with_metadata"):
                answer, metadata = provider.generate_text_with_metadata(prompt)
            else:
                answer = provider.generate_text(prompt)
        except TextGenerationError as exc:
            if resolved_user_id:
                UserService.refund_credits(db, resolved_user_id, 1.0)
                AiUsageLogger.log_failure(
                    db,
                    user_id=resolved_user_id,
                    course_id=course_id,
                    generation_type=GenerationType.AI_TUTOR,
                    error_category=getattr(
                        exc, "error_category", ErrorCategory.PROVIDER_ERROR
                    ),
                )
            raise AiTutorError("Text generation provider failed.") from exc
        except Exception:
            if resolved_user_id:
                UserService.refund_credits(db, resolved_user_id, 1.0)
            raise

        if resolved_user_id:
            AiUsageLogger.log_success(
                db,
                user_id=resolved_user_id,
                course_id=course_id,
                generation_type=GenerationType.AI_TUTOR,
                metadata=metadata,
            )

        return AiTutorGeneration(
            response=AiTutorResponse(answer=answer),
            material=material,
            model_used=model_identifier(metadata),
        )
