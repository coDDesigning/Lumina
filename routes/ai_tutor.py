from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.app.database import get_db
from schemas.ai_tutor import AiTutorGenerationResult, AiTutorRequest
from schemas.response import BaseResponse
from schemas.user import UserResponse
from services.ai_tutor import AiTutorError, AiTutorService
from services.text_generation import TextGenerationError, get_text_generation_provider
from utils.ai_errors import ai_generation_http_exception
from utils.authorization import OwnedCourse
from utils.deps import get_current_user

router = APIRouter(
    prefix="/api/courses",
    tags=["AI Tutor"],
)


@router.post(
    "/{course_id}/ai-tutor",
    response_model=BaseResponse[AiTutorGenerationResult],
    responses={
        400: {"description": "No processed course material is available"},
        401: {"description": "Authentication required"},
        404: {"description": "Course not found"},
        429: {"description": "AI provider rate limited"},
        503: {"description": "AI provider unreachable"},
        504: {"description": "AI provider timed out"},
    },
)
def ask_ai_tutor(
    course: OwnedCourse,
    request: AiTutorRequest,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    try:
        provider = get_text_generation_provider()

        generation = AiTutorService.generate(
            db,
            course.id,
            request.question,
            provider,
            user_id=current_user.id,
        )

    except (TextGenerationError, AiTutorError) as exc:
        raise ai_generation_http_exception(exc, feature="ai_tutor") from exc

    return BaseResponse(
        success=True,
        message="AI tutor response generated successfully",
        data=AiTutorGenerationResult(
            answer=generation.response.answer,
            context_truncated=generation.material.truncated,
            chunks_used=generation.material.chunks_used,
            chunks_available=generation.material.chunks_available,
        ),
    )
