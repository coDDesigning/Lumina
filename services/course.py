from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from backend.app.database import begin_serialized_write
from backend.app.models import Course, UploadedDocument
from schemas.course import CourseCreate, CourseResponse, CourseUpdate
from schemas.user import UserResponse
from services.processing_jobs import fence_course_jobs
from utils.authorization import readable_course_criteria
from utils.exceptions import NotFoundException


class CourseService:
    @staticmethod
    def get_courses_for_user(
        db: Session, current_user: UserResponse
    ) -> list[CourseResponse]:
        """List the courses ``current_user`` may read.

        Ownership is a predicate in the query rather than a filter applied to
        the results, so another owner's rows never leave the database. Single
        course reads go through ``utils.authorization`` instead; there is no
        unscoped course lookup for a caller to reach for by mistake.
        """
        statement = (
            select(Course)
            .where(*readable_course_criteria(current_user))
            .order_by(Course.id)
        )
        return [
            CourseResponse.model_validate(course)
            for course in db.scalars(statement).all()
        ]

    @staticmethod
    def create_course(
        db: Session, course_data: CourseCreate, owner_id: int
    ) -> CourseResponse:
        course = Course(
            **course_data.model_dump(),
            owner_id=owner_id,
            is_deleted=False,
        )
        db.add(course)
        db.flush()
        db.refresh(course)
        course_id = course.id
        try:
            db.commit()
        except SQLAlchemyError as exc:
            db.rollback()
            try:
                with Session(bind=db.get_bind()) as verification_db:
                    persisted = verification_db.get(Course, course_id)
                    if persisted is not None:
                        return CourseResponse.model_validate(persisted)
            except SQLAlchemyError as verification_exc:
                raise exc from verification_exc
            raise
        return CourseResponse.model_validate(course)

    @staticmethod
    def update_course(
        db: Session, course_id: int, update_data: CourseUpdate
    ) -> CourseResponse:
        updates = update_data.model_dump(exclude_unset=True)
        if updates.get("is_deleted") is True:
            db.rollback()
            begin_serialized_write(db)
        statement = select(Course).where(
            Course.id == course_id,
            Course.is_deleted.is_(False),
        )
        if updates.get("is_deleted") is True:
            statement = statement.with_for_update()
        course = db.scalar(statement)
        if course is None:
            raise NotFoundException(detail="Course not found")

        for field, value in updates.items():
            setattr(course, field, value)
        if course.is_deleted:
            fence_course_jobs(db, course_id)

        db.commit()
        db.refresh(course)
        return CourseResponse.model_validate(course)

    @staticmethod
    def soft_delete_course(db: Session, course_id: int) -> CourseResponse:
        """Moves the course to trash (soft delete by setting is_deleted=True)."""
        db.rollback()
        begin_serialized_write(db)
        course = db.scalar(
            select(Course)
            .where(
                Course.id == course_id,
                Course.is_deleted.is_(False),
            )
            .with_for_update()
        )
        if course is None:
            raise NotFoundException(detail="Course not found")

        course.is_deleted = True
        fence_course_jobs(db, course_id)
        db.commit()
        db.refresh(course)
        return CourseResponse.model_validate(course)

    @staticmethod
    def prepare_hard_delete(db: Session, course_id: int) -> list[tuple[str, str]]:
        """Tombstone a course and retain metadata until storage is cleaned."""
        db.rollback()
        begin_serialized_write(db)
        course = db.scalar(
            select(Course).where(Course.id == course_id).with_for_update()
        )
        if course is None:
            raise NotFoundException(detail="Course not found")

        course.is_deleted = True
        fence_course_jobs(db, course_id)
        db.commit()
        stored_documents = list(
            db.execute(
                select(
                    UploadedDocument.storage_provider,
                    UploadedDocument.storage_key,
                ).where(UploadedDocument.course_id == course_id)
            ).all()
        )
        db.rollback()
        return stored_documents

    @staticmethod
    def finalize_hard_delete(db: Session, course_id: int) -> None:
        """Delete a tombstoned course after its stored documents are gone."""
        db.rollback()
        begin_serialized_write(db)
        course = db.scalar(
            select(Course).where(Course.id == course_id).with_for_update()
        )
        if course is None:
            raise NotFoundException(detail="Course not found")
        if not course.is_deleted:
            raise RuntimeError("Course must be tombstoned before hard deletion.")

        db.delete(course)
        try:
            db.commit()
        except SQLAlchemyError as exc:
            try:
                db.rollback()
            except SQLAlchemyError:
                pass
            try:
                with Session(bind=db.get_bind()) as verification_db:
                    deletion_committed = verification_db.get(Course, course_id) is None
            except SQLAlchemyError as verification_exc:
                raise exc from verification_exc
            if not deletion_committed:
                raise
