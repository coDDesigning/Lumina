from typing import List, Optional
from datetime import datetime
from schemas.course import CourseCreate, CourseUpdate, CourseResponse
from utils.exceptions import NotFoundException

# Temporary in-memory database
_courses_db: List[dict] = []
_id_counter = 1

class CourseService:
    @staticmethod
    def get_all_courses(include_deleted: bool = False) -> List[CourseResponse]:
        if include_deleted:
            return [CourseResponse(**course) for course in _courses_db]
        return [CourseResponse(**course) for course in _courses_db if not course.get("is_deleted")]

    @staticmethod
    def get_course_by_id(course_id: int) -> CourseResponse:
        for course in _courses_db:
            if course["id"] == course_id and not course.get("is_deleted"):
                return CourseResponse(**course)
        raise NotFoundException(detail="Course not found")

    @staticmethod
    def create_course(course_data: CourseCreate) -> CourseResponse:
        global _id_counter
        new_course = course_data.model_dump()
        new_course["id"] = _id_counter
        new_course["created_at"] = datetime.utcnow()
        new_course["is_deleted"] = False
        
        _courses_db.append(new_course)
        _id_counter += 1
        return CourseResponse(**new_course)

    @staticmethod
    def update_course(course_id: int, update_data: CourseUpdate) -> CourseResponse:
        for course in _courses_db:
            if course["id"] == course_id and not course.get("is_deleted"):
                update_dict = update_data.model_dump(exclude_unset=True)
                course.update(update_dict)
                return CourseResponse(**course)
        raise NotFoundException(detail="Course not found")

    @staticmethod
    def soft_delete_course(course_id: int) -> CourseResponse:
        """Moves the course to trash (soft delete by setting is_deleted=True)."""
        for course in _courses_db:
            if course["id"] == course_id and not course.get("is_deleted"):
                course["is_deleted"] = True
                return CourseResponse(**course)
        raise NotFoundException(detail="Course not found")

    @staticmethod
    def hard_delete_course(course_id: int) -> bool:
        """Permanently deletes the course from memory."""
        global _courses_db
        for i, course in enumerate(_courses_db):
            if course["id"] == course_id:
                _courses_db.pop(i)
                return True
        raise NotFoundException(detail="Course not found")
