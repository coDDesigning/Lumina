from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.app.database import get_db
from schemas.course_settings import CourseSettingsResponse, CourseSettingsUpdate
from schemas.response import BaseResponse
from services.course_settings import CourseSettingsService
from utils.authorization import AuthorizedCourse, OwnedCourse

router = APIRouter(prefix="/api/courses", tags=["Course Settings"])


@router.get(
    "/{course_id}/settings",
    response_model=BaseResponse[CourseSettingsResponse],
    responses={
        401: {"description": "Authentication required"},
        404: {"description": "Course not found"},
    },
)
def get_course_settings(
    course: AuthorizedCourse,
    db: Annotated[Session, Depends(get_db)],
):
    """Retrieves the settings for the specified course."""
    settings = CourseSettingsService.get_or_create(db, course.id)
    return BaseResponse(
        success=True,
        message="Course settings retrieved successfully",
        data=CourseSettingsService.to_response(settings),
    )


@router.patch(
    "/{course_id}/settings",
    response_model=BaseResponse[CourseSettingsResponse],
    responses={
        401: {"description": "Authentication required"},
        404: {"description": "Course not found"},
    },
)
def update_course_settings(
    course: OwnedCourse,
    update_data: CourseSettingsUpdate,
    db: Annotated[Session, Depends(get_db)],
):
    """Updates the settings for the specified course."""
    settings = CourseSettingsService.update(db, course.id, update_data)
    return BaseResponse(
        success=True,
        message="Course settings updated successfully",
        data=CourseSettingsService.to_response(settings),
    )
