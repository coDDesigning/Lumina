from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.app.database import get_db
from schemas.flashcard import FlashcardGenerationResult, FlashcardRequest
from schemas.response import BaseResponse
from schemas.user import UserResponse
from services.credits import CreditService
from services.flashcard import FlashcardGenerationError, FlashcardService
from services.text_generation import (
    TextGenerationError,
    get_text_generation_provider,
    resolve_effective_model,
)
from utils.ai_errors import ai_generation_http_exception
from utils.authorization import OwnedCourse
from utils.deps import get_current_user

router = APIRouter(
    prefix="/api/courses",
    tags=["Flashcard"],
)


@router.post(
    "/{course_id}/flashcards",
    response_model=BaseResponse[FlashcardGenerationResult],
    responses={
        400: {"description": "No processed course material is available"},
        401: {"description": "Authentication required"},
        402: {"description": "Insufficient credits"},
        404: {"description": "Course not found"},
        429: {"description": "AI provider rate limited"},
        503: {"description": "AI provider unreachable"},
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
        )
        try:
            provider = get_text_generation_provider(effective_model=effective_model)
        except TypeError:
            provider = get_text_generation_provider()

        generation = FlashcardService.generate(
            db,
            course.id,
            provider,
            user_id=current_user.id,
        )

        FlashcardService.save_generated_flashcards(
            db,
            course.id,
            generation.flashcards,
            user_id=current_user.id,
            model_used=generation.model_used,
        )

    except (TextGenerationError, FlashcardGenerationError, Exception) as exc:
        if generation is not None:
            db.rollback()
            CreditService.refund(db, generation.charge_receipt)
        raise ai_generation_http_exception(exc, feature="flashcard") from exc

    return BaseResponse(
        success=True,
        message="Flashcards generated successfully",
        data=FlashcardGenerationResult(
            flashcards=generation.flashcards,
            context_truncated=generation.material.truncated,
            chunks_used=generation.material.chunks_used,
            chunks_available=generation.material.chunks_available,
        ),
    )
