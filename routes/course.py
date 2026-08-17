import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from backend.app.database import get_db
from schemas.course import CourseCreate, CourseResponse, CourseUpdate
from schemas.response import BaseResponse
from schemas.user import UserResponse
from services.course import CourseService
from storage.base import Storage, StorageError
from storage.dependencies import get_storage
from utils.authorization import AuthorizedCourse, DeletableCourse, OwnedCourse
from utils.deps import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/courses", tags=["Courses"])


@router.post(
    "/",
    response_model=BaseResponse[CourseResponse],
    status_code=status.HTTP_201_CREATED,
    responses={
        401: {"description": "Authentication required"},
    },
)
def create_course(
    course: CourseCreate,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """Creates a course owned by the authenticated user.

    Ownership comes from the verified token, never from the request body, so a
    caller cannot create a course under another account.
    """
    created_course = CourseService.create_course(db, course, current_user.id)
    return BaseResponse(
        success=True, message="Course created successfully", data=created_course
    )


@router.get(
    "/",
    response_model=BaseResponse[list[CourseResponse]],
    responses={
        401: {"description": "Authentication required"},
    },
)
def get_courses(
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """Lists the active courses the caller is allowed to read."""
    courses = CourseService.get_courses_for_user(db, current_user)
    return BaseResponse(
        success=True, message="Courses retrieved successfully", data=courses
    )


@router.get(
    "/{course_id}",
    response_model=BaseResponse[CourseResponse],
    responses={
        401: {"description": "Authentication required"},
        404: {"description": "Course not found"},
    },
)
def get_course(course: AuthorizedCourse):
    """Gets details of a course the caller is allowed to read."""
    return BaseResponse(
        success=True, message="Course retrieved successfully", data=course
    )


@router.put(
    "/{course_id}",
    response_model=BaseResponse[CourseResponse],
    responses={
        401: {"description": "Authentication required"},
        404: {"description": "Course not found"},
    },
)
def update_course(
    course_update: CourseUpdate,
    course: OwnedCourse,
    db: Annotated[Session, Depends(get_db)],
):
    """Updates a course the caller owns."""
    updated_course = CourseService.update_course(db, course.id, course_update)
    return BaseResponse(
        success=True, message="Course updated successfully", data=updated_course
    )


@router.delete(
    "/{course_id}",
    response_model=BaseResponse[CourseResponse],
    responses={
        401: {"description": "Authentication required"},
        404: {"description": "Course not found"},
        500: {"description": "Course cleanup failed"},
    },
)
def delete_course(
    course: DeletableCourse,
    db: Annotated[Session, Depends(get_db)],
    storage: Annotated[Storage, Depends(get_storage)],
    hard_delete: bool = False,
):
    """
    Deletes a course the caller owns.
    Performs a soft delete by default (moves to trash).
    Permanently deletes if hard_delete=True.
    """
    if hard_delete:
        stored_documents = CourseService.prepare_hard_delete(db, course.id)
        try:
            for storage_provider, storage_key in stored_documents:
                if storage_provider != storage.provider:
                    raise StorageError("Stored document uses another provider.")
                storage.delete(storage_key)
        except StorageError as exc:
            logger.exception("Course storage cleanup failed; metadata retained")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Course cleanup failed; retry hard deletion",
            ) from exc
        CourseService.finalize_hard_delete(db, course.id)
        return BaseResponse(
            success=True, message="Course permanently deleted", data=None
        )
    else:
        deleted_course = CourseService.soft_delete_course(db, course.id)
        return BaseResponse(
            success=True, message="Course soft deleted", data=deleted_course
        )
