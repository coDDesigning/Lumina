from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.app.database import get_db
from backend.app.models import User
from schemas.ai_tutor import AiTutorGenerationResult, AiTutorRequest
from schemas.response import BaseResponse
from schemas.user import UserResponse
from services.ai_tutor import AiTutorService
from services.text_generation import (
    get_text_generation_provider,
    resolve_effective_model,
)
from utils.ai_errors import ai_generation_http_exception
from utils.authorization import OwnedCourse
from utils.deps import get_current_user
from utils.rate_limit import rate_limit_generation

router = APIRouter(
    prefix="/api/courses",
    tags=["AI Tutor"],
)


@router.post(
    "/{course_id}/ai-tutor",
    response_model=BaseResponse[AiTutorGenerationResult],
    dependencies=[Depends(rate_limit_generation("ai_tutor"))],
    responses={
        400: {"description": "No processed course material is available"},
        401: {"description": "Authentication required"},
        402: {"description": "Insufficient credits"},
        404: {"description": "Course not found"},
        409: {"description": "Course material is not indexed or did not match"},
        429: {"description": "AI provider or per-user generation rate limited"},
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
        db_user = db.get(User, current_user.id)
        effective_model = resolve_effective_model(
            request.model,
            current_user.preferred_model,
            required_capability="ai_tutor",
        )
        try:
            provider = get_text_generation_provider(
                effective_model=effective_model,
                user=db_user,
            )
        except TypeError:
            try:
                provider = get_text_generation_provider(effective_model=effective_model)
            except TypeError:
                provider = get_text_generation_provider()

        generation = AiTutorService.generate(
            db,
            course.id,
            request.question,
            provider,
            user_id=current_user.id,
            conversation_id=request.conversation_id,
            use_profile_knowledge=request.use_profile_knowledge,
        )

    except HTTPException:
        raise
    except Exception as exc:
        raise ai_generation_http_exception(exc, feature="ai_tutor") from exc

    return BaseResponse(
        success=True,
        message="AI tutor response generated successfully",
        data=AiTutorGenerationResult(
            answer=generation.response.answer,
            citations=generation.response.citations,
            conversation_id=generation.conversation_id,
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
