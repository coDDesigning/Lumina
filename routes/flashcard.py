from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.app.config import settings
from backend.app.database import get_db
from schemas.flashcard import (
    FlashcardGenerationContext,
    FlashcardGenerationResult,
    FlashcardGenerationSettings,
    FlashcardRequest,
)
from schemas.response import BaseResponse
from schemas.user import UserResponse
from services.credits import CreditService
from services.flashcard import FlashcardGenerationError, FlashcardService
from services.retrieval_material import RetrievalMaterialError
from services.text_generation import (
    TextGenerationError,
    get_text_generation_provider,
    resolve_effective_model,
)
from utils.ai_errors import ai_generation_http_exception
from utils.authorization import OwnedCourse
from utils.deps import get_current_user
from utils.rate_limit import rate_limit_generation

router = APIRouter(
    prefix="/api/courses",
    tags=["Flashcard"],
)


@router.post(
    "/{course_id}/flashcards",
    response_model=BaseResponse[FlashcardGenerationResult],
    dependencies=[Depends(rate_limit_generation("flashcard"))],
    responses={
        400: {"description": "No processed course material is available"},
        401: {"description": "Authentication required"},
        402: {"description": "Insufficient credits"},
        404: {"description": "Course not found"},
        409: {
            "description": (
                "No course material matched the request, or the course material "
                "is not searchable yet"
            )
        },
        429: {"description": "AI provider or per-user generation rate limited"},
        503: {"description": "AI provider or course search unreachable"},
        504: {"description": "AI provider timed out"},
    },
)
def generate_flashcards(
    course: OwnedCourse,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    request: FlashcardRequest | None = None,
):
    generation = None
    try:
        effective_model = resolve_effective_model(
            request.model if request else None,
            current_user.preferred_model,
            required_capability="flashcard",
        )
        provider = get_text_generation_provider(
            effective_model=effective_model,
            require_json_mode=True,
        )

        generation = FlashcardService.generate(
            db,
            course.id,
            provider,
            request=request,
            user_id=current_user.id,
        )

        applied_settings = FlashcardGenerationSettings.from_request(
            generation.effective_request,
            retrieval_limit=settings.retrieval_chunk_limit,
            retrieval_min_similarity=settings.retrieval_min_similarity,
        ).model_dump_json()
        applied_context = FlashcardGenerationContext.from_material(
            generation.material,
            profile_knowledge=generation.profile_knowledge,
        ).model_dump_json()

        persisted = FlashcardService.save_generated_flashcards(
            db,
            course.id,
            generation.flashcards,
            user_id=current_user.id,
            model_used=generation.model_used,
            generation_settings=applied_settings,
            generation_context=applied_context,
        )

    except (
        TextGenerationError,
        FlashcardGenerationError,
        RetrievalMaterialError,
        Exception,
    ) as exc:
        if generation is not None:
            db.rollback()
            CreditService.refund(db, generation.charge_receipt)
        raise ai_generation_http_exception(exc, feature="flashcard") from exc

    return BaseResponse(
        success=True,
        message="Flashcards generated successfully",
        data=FlashcardGenerationResult(
            flashcards=generation.flashcards,
            generated_output_id=persisted.id,
            retrieval_narrowed=generation.material.retrieval_narrowed,
            lowest_similarity=generation.material.lowest_similarity,
            highest_similarity=generation.material.highest_similarity,
            context_truncated=generation.material.truncated,
            chunks_used=generation.material.chunks_used,
            chunks_available=generation.material.chunks_available,
            profile_knowledge_used=bool(
                generation.profile_knowledge
                and not generation.profile_knowledge.is_empty
            ),
            profile_knowledge_items_used=(
                generation.profile_knowledge.items_used
                if generation.profile_knowledge
                else 0
            ),
        ),
    )
