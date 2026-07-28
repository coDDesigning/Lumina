from fastapi import APIRouter, Depends, status
from typing import List
from schemas.course import CourseCreate, CourseUpdate, CourseResponse
from schemas.response import BaseResponse
from services.course import CourseService
from utils.deps import get_current_user
from schemas.user import UserResponse

router = APIRouter(prefix="/api/courses", tags=["Courses"])

@router.post("/", response_model=BaseResponse[CourseResponse], status_code=status.HTTP_201_CREATED)
async def create_course(
    course: CourseCreate, 
    current_user: UserResponse = Depends(get_current_user)
):
    """Creates a new course."""
    created_course = CourseService.create_course(course)
    return BaseResponse(success=True, message="Course created successfully", data=created_course)

@router.get("/", response_model=BaseResponse[List[CourseResponse]])
async def get_courses():
    """Lists all active (non-deleted) courses."""
    courses = CourseService.get_all_courses(include_deleted=False)
    return BaseResponse(success=True, message="Courses retrieved successfully", data=courses)

@router.get("/{course_id}", response_model=BaseResponse[CourseResponse])
async def get_course(course_id: int):
    """Gets details of a specific course."""
    course = CourseService.get_course_by_id(course_id)
    return BaseResponse(success=True, message="Course retrieved successfully", data=course)

@router.put("/{course_id}", response_model=BaseResponse[CourseResponse])
async def update_course(
    course_id: int, 
    course_update: CourseUpdate,
    current_user: UserResponse = Depends(get_current_user)
):
    """Updates course information."""
    updated_course = CourseService.update_course(course_id, course_update)
    return BaseResponse(success=True, message="Course updated successfully", data=updated_course)

@router.delete("/{course_id}", response_model=BaseResponse[CourseResponse])
async def delete_course(
    course_id: int,
    hard_delete: bool = False,
    current_user: UserResponse = Depends(get_current_user)
):
    """
    Deletes a course.
    Performs a soft delete by default (moves to trash).
    Permanently deletes if hard_delete=True.
    """
    if hard_delete:
        CourseService.hard_delete_course(course_id)
        return BaseResponse(success=True, message="Course permanently deleted", data=None)
    else:
        deleted_course = CourseService.soft_delete_course(course_id)
        return BaseResponse(success=True, message="Course soft deleted", data=deleted_course)
