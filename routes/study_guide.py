from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from backend.app.database import get_db
from schemas.response import BaseResponse
from schemas.study_guide import StudyGuideResponse
from schemas.user import UserResponse
from services.study_guide import StudyGuideGenerationError, StudyGuideService
from services.text_generation import TextGenerationError, get_text_generation_provider
from utils.deps import get_current_user


router = APIRouter(prefix="/api/courses", tags=["Study Guide"])


@router.post(
    "/{course_id}/study-guide",
    response_model=BaseResponse[StudyGuideResponse],
)
def generate_study_guide(
    course_id: int,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    try:
        provider = get_text_generation_provider()
        study_guide = StudyGuideService.generate(
            db,
            course_id,
            provider,
        )
        StudyGuideService.save_generated_output(
            db,
            course_id,
            study_guide,
        )
    except (TextGenerationError, StudyGuideGenerationError) as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc

    return BaseResponse(
        success=True,
        message="Study guide generated successfully",
        data=study_guide,
    )
