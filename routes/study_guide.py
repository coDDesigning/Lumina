from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.app.database import get_db
from schemas.response import BaseResponse
from schemas.study_guide import StudyGuideGenerationResult, StudyGuideRequest
from schemas.user import UserResponse
from services.study_guide import StudyGuideGenerationError, StudyGuideService
from services.text_generation import (
    TextGenerationError,
    get_text_generation_provider,
    resolve_effective_model,
)
from utils.ai_errors import ai_generation_http_exception
from utils.authorization import OwnedCourse
from utils.deps import get_current_user

router = APIRouter(prefix="/api/courses", tags=["Study Guide"])


@router.post(
    "/{course_id}/study-guide",
    response_model=BaseResponse[StudyGuideGenerationResult],
    responses={
        400: {"description": "No processed course material is available"},
        401: {"description": "Authentication required"},
        402: {"description": "Insufficient credits"},
        404: {"description": "Course not found"},
        422: {"description": "Invalid study guide request"},
        429: {"description": "AI provider rate limited"},
        503: {"description": "AI provider unreachable"},
        504: {"description": "AI provider timed out"},
    },
)
def generate_study_guide(
    course: OwnedCourse,
    request: StudyGuideRequest,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    try:
        effective_model = resolve_effective_model(
            request.model, current_user.preferred_model
        )
        try:
            provider = get_text_generation_provider(effective_model=effective_model)
        except TypeError:
            provider = get_text_generation_provider()
        generation = StudyGuideService.generate(
            db,
            course.id,
            request.summary_format,
            request.topic_focus,
            provider,
            user_id=current_user.id,
        )
        StudyGuideService.save_generated_output(
            db,
            course.id,
            generation.study_guide,
            user_id=current_user.id,
            model_used=generation.model_used,
        )
    except (TextGenerationError, StudyGuideGenerationError, Exception) as exc:
        raise ai_generation_http_exception(exc, feature="study_guide") from exc

    return BaseResponse(
        success=True,
        message="Study guide generated successfully",
        data=StudyGuideGenerationResult(
            study_guide=generation.study_guide,
            context_truncated=generation.material.truncated,
            chunks_used=generation.material.chunks_used,
            chunks_available=generation.material.chunks_available,
        ),
    )
