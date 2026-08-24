from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.app.database import get_db
from schemas.progress import CourseProgressSummary
from schemas.response import BaseResponse
from schemas.user import UserResponse
from services.progress import ProgressService
from utils.deps import get_current_user

router = APIRouter(prefix="/api", tags=["Progress"])


@router.get(
    "/progress",
    response_model=BaseResponse[list[CourseProgressSummary]],
    responses={
        401: {"description": "Authentication required"},
    },
)
def list_course_progress(
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    summaries = ProgressService.list_course_summaries(db, user_id=current_user.id)
    return BaseResponse(
        success=True,
        message="Course progress retrieved successfully",
        data=summaries,
    )
