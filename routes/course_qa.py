from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.app.database import get_db
from schemas.course_qa import CourseQAGenerationResult, CourseQARequest
from schemas.response import BaseResponse
from schemas.user import UserResponse
from services.course_qa import CourseQAService
from services.text_generation import (
    get_text_generation_provider,
    resolve_effective_model,
)
from utils.ai_errors import ai_generation_http_exception
from utils.authorization import OwnedCourse
from utils.deps import get_current_user

router = APIRouter(
    prefix="/api/courses",
    tags=["Course QA"],
)


@router.post(
    "/{course_id}/qa",
    response_model=BaseResponse[CourseQAGenerationResult],
    responses={
        400: {"description": "No processed course material is available"},
        401: {"description": "Authentication required"},
        402: {"description": "Insufficient credits"},
        404: {"description": "Course not found"},
        409: {"description": "Course material is not indexed or did not match"},
        429: {"description": "AI provider rate limited"},
        503: {"description": "AI provider unreachable"},
        504: {"description": "AI provider timed out"},
    },
)
def ask_course_question(
    course: OwnedCourse,
    request: CourseQARequest,
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

        generation = CourseQAService.generate(
            db,
            course.id,
            request.question,
            provider,
            user_id=current_user.id,
            conversation_id=request.conversation_id,
        )

    except HTTPException:
        raise
    except Exception as exc:
        raise ai_generation_http_exception(exc, feature="course_qa") from exc

    return BaseResponse(
        success=True,
        message="Course Q&A answer generated successfully",
        data=CourseQAGenerationResult(
            answer=generation.response.answer,
            conversation_id=generation.conversation_id,
            context_truncated=generation.material.truncated,
            chunks_used=generation.material.chunks_used,
            chunks_available=generation.material.chunks_available,
            retrieval_narrowed=generation.material.retrieval_narrowed,
            lowest_similarity=generation.material.lowest_similarity,
            highest_similarity=generation.material.highest_similarity,
        ),
    )
