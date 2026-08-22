from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError
from sqlalchemy.orm import Session

from backend.app.config import settings
from backend.app.models import Course, GeneratedOutput
from schemas.ai_usage import ErrorCategory, GenerationType
from schemas.flashcard import (
    FlashcardGenerationResponse,
    FlashcardRequest,
)
from services.ai_usage_logger import AiUsageLogger
from services.course_material import count_available_chunks
from services.generated_output import GeneratedOutputService
from services.profile_knowledge import (
    ProfileKnowledgeContext,
    assemble_generation_context,
    format_profile_context,
)
from schemas.prompt_context import PromptContext
from services.prompt_context import resolve_prompt_context
from services.prompt_loader import PromptLoader
from services.retrieval_material import (
    MaterialNotIndexedError,
    MaterialRetrievalError,
    NoRelevantMaterialError,
    RetrievedCourseMaterial,
    load_retrieved_material,
)
from services.retrieval_query import build_retrieval_query
from services.text_generation import (
    TextGenerationError,
    TextGenerationProvider,
    model_identifier,
)
from services.credits import GENERATION_CREDIT_COSTS, CreditService
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
    material: RetrievedCourseMaterial
    model_used: str
    effective_request: FlashcardRequest
    profile_knowledge: ProfileKnowledgeContext | None = None


class FlashcardService:
    PROMPT_TEMPLATE_NAME = "flashcard"
    PROMPT_PATH = (
        Path(__file__).resolve().parents[1] / "app" / "prompts" / "flashcard.json"
    )

    @staticmethod
    def build_retrieval_query(course: Course | None, request: FlashcardRequest) -> str:
        return build_retrieval_query(course, request.topic_focus)

    @staticmethod
    def get_course_material(
        db: Session,
        course_id: int,
        *,
        query: str,
    ) -> RetrievedCourseMaterial:
        return load_retrieved_material(
            db,
            course_id,
            query=query,
            limit=settings.retrieval_chunk_limit,
            min_similarity=settings.retrieval_min_similarity,
            max_characters=settings.flashcard_material_max_chars,
        )

    @classmethod
    def build_prompt(
        cls,
        course_material: str,
        *,
        topic_focus: str = "All Topics",
        profile_knowledge: ProfileKnowledgeContext | None = None,
        context: PromptContext,
    ) -> str:
        return PromptLoader.render(
            cls.PROMPT_TEMPLATE_NAME,
            {
                **context.as_variables(),
                "TOPIC_FOCUS": topic_focus,
                "TEXT": course_material,
                "PROFILE_CONTEXT": format_profile_context(profile_knowledge),
            },
        )

    @classmethod
    def generate(
        cls,
        db: Session,
        course_id: int,
        provider: TextGenerationProvider,
        request: FlashcardRequest | None = None,
        user_id: int | None = None,
        *,
        include_profile_context: bool = False,
        use_profile_knowledge: bool | None = None,
    ) -> FlashcardGeneration:
        if request is None:
            effective_request = FlashcardRequest()
        else:
            effective_request = request

        if use_profile_knowledge is not None:
            effective_request = FlashcardRequest(
                topic_focus=effective_request.topic_focus,
                use_profile_knowledge=use_profile_knowledge,
                model=effective_request.model,
            )
        elif include_profile_context:
            effective_request = FlashcardRequest(
                topic_focus=effective_request.topic_focus,
                use_profile_knowledge=True,
                model=effective_request.model,
            )

        course = db.get(Course, course_id)
        resolved_user_id = user_id
        if resolved_user_id is None and course is not None:
            resolved_user_id = course.owner_id

        def log_failure(category: ErrorCategory, **extra) -> None:
            if resolved_user_id:
                AiUsageLogger.log_failure(
                    db,
                    user_id=resolved_user_id,
                    course_id=course_id,
                    generation_type=GenerationType.FLASHCARD,
                    error_category=category,
                    **extra,
                )

        if count_available_chunks(db, course_id) == 0:
            log_failure(ErrorCategory.NO_READY_MATERIAL)
            raise NoReadyCourseMaterialError(NO_READY_MATERIAL_MESSAGE)

        query = cls.build_retrieval_query(course, effective_request)

        try:
            material = cls.get_course_material(db, course_id, query=query)
        except MaterialNotIndexedError:
            log_failure(ErrorCategory.MATERIAL_NOT_INDEXED)
            raise
        except NoRelevantMaterialError:
            log_failure(ErrorCategory.NO_RELEVANT_MATERIAL)
            raise
        except MaterialRetrievalError:
            log_failure(ErrorCategory.RETRIEVAL_ERROR)
            raise

        generation_ctx = assemble_generation_context(
            db,
            course_id=course_id,
            user_id=resolved_user_id,
            course_material=material,
            include_profile_context=effective_request.use_profile_knowledge,
        )

        prompt_context = resolve_prompt_context(
            db, course=course, user_id=resolved_user_id
        )
        prompt = cls.build_prompt(
            generation_ctx.course_material.text,
            topic_focus=effective_request.topic_focus,
            profile_knowledge=generation_ctx.profile_knowledge,
            context=prompt_context,
        )
        metadata = None

        receipt = None
        if resolved_user_id:
            receipt = CreditService.charge(
                db,
                resolved_user_id,
                GENERATION_CREDIT_COSTS["flashcard"],
                source_type="flashcard",
            )
            if receipt is None:
                log_failure(ErrorCategory.INSUFFICIENT_CREDITS)
                raise InsufficientCreditsError("Insufficient credits.")

        try:
            if hasattr(provider, "generate_json_with_metadata"):
                result, metadata = provider.generate_json_with_metadata(prompt)
            else:
                result = provider.generate_json(prompt)
        except TextGenerationError as exc:
            if resolved_user_id:
                CreditService.refund(db, receipt)
                log_failure(
                    getattr(exc, "error_category", ErrorCategory.PROVIDER_ERROR)
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
                log_failure(
                    ErrorCategory.INVALID_STRUCTURE,
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
            effective_request=effective_request,
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
