"""Single authorization boundary for every course-scoped operation.

Course resources are owner-scoped. A request naming a course the caller may not
use is answered with 404 so existence is never disclosed: a missing course, a
soft-deleted course, and another owner's course are indistinguishable to anyone
enumerating identifiers.

The administrator policy is deliberate rather than emergent. It lives in
``can_read_any_course`` and ``can_write_any_course``; changing the policy means
editing those two predicates and nothing else.
"""

from typing import Annotated

from fastapi import Depends
from sqlalchemy import ColumnElement, select
from sqlalchemy.orm import Session

from backend.app.database import get_db
from backend.app.models import Course
from schemas.course import CourseResponse
from schemas.user import Role, UserResponse
from utils.deps import get_current_user
from utils.exceptions import NotFoundException

__all__ = [
    "COURSE_NOT_FOUND",
    "AuthorizedCourse",
    "DeletableCourse",
    "OwnedCourse",
    "can_read_any_course",
    "can_write_any_course",
    "deletable_course_criteria",
    "readable_course_criteria",
    "require_course_access",
    "require_course_deletion",
    "require_course_owner",
    "writable_course_criteria",
]

# Every denial reuses one message so responses cannot be told apart.
COURSE_NOT_FOUND = "Course not found"


def can_read_any_course(user: UserResponse) -> bool:
    """Administrators may read every course for support and administration."""
    return user.role == Role.ADMIN


def can_write_any_course(user: UserResponse) -> bool:
    """No one modifies another owner's course; administrators are not exempt."""
    return False


def readable_course_criteria(user: UserResponse) -> tuple[ColumnElement[bool], ...]:
    """Restrict a ``Course`` select to the courses ``user`` may read."""
    criteria: tuple[ColumnElement[bool], ...] = (Course.is_deleted.is_(False),)
    if not can_read_any_course(user):
        criteria += (Course.owner_id == user.id,)
    return criteria


def writable_course_criteria(user: UserResponse) -> tuple[ColumnElement[bool], ...]:
    """Restrict a ``Course`` select to the courses ``user`` may modify."""
    criteria: tuple[ColumnElement[bool], ...] = (Course.is_deleted.is_(False),)
    if not can_write_any_course(user):
        criteria += (Course.owner_id == user.id,)
    return criteria


def deletable_course_criteria(user: UserResponse) -> tuple[ColumnElement[bool], ...]:
    """Restrict a ``Course`` select to the courses ``user`` may delete.

    Unlike the other modes this one deliberately ignores ``is_deleted``. Hard
    deletion tombstones the course before it removes stored files, so a storage
    failure leaves a tombstoned course that its owner must still be able to
    purge. Soft-delete semantics stay in the service layer, which continues to
    reject soft-deleting an already deleted course.
    """
    if can_write_any_course(user):
        return ()
    return (Course.owner_id == user.id,)


def _authorized_course(
    db: Session,
    course_id: int,
    criteria: tuple[ColumnElement[bool], ...],
) -> CourseResponse:
    """Load a course the caller is allowed to use, or deny without disclosure.

    The policy is applied inside the query so unauthorized rows never leave the
    database, and the detached ``CourseResponse`` stays valid across the session
    rollbacks that course and document services perform.
    """
    course = db.scalar(select(Course).where(Course.id == course_id, *criteria))
    if course is None:
        raise NotFoundException(detail=COURSE_NOT_FOUND)
    return CourseResponse.model_validate(course)


def require_course_access(
    course_id: int,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> CourseResponse:
    """Authorize reading a course before the endpoint runs."""
    return _authorized_course(db, course_id, readable_course_criteria(current_user))


def require_course_owner(
    course_id: int,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> CourseResponse:
    """Authorize modifying a course, or its material, before the endpoint runs."""
    return _authorized_course(db, course_id, writable_course_criteria(current_user))


def require_course_deletion(
    course_id: int,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> CourseResponse:
    """Authorize deleting a course, including one already tombstoned."""
    return _authorized_course(db, course_id, deletable_course_criteria(current_user))


AuthorizedCourse = Annotated[CourseResponse, Depends(require_course_access)]
OwnedCourse = Annotated[CourseResponse, Depends(require_course_owner)]
DeletableCourse = Annotated[CourseResponse, Depends(require_course_deletion)]
