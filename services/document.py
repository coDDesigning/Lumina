"""Course-scoped document registration orchestration."""

import logging
import time
from dataclasses import dataclass
from uuid import UUID, uuid4

from fastapi import UploadFile
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, OperationalError, SQLAlchemyError
from sqlalchemy.orm import Session

from backend.app.config import settings
from backend.app.database import begin_serialized_write
from backend.app.models import (
    JOB_TYPE_EXTRACT_DOCUMENT,
    Course,
    ProcessingJob,
    UploadedDocument,
)
from backend.app.repositories.document import DocumentRepository
from services.document_hash import calculate_file_hash
from services.document_validation import validate_document
from services.processing_jobs import (
    ProcessingJobStateError,
    enqueue_document_job,
    retry_failed_job,
)
from storage.base import Storage, StorageError
from utils.exceptions import ConflictException, NotFoundException

logger = logging.getLogger(__name__)
COMMIT_RECONCILIATION_ATTEMPTS = 3
COMMIT_RECONCILIATION_DELAY_SECONDS = 0.05


class DocumentRegistrationError(Exception):
    """A document could not be registered safely."""


class CourseDocumentLimitError(Exception):
    """The requested course cannot accept another document."""


@dataclass(frozen=True, slots=True)
class DocumentUploadResult:
    document: UploadedDocument
    duplicate: bool


