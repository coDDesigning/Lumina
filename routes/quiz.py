from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from backend.app.database import get_db
from schemas.quiz import QuizGenerationResponse
from schemas.response import BaseResponse
from services.quiz import (
    NoReadyCourseMaterialError,
    QuizGenerationError,
    QuizService,
)
from services.text_generation import (
    TextGenerationError,
    get_text_generation_provider,
)
from utils.authorization import OwnedCourse


router = APIRouter(
    prefix="/api/courses",
    tags=["Quiz"],
)


@router.post(
    "/{course_id}/quiz",
    response_model=BaseResponse[QuizGenerationResponse],
    responses={
        401: {"description": "Authentication required"},
        404: {"description": "Course not found"},
    },
)
def generate_quiz(
    course: OwnedCourse,
    db: Annotated[Session, Depends(get_db)],
):
    try:
        provider = get_text_generation_provider()

        quiz_data = QuizService.generate(
            db,
            course.id,
            provider,
        )

        QuizService.save_generated_quiz(
            db,
            course.id,
            quiz_data,
        )

    except NoReadyCourseMaterialError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    except (TextGenerationError, QuizGenerationError) as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc

    return BaseResponse(
        success=True,
        message="Quiz generated successfully",
        data=quiz_data,
    )
