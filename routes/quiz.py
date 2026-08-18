from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.app.database import get_db
from schemas.quiz import QuizGenerationResult
from schemas.response import BaseResponse
from schemas.user import UserResponse
from services.quiz import QuizGenerationError, QuizService
from services.text_generation import TextGenerationError, get_text_generation_provider
from utils.ai_errors import ai_generation_http_exception
from utils.authorization import OwnedCourse
from utils.deps import get_current_user

router = APIRouter(
    prefix="/api/courses",
    tags=["Quiz"],
)


@router.post(
    "/{course_id}/quiz",
    response_model=BaseResponse[QuizGenerationResult],
    responses={
        400: {"description": "No processed course material is available"},
        401: {"description": "Authentication required"},
        404: {"description": "Course not found"},
        429: {"description": "AI provider rate limited"},
        503: {"description": "AI provider unreachable"},
        504: {"description": "AI provider timed out"},
    },
)
def generate_quiz(
    course: OwnedCourse,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    try:
        provider = get_text_generation_provider()

        generation = QuizService.generate(
            db,
            course.id,
            provider,
            user_id=current_user.id,
        )

        QuizService.save_generated_quiz(
            db,
            course.id,
            generation.quiz,
        )

    except (TextGenerationError, QuizGenerationError) as exc:
        raise ai_generation_http_exception(exc, feature="quiz") from exc

    return BaseResponse(
        success=True,
        message="Quiz generated successfully",
        data=QuizGenerationResult(
            quiz=generation.quiz,
            context_truncated=generation.material.truncated,
            chunks_used=generation.material.chunks_used,
            chunks_available=generation.material.chunks_available,
        ),
    )
