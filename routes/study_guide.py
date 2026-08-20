from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.app.config import settings
from backend.app.database import get_db
from schemas.response import BaseResponse
from schemas.study_guide import (
    StudyGuideGenerationContext,
    StudyGuideGenerationResult,
    StudyGuideGenerationSettings,
    StudyGuideRequest,
)
from schemas.user import UserResponse
from services.retrieval_material import RetrievalMaterialError
from services.study_guide import StudyGuideGenerationError, StudyGuideService
from services.text_generation import TextGenerationError, get_text_generation_provider
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
        404: {"description": "Course not found"},
        409: {"description": "No course material matched the request"},
        422: {"description": "Invalid study guide request"},
        429: {"description": "AI provider rate limited"},
        503: {"description": "AI provider or course search unreachable"},
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
        provider = get_text_generation_provider()
        generation = StudyGuideService.generate(
            db,
            course.id,
            request,
            provider,
            user_id=current_user.id,
        )
        applied_settings = StudyGuideGenerationSettings.from_request(
            request,
            retrieval_limit=settings.retrieval_chunk_limit,
            retrieval_min_similarity=settings.retrieval_min_similarity,
        )
        persisted = StudyGuideService.save_generated_output(
            db,
            course.id,
            generation.study_guide,
            user_id=current_user.id,
            model_used=generation.model_used,
            generation_settings=applied_settings.model_dump_json(),
            generation_context=StudyGuideGenerationContext.from_material(
                generation.material
            ).model_dump_json(),
        )
    except (
        TextGenerationError,
        StudyGuideGenerationError,
        RetrievalMaterialError,
    ) as exc:
        raise ai_generation_http_exception(exc, feature="study_guide") from exc

    return BaseResponse(
        success=True,
        message="Study guide generated successfully",
        data=StudyGuideGenerationResult(
            study_guide=generation.study_guide,
            generated_output_id=persisted.id,
            context_truncated=generation.material.truncated,
            chunks_used=generation.material.chunks_used,
            chunks_available=generation.material.chunks_available,
            retrieval_narrowed=generation.material.retrieval_narrowed,
            lowest_similarity=generation.material.lowest_similarity,
            highest_similarity=generation.material.highest_similarity,
        ),
    )
