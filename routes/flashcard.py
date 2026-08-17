from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from backend.app.database import get_db
from schemas.flashcard import FlashcardGenerationResponse
from schemas.response import BaseResponse
from schemas.user import UserResponse
from services.flashcard import (
    FlashcardGenerationError,
    FlashcardService,
    NoReadyCourseMaterialError,
)
from services.text_generation import (
    TextGenerationError,
    get_text_generation_provider,
)
from utils.deps import get_current_user


router = APIRouter(
    prefix="/api/courses",
    tags=["Flashcard"],
)


@router.post(
    "/{course_id}/flashcards",
    response_model=BaseResponse[FlashcardGenerationResponse],
)
def generate_flashcards(
    course_id: int,
    current_user: Annotated[
        UserResponse,
        Depends(get_current_user),
    ],
    db: Annotated[Session, Depends(get_db)],
):
    try:
        provider = get_text_generation_provider()

        flashcards = FlashcardService.generate(
            db,
            course_id,
            provider,
        )

        FlashcardService.save_generated_flashcards(
            db,
            course_id,
            flashcards,
        )

    except NoReadyCourseMaterialError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    except (TextGenerationError, FlashcardGenerationError) as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc

    return BaseResponse(
        success=True,
        message="Flashcards generated successfully",
        data=flashcards,
    )
