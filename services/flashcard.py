from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError
from sqlalchemy.orm import Session

from backend.app.config import settings
from backend.app.models import Course, GeneratedOutput
from schemas.ai_usage import ErrorCategory, GenerationType
from schemas.flashcard import FlashcardGenerationResponse
from services.ai_usage_logger import AiUsageLogger
from services.course_material import CourseMaterial, load_course_material
from services.generated_output import GeneratedOutputService
from services.profile_knowledge import (
    ProfileKnowledgeContext,
    assemble_generation_context,
)
from services.prompt_loader import PromptLoader
from services.text_generation import (
    TextGenerationError,
    TextGenerationProvider,
    model_identifier,
)
from services.credits import CreditService
from utils.ai_errors import (
    NO_READY_MATERIAL_MESSAGE,
    CourseMaterialUnavailableError,
    InsufficientCreditsError,
    InvalidGeneratedStructureError,
)


class FlashcardGenerationError(RuntimeError):
    """Flashcard generation failed."""


class NoReadyCourseMaterialError(
    FlashcardGenerationError, CourseMaterialUnavailableError
):
    """No processed course material is available for flashcard generation."""


class InvalidFlashcardStructureError(
    FlashcardGenerationError, InvalidGeneratedStructureError
):
    """The provider returned something that is not a valid flashcard set."""


@dataclass(frozen=True)
class FlashcardGeneration:
    flashcards: FlashcardGenerationResponse
    material: CourseMaterial
    model_used: str
    profile_knowledge: ProfileKnowledgeContext | None = None


class FlashcardService:
    PROMPT_TEMPLATE_NAME = "flashcard"
    PROMPT_PATH = (
        Path(__file__).resolve().parents[1] / "app" / "prompts" / "flashcard.json"
    )

    @staticmethod
    def get_course_material(
        db: Session,
        course_id: int,
    ) -> CourseMaterial:
        return load_course_material(
            db,
            course_id,
            max_characters=settings.flashcard_material_max_chars,
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
        *,
        include_profile_context: bool = False,
    ) -> FlashcardGeneration:
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
                    generation_type=GenerationType.FLASHCARD,
                    error_category=ErrorCategory.NO_READY_MATERIAL,
                )
            raise NoReadyCourseMaterialError(NO_READY_MATERIAL_MESSAGE)

        generation_ctx = assemble_generation_context(
            db,
            course_id=course_id,
            user_id=resolved_user_id,
            course_material=material,
            include_profile_context=include_profile_context,
        )

        prompt = cls.build_prompt(generation_ctx.combined_text)
        metadata = None

        receipt = None
        if resolved_user_id:
            receipt = CreditService.charge(
                db, resolved_user_id, 1.0, source_type="flashcard"
            )
            if receipt is None:
                AiUsageLogger.log_failure(
                    db,
                    user_id=resolved_user_id,
                    course_id=course_id,
                    generation_type=GenerationType.FLASHCARD,
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
                CreditService.refund(db, receipt)
                AiUsageLogger.log_failure(
                    db,
                    user_id=resolved_user_id,
                    course_id=course_id,
                    generation_type=GenerationType.FLASHCARD,
                    error_category=getattr(
                        exc, "error_category", ErrorCategory.PROVIDER_ERROR
                    ),
                )
            raise FlashcardGenerationError("Text generation provider failed.") from exc
        except Exception:
            if resolved_user_id:
                CreditService.refund(db, receipt)
            raise

        try:
            validated = FlashcardGenerationResponse.model_validate(result)
        except ValidationError as exc:
            if resolved_user_id:
                CreditService.refund(db, receipt)
                AiUsageLogger.log_failure(
                    db,
                    user_id=resolved_user_id,
                    course_id=course_id,
                    generation_type=GenerationType.FLASHCARD,
                    error_category=ErrorCategory.INVALID_STRUCTURE,
                    latency_ms=metadata.latency_ms if metadata else None,
                )
            raise InvalidFlashcardStructureError(
                "Generated flashcards have an invalid structure."
            ) from exc

        if resolved_user_id:
            AiUsageLogger.log_success(
                db,
                user_id=resolved_user_id,
                course_id=course_id,
                generation_type=GenerationType.FLASHCARD,
                metadata=metadata,
            )

        return FlashcardGeneration(
            flashcards=validated,
            material=material,
            model_used=model_identifier(metadata),
            profile_knowledge=generation_ctx.profile_knowledge,
        )

    @staticmethod
    def save_generated_flashcards(
        db: Session,
        course_id: int,
        flashcards: FlashcardGenerationResponse,
        *,
        user_id: int,
        model_used: str,
        generation_settings: str | None = None,
        generation_context: str | None = None,
    ) -> GeneratedOutput:
        return GeneratedOutputService.record(
            db,
            course_id=course_id,
            user_id=user_id,
            output_type="flashcards",
            content=flashcards.model_dump_json(),
            model_used=model_used,
            generation_settings=generation_settings,
            generation_context=generation_context,
        )