class DocumentService:
    """Coordinate validation, deduplication, storage, and persistence."""

    @staticmethod
    def register(
        db: Session,
        storage: Storage,
        upload: UploadFile,
        course_id: int,
        user_id: int,
    ) -> DocumentUploadResult:
        try:
            course_exists = db.scalar(
                select(Course.id).where(
                    Course.id == course_id,
                    Course.is_deleted.is_(False),
                )
            )
        except SQLAlchemyError as exc:
            raise DocumentRegistrationError from exc

        if course_exists is None:
            raise NotFoundException("Course not found")

        # Authentication and course checks are complete; release the connection
        # before CPU- and filesystem-heavy validation work begins.
        try:
            db.rollback()
        except SQLAlchemyError as exc:
            raise DocumentRegistrationError from exc

        metadata = validate_document(upload)
        file_hash = calculate_file_hash(upload)

        try:
            begin_serialized_write(db)
            active_course = db.scalar(
                select(Course.id)
                .where(
                    Course.id == course_id,
                    Course.is_deleted.is_(False),
                )
                .with_for_update()
            )
            if active_course is None:
                db.rollback()
                raise NotFoundException("Course not found")
            existing = DocumentRepository.get_by_course_and_hash(
                db, course_id, file_hash
            )
        except SQLAlchemyError as exc:
            raise DocumentRegistrationError from exc

        if existing is not None:
            try:
                file_exists = (
                    existing.storage_provider != storage.provider
                    or storage.exists(existing.storage_key)
                )
            except StorageError as exc:
                raise DocumentRegistrationError from exc

            if file_exists:
                db.expunge(existing)
                try:
                    db.rollback()
                except SQLAlchemyError as exc:
                    raise DocumentRegistrationError from exc
                return DocumentUploadResult(document=existing, duplicate=True)

            try:
                storage.save(existing.storage_key, upload.file)
            except StorageError as exc:
                raise DocumentRegistrationError from exc
            db.expunge(existing)
            db.rollback()
            return DocumentUploadResult(document=existing, duplicate=True)

        else:
            try:
                db.rollback()
            except SQLAlchemyError as exc:
                raise DocumentRegistrationError from exc

        document_id = uuid4()
        storage_key = storage.generate_key(
            course_id,
            document_id,
            metadata.file_type,
        )

        try:
            storage.save(storage_key, upload.file)
        except StorageError as exc:
            raise DocumentRegistrationError from exc

        try:
            begin_serialized_write(db)
            active_course = db.scalar(
                select(Course.id)
                .where(
                    Course.id == course_id,
                    Course.is_deleted.is_(False),
                )
                .with_for_update()
            )
        except SQLAlchemyError as exc:
            DocumentService._rollback_and_remove(db, storage, storage_key)
            raise DocumentRegistrationError from exc

        if active_course is None:
            DocumentService._rollback_and_remove(db, storage, storage_key)
            raise NotFoundException("Course not found")

        document_count, stored_bytes = db.execute(
            select(
                func.count(UploadedDocument.id),
                func.coalesce(func.sum(UploadedDocument.file_size), 0),
            ).where(UploadedDocument.course_id == course_id)
        ).one()
        if (
            document_count >= settings.max_documents_per_course
            or stored_bytes + metadata.file_size > settings.max_course_storage_bytes
        ):
            DocumentService._rollback_and_remove(db, storage, storage_key)
            raise CourseDocumentLimitError

        try:
            document = DocumentRepository.create(
                db,
                id=document_id,
                original_file_name=metadata.original_file_name,
                file_type=metadata.file_type,
                mime_type=metadata.mime_type,
                file_size=metadata.file_size,
                file_hash=file_hash,
                user_id=user_id,
                course_id=course_id,
                storage_provider=storage.provider,
                storage_key=storage_key,
                status="pending",
            )
            enqueue_document_job(db, document)
            db.refresh(document)
        except (IntegrityError, OperationalError) as exc:
            DocumentService._rollback_and_remove(db, storage, storage_key)
            existing = DocumentService._wait_for_duplicate(
                db,
                course_id,
                file_hash,
            )
            if existing is None:
                raise DocumentRegistrationError from exc
            return DocumentUploadResult(document=existing, duplicate=True)
        except Exception as exc:
            DocumentService._rollback_and_remove(db, storage, storage_key)
            raise DocumentRegistrationError from exc

        try:
            db.commit()
        except SQLAlchemyError as exc:
            return DocumentService._recover_uncertain_commit(
                db,
                storage,
                storage_key,
                course_id,
                file_hash,
                exc,
            )
        return DocumentUploadResult(document=document, duplicate=False)

    @staticmethod
    def get_document_job(
        db: Session,
        document_id: UUID,
        course_id: int,
    ) -> tuple[UploadedDocument, ProcessingJob]:
        row = db.execute(
            select(UploadedDocument, ProcessingJob)
            .join(
                ProcessingJob,
                (ProcessingJob.document_id == UploadedDocument.id)
                & (ProcessingJob.course_id == UploadedDocument.course_id),
            )
            .join(Course, Course.id == UploadedDocument.course_id)
            .where(
                UploadedDocument.id == document_id,
                UploadedDocument.course_id == course_id,
                ProcessingJob.job_type == JOB_TYPE_EXTRACT_DOCUMENT,
                Course.is_deleted.is_(False),
            )
        ).one_or_none()
        if row is None:
            raise NotFoundException("Document not found")
        return row

    @staticmethod
    def retry_document(
        db: Session,
        document_id: UUID,
        course_id: int,
    ) -> tuple[UploadedDocument, ProcessingJob]:
        try:
            result = retry_failed_job(db, document_id, course_id)
        except ProcessingJobStateError as exc:
            raise ConflictException(str(exc)) from exc
        if result is None:
            raise NotFoundException("Document not found")
        return result

    @staticmethod
    def _wait_for_duplicate(
        db: Session,
        course_id: int,
        file_hash: str,
    ) -> UploadedDocument | None:
        for attempt in range(COMMIT_RECONCILIATION_ATTEMPTS):
            try:
                existing = DocumentRepository.get_by_course_and_hash(
                    db,
                    course_id,
                    file_hash,
                )
            except SQLAlchemyError as exc:
                raise DocumentRegistrationError from exc
            if existing is not None:
                db.refresh(existing)
                db.expunge(existing)
                db.rollback()
                return existing
            db.rollback()
            if attempt + 1 < COMMIT_RECONCILIATION_ATTEMPTS:
                time.sleep(COMMIT_RECONCILIATION_DELAY_SECONDS)
        return None

    @staticmethod
    def _rollback_and_remove(
        db: Session,
        storage: Storage,
        storage_key: str,
    ) -> None:
        try:
            db.rollback()
        except Exception as exc:
            logger.exception("Failed to roll back document transaction")
            DocumentService._remove_failed_upload(storage, storage_key)
            raise DocumentRegistrationError from exc
        DocumentService._remove_failed_upload(storage, storage_key)

    @staticmethod
    def _recover_uncertain_commit(
        db: Session,
        storage: Storage,
        storage_key: str,
        course_id: int,
        file_hash: str,
        commit_error: Exception,
    ) -> DocumentUploadResult:
        """Reconcile storage after a commit whose outcome is unknown."""
        try:
            db.rollback()
        except Exception:
            logger.exception("Failed to reset session after document commit error")

        try:
            persisted = None
            for attempt in range(COMMIT_RECONCILIATION_ATTEMPTS):
                with Session(
                    bind=db.get_bind(), expire_on_commit=False
                ) as verification_db:
                    persisted = DocumentRepository.get_by_course_and_hash(
                        verification_db,
                        course_id,
                        file_hash,
                    )
                    if persisted is not None:
                        verification_db.expunge(persisted)
                        break
                if attempt + 1 < COMMIT_RECONCILIATION_ATTEMPTS:
                    time.sleep(COMMIT_RECONCILIATION_DELAY_SECONDS)
        except Exception:
            # Preserve storage when the database cannot tell us whether it committed.
            logger.exception("Could not determine document commit outcome")
            raise DocumentRegistrationError from commit_error

        if persisted is None:
            logger.error(
                "Document commit outcome remains unknown; preserving stored content"
            )
            raise DocumentRegistrationError from commit_error

        if persisted.storage_key != storage_key:
            DocumentService._remove_failed_upload(storage, storage_key)
            return DocumentUploadResult(document=persisted, duplicate=True)

        logger.warning("Recovered document after an uncertain commit acknowledgement")
        return DocumentUploadResult(document=persisted, duplicate=False)

    @staticmethod
    def _remove_failed_upload(storage: Storage, storage_key: str) -> None:
        try:
            storage.delete(storage_key)
        except StorageError as exc:
            logger.exception("Failed to clean up unregistered document")
            raise DocumentRegistrationError from exc
