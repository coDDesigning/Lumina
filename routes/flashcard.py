from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from backend.app.database import get_db
from schemas.flashcard import FlashcardGenerationResponse
from schemas.response import BaseResponse
from services.flashcard import (
    FlashcardGenerationError,
    FlashcardService,
    NoReadyCourseMaterialError,
)
from services.text_generation import (
    TextGenerationError,
    get_text_generation_provider,
)
from utils.authorization import OwnedCourse

router = APIRouter(
    prefix="/api/courses",
    tags=["Flashcard"],
)


@router.post(
    "/{course_id}/flashcards",
    response_model=BaseResponse[FlashcardGenerationResponse],
    responses={
        401: {"description": "Authentication required"},
        404: {"description": "Course not found"},
    },
)
def generate_flashcards(
    course: OwnedCourse,
    db: Annotated[Session, Depends(get_db)],
):
    try:
        provider = get_text_generation_provider()

        flashcards = FlashcardService.generate(
            db,
            course.id,
            provider,
            user_id=course.owner_id,
        )

        FlashcardService.save_generated_flashcards(
            db,
            course.id,
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
