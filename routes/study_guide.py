from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from backend.app.database import get_db
from schemas.response import BaseResponse
from schemas.study_guide import StudyGuideResponse
from services.study_guide import StudyGuideGenerationError, StudyGuideService
from services.text_generation import (
    TextGenerationConnectionError,
    TextGenerationError,
    get_text_generation_provider,
)
from utils.authorization import OwnedCourse

router = APIRouter(prefix="/api/courses", tags=["Study Guide"])


@router.post(
    "/{course_id}/study-guide",
    response_model=BaseResponse[StudyGuideResponse],
    responses={
        401: {"description": "Authentication required"},
        404: {"description": "Course not found"},
        429: {"description": "AI provider rate limited"},
        503: {"description": "AI provider unreachable"},
        504: {"description": "AI provider timed out"},
    },
)
def generate_study_guide(
    course: OwnedCourse,
    db: Annotated[Session, Depends(get_db)],
):
    try:
        provider = get_text_generation_provider()
        study_guide = StudyGuideService.generate(
            db,
            course.id,
            provider,
            user_id=course.owner_id,
        )
        StudyGuideService.save_generated_output(
            db,
            course.id,
            study_guide,
        )
    except TextGenerationConnectionError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc

    except (TextGenerationError, StudyGuideGenerationError) as exc:
        cause = exc.__cause__ if exc.__cause__ is not None else exc
        error_cat = getattr(
            cause, "error_category", getattr(exc, "error_category", None)
        )
        if error_cat == "rate_limit":
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=str(cause or exc),
            ) from exc
        if error_cat == "timeout":
            raise HTTPException(
                status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                detail=str(cause or exc),
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc

    return BaseResponse(
        success=True,
        message="Study guide generated successfully",
        data=study_guide,
    )
