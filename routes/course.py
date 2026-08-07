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
from utils.deps import get_current_admin

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/courses", tags=["Courses"])


@router.post(
    "/",
    response_model=BaseResponse[CourseResponse],
    status_code=status.HTTP_201_CREATED,
)
def create_course(
    course: CourseCreate,
    current_admin: Annotated[UserResponse, Depends(get_current_admin)],
    db: Annotated[Session, Depends(get_db)],
):
    """Creates a new course."""
    created_course = CourseService.create_course(db, course, current_admin.id)
    return BaseResponse(
        success=True, message="Course created successfully", data=created_course
    )


@router.get("/", response_model=BaseResponse[list[CourseResponse]])
def get_courses(db: Annotated[Session, Depends(get_db)]):
    """Lists all active (non-deleted) courses."""
    courses = CourseService.get_all_courses(db, include_deleted=False)
    return BaseResponse(
        success=True, message="Courses retrieved successfully", data=courses
    )


@router.get("/{course_id}", response_model=BaseResponse[CourseResponse])
def get_course(course_id: int, db: Annotated[Session, Depends(get_db)]):
    """Gets details of a specific course."""
    course = CourseService.get_course_by_id(db, course_id)
    return BaseResponse(
        success=True, message="Course retrieved successfully", data=course
    )


@router.put("/{course_id}", response_model=BaseResponse[CourseResponse])
def update_course(
    course_id: int,
    course_update: CourseUpdate,
    current_admin: Annotated[UserResponse, Depends(get_current_admin)],
    db: Annotated[Session, Depends(get_db)],
):
    """Updates course information."""
    updated_course = CourseService.update_course(db, course_id, course_update)
    return BaseResponse(
        success=True, message="Course updated successfully", data=updated_course
    )


@router.delete("/{course_id}", response_model=BaseResponse[CourseResponse])
def delete_course(
    course_id: int,
    current_admin: Annotated[UserResponse, Depends(get_current_admin)],
    db: Annotated[Session, Depends(get_db)],
    storage: Annotated[Storage, Depends(get_storage)],
    hard_delete: bool = False,
):
    """
    Deletes a course.
    Performs a soft delete by default (moves to trash).
    Permanently deletes if hard_delete=True.
    """
    if hard_delete:
        stored_documents = CourseService.prepare_hard_delete(db, course_id)
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
        CourseService.finalize_hard_delete(db, course_id)
        return BaseResponse(
            success=True, message="Course permanently deleted", data=None
        )
    else:
        deleted_course = CourseService.soft_delete_course(db, course_id)
        return BaseResponse(
            success=True, message="Course soft deleted", data=deleted_course
        )
