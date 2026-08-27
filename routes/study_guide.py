from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
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
from services.credits import CreditService
from services.study_guide import StudyGuideService
from services.text_generation import (
    get_text_generation_provider,
    resolve_effective_model,
)
from utils.ai_errors import ai_generation_http_exception
from utils.authorization import OwnedCourse
from utils.deps import get_current_user
from utils.rate_limit import rate_limit_generation

router = APIRouter(prefix="/api/courses", tags=["Study Guide"])


@router.post(
    "/{course_id}/study-guide",
    response_model=BaseResponse[StudyGuideGenerationResult],
    dependencies=[Depends(rate_limit_generation("study_guide"))],
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
        422: {"description": "Invalid study guide request"},
        429: {"description": "AI provider or per-user generation rate limited"},
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
    generation = None
    try:
        effective_model = resolve_effective_model(
            request.model,
            current_user.preferred_model,
            required_capability="study_guide",
        )
        try:
            provider = get_text_generation_provider(effective_model=effective_model)
        except TypeError:
            provider = get_text_generation_provider()
        generation = StudyGuideService.generate(
            db,
            course.id,
            request,
            provider,
            user_id=current_user.id,
        )
        applied_settings = StudyGuideGenerationSettings.from_request(
            generation.effective_request,
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
                generation.material,
                profile_knowledge=generation.profile_knowledge,
            ).model_dump_json(),
        )
    except HTTPException:
        raise
    except Exception as exc:
        if generation is not None:
            db.rollback()
            CreditService.refund(db, generation.charge_receipt)
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
