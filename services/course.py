from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, selectinload

from backend.app.database import begin_serialized_write
from backend.app.models import Course, CourseTopic, UploadedDocument
from schemas.course import CourseCreate, CourseResponse, CourseUpdate
from schemas.user import UserResponse
from services.processing_jobs import fence_course_jobs
from services.generation_jobs import (
    GenerationJobRefundError,
    cancel_course_generation_jobs,
)
from services.vector_store import VectorStore, VectorStoreError, get_vector_store
from storage.base import Storage, StorageError
from utils.exceptions import NotFoundException


class CourseDeletionError(Exception):
    """Course erasure left work behind; the tombstone remains for a retry."""


class CourseService:
    @staticmethod
    def get_courses_for_user(
        db: Session, current_user: UserResponse
    ) -> list[CourseResponse]:
        """List the courses ``current_user`` owns.

        Ownership is a predicate in the query rather than a filter applied to
        the results, so another owner's rows never leave the database. Course
        listing is strictly owner-scoped for all users, including administrators.
        Single course reads go through ``utils.authorization`` instead; there is
        no unscoped course lookup for a caller to reach for by mistake.
        """
        return CourseService.get_courses_by_user_id(db, current_user.id)

    @staticmethod
    def get_courses_by_user_id(db: Session, user_id: int) -> list[CourseResponse]:
        """List the active courses owned by a specific user.

        Used by standard course listing for the owner, and by administrative
        support workflows for an explicit target user.
        """
        statement = (
            select(Course)
            .where(Course.owner_id == user_id, Course.is_deleted.is_(False))
            .order_by(Course.exam_date.is_(None), Course.exam_date, Course.id)
            .options(selectinload(Course.topic_rows))
        )
        return [
            CourseResponse.model_validate(course)
            for course in db.scalars(statement).all()
        ]

    @staticmethod
    def _replace_topics(db: Session, course: Course, names: list[str]) -> None:
        if course.topic_rows:
            course.topic_rows.clear()
            db.flush()
        course.topic_rows = [
            CourseTopic(position=position, name=name)
            for position, name in enumerate(names)
        ]

    @staticmethod
    def create_course(
        db: Session, course_data: CourseCreate, owner_id: int
    ) -> CourseResponse:
        fields = course_data.model_dump()
        topics = fields.pop("topics")
        course = Course(
            **fields,
            owner_id=owner_id,
            is_deleted=False,
        )
        CourseService._replace_topics(db, course, topics)
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
        course = db.scalar(
            select(Course).where(
                Course.id == course_id,
                Course.is_deleted.is_(False),
            )
        )
        if course is None:
            raise NotFoundException(detail="Course not found")

        topics = updates.pop("topics", None)
        for field, value in updates.items():
            setattr(course, field, value)
        if topics is not None:
            CourseService._replace_topics(db, course, topics)

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
        try:
            cancel_course_generation_jobs(db, course_id)
        except GenerationJobRefundError as exc:
            db.commit()
            raise CourseDeletionError from exc
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
    def finalize_hard_delete(
        db: Session,
        course_id: int,
        vector_store: VectorStore | None = None,
    ) -> None:
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

        store = vector_store if vector_store is not None else get_vector_store()
        try:
            store.delete_course_vectors(db, course_id)
        except VectorStoreError:
            db.rollback()
            raise

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

    @staticmethod
    def hard_delete_course(
        db: Session,
        course_id: int,
        storage: Storage,
        vector_store: VectorStore | None = None,
    ) -> None:
        """Erase a course, its stored files, and its vectors, in a resumable order.

        Every step is idempotent and the tombstone outlives all of them, so a
        failure anywhere leaves a course the owner can delete again rather than
        untracked files or searchable vectors.
        """
        stored_documents = CourseService.prepare_hard_delete(db, course_id)
        try:
            for storage_provider, storage_key in stored_documents:
                if storage_provider != storage.provider:
                    raise StorageError("Stored document uses another provider.")
                storage.delete(storage_key)
        except (StorageError, ValueError) as exc:
            raise CourseDeletionError from exc

        try:
            CourseService.finalize_hard_delete(db, course_id, vector_store)
        except (RuntimeError, VectorStoreError) as exc:
            raise CourseDeletionError from exc
