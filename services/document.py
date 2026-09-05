"""Course-scoped document registration orchestration."""

import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID, uuid4

from fastapi import UploadFile
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, OperationalError, SQLAlchemyError
from sqlalchemy.orm import Session, selectinload

from backend.app.config import settings
from backend.app.database import begin_serialized_write
from backend.app.models import (
    JOB_TYPE_EXTRACT_DOCUMENT,
    Course,
    ProcessingJob,
    UploadedDocument,
)
from backend.app.repositories.document import DocumentRepository
from schemas.prompt_context import DocumentMaterialKind
from services.document_hash import calculate_file_hash
from services.document_lock import is_document_locked_for_generation
from services.document_pipeline import extract_raw_document
from services.document_validation import (
    DocumentValidationError,
    validate_basic_upload,
)
from services.processing_jobs import (
    ProcessingJobStateError,
    enqueue_document_job,
    retry_failed_job,
)
from services.vector_store import VectorStore, VectorStoreError, get_vector_store
from storage.base import Storage, StorageError
from utils.exceptions import ConflictException, NotFoundException

logger = logging.getLogger(__name__)
COMMIT_RECONCILIATION_ATTEMPTS = 3
COMMIT_RECONCILIATION_DELAY_SECONDS = 0.05
# Text file types a syllabus may be pasted from. Images are excluded: reading
# one needs OCR, which belongs to the processing pipeline, not a form field.
SYLLABUS_FILE_TYPES = frozenset({"pdf", "txt", "md", "markdown"})
SYLLABUS_MAX_CHARACTERS = 20_000
PAGE_SEPARATOR = "\n\n"


class DocumentRegistrationError(Exception):
    """A document could not be registered safely."""


class CourseDocumentLimitError(Exception):
    """The requested course cannot accept another document."""


class DocumentActiveError(Exception):
    """An active document cannot be deleted."""


class DocumentDeletionInProgressError(Exception):
    """A matching document is already being deleted."""


class DocumentDeletionError(Exception):
    """A document could not be deleted safely."""


@dataclass(frozen=True, slots=True)
class DocumentUploadResult:
    document: UploadedDocument
    duplicate: bool


@dataclass(frozen=True, slots=True)
class SyllabusExtraction:
    text: str
    truncated: bool


class DocumentService:
    """Coordinate validation, deduplication, storage, and persistence."""

    @staticmethod
    def register(
        db: Session,
        storage: Storage,
        upload: UploadFile,
        course_id: int,
        user_id: int,
        material_kind: str = DocumentMaterialKind.UNSPECIFIED.value,
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

        metadata = validate_basic_upload(upload)
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
            if existing.status == "deleting":
                db.rollback()
                raise DocumentDeletionInProgressError
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

        # A tombstoned document is pending erasure and is hidden from every read,
        # so it must not spend the course's document or byte allowance either.
        document_count, stored_bytes = db.execute(
            select(
                func.count(UploadedDocument.id),
                func.coalesce(func.sum(UploadedDocument.file_size), 0),
            ).where(
                UploadedDocument.course_id == course_id,
                UploadedDocument.status != "deleting",
            )
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
                status="uploaded",
                material_kind=DocumentMaterialKind(material_kind).value,
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
    def extract_syllabus_text(upload: UploadFile) -> SyllabusExtraction:
        """Read a syllabus upload into plain text without persisting anything.

        This backs the syllabus field of the course form, so the upload is
        never stored, hashed, or queued: only the text the user is about to
        edit is returned.
        """
        metadata = validate_basic_upload(upload)
        if metadata.file_type not in SYLLABUS_FILE_TYPES:
            raise DocumentValidationError("unsupported_file_type")

        try:
            content = upload.file.read()
        except Exception as exc:
            raise DocumentValidationError("upload_failed") from exc

        extracted = extract_raw_document(metadata.file_type, content)
        text = PAGE_SEPARATOR.join(
            page.text.strip() for page in extracted.contents if page.text.strip()
        ).strip()
        truncated = len(text) > SYLLABUS_MAX_CHARACTERS
        return SyllabusExtraction(
            text=text[:SYLLABUS_MAX_CHARACTERS].strip(),
            truncated=truncated,
        )

    @staticmethod
    def list_course_documents(
        db: Session,
        course_id: int,
    ) -> Sequence[UploadedDocument]:
        """List the documents of an already authorized course, newest first.

        Callers reach this only through the course authorization boundary, so
        the course scope here is the authorized identifier rather than the one
        supplied by the client.
        """
        return db.scalars(
            select(UploadedDocument)
            .options(selectinload(UploadedDocument.pages))
            .where(
                UploadedDocument.course_id == course_id,
                UploadedDocument.status != "deleting",
            )
            .order_by(UploadedDocument.created_at.desc())
        ).all()

    @staticmethod
    def get_document_job(
        db: Session,
        document_id: UUID,
        course_id: int,
    ) -> tuple[UploadedDocument, ProcessingJob]:
        row = db.execute(
            select(UploadedDocument, ProcessingJob)
            .options(selectinload(UploadedDocument.pages))
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
                UploadedDocument.status != "deleting",
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
    def delete_document(
        db: Session,
        storage: Storage,
        document_id: UUID,
        course_id: int,
        vector_store: VectorStore | None = None,
        *,
        force: bool = False,
    ) -> None:
        """Tombstone a terminal document, remove its source, then delete metadata.

        ``force`` lets a caller discard a document whose extraction is in flight
        or stuck: a queued or running job, regardless of worker lease. A document
        a generation is actively reading is still protected.
        """
        try:
            db.rollback()
            begin_serialized_write(db)
            course_statement = select(Course).where(
                Course.id == course_id,
                Course.is_deleted.is_(False),
            )
            if db.get_bind().dialect.name == "postgresql":
                course_statement = course_statement.with_for_update(of=Course)
            if db.scalar(course_statement) is None:
                db.rollback()
                raise NotFoundException("Document not found")

            statement = (
                select(UploadedDocument, ProcessingJob)
                .join(
                    ProcessingJob,
                    (ProcessingJob.document_id == UploadedDocument.id)
                    & (ProcessingJob.course_id == UploadedDocument.course_id),
                )
                .where(
                    UploadedDocument.id == document_id,
                    UploadedDocument.course_id == course_id,
                    ProcessingJob.job_type == JOB_TYPE_EXTRACT_DOCUMENT,
                )
            )
            if db.get_bind().dialect.name == "postgresql":
                statement = statement.with_for_update(
                    of=(ProcessingJob, UploadedDocument)
                )
            row = db.execute(statement).one_or_none()
        except SQLAlchemyError as exc:
            raise DocumentDeletionError from exc

        if row is None:
            db.rollback()
            raise NotFoundException("Document not found")
        document, job = row
        job_is_active = (job.status in {"queued", "running"}) if not force else False
        if job_is_active or is_document_locked_for_generation(document_id):
            db.rollback()
            raise DocumentActiveError
        if document.storage_provider != storage.provider:
            db.rollback()
            raise DocumentDeletionError

        storage_provider = document.storage_provider
        storage_key = document.storage_key
        if document.status != "deleting":
            document.status = "deleting"
            try:
                db.commit()
            except SQLAlchemyError as exc:
                try:
                    db.rollback()
                except SQLAlchemyError:
                    pass
                if not DocumentService._deletion_is_prepared(
                    db,
                    document_id,
                    course_id,
                    storage_provider,
                    storage_key,
                ):
                    raise DocumentDeletionError from exc
        else:
            db.rollback()

        try:
            storage.delete(storage_key)
        except (StorageError, ValueError) as exc:
            raise DocumentDeletionError from exc

        try:
            begin_serialized_write(db)
            course_statement = select(Course).where(Course.id == course_id)
            if db.get_bind().dialect.name == "postgresql":
                course_statement = course_statement.with_for_update(of=Course)
            if db.scalar(course_statement) is None:
                db.rollback()
                return

            document_statement = select(UploadedDocument).where(
                UploadedDocument.id == document_id,
                UploadedDocument.course_id == course_id,
                UploadedDocument.status == "deleting",
            )
            if db.get_bind().dialect.name == "postgresql":
                document_statement = document_statement.with_for_update(
                    of=UploadedDocument
                )
            deleting_document = db.scalar(document_statement)
            if deleting_document is None:
                db.rollback()
                if DocumentService._document_is_absent(db, document_id):
                    return
                raise DocumentDeletionError

            store = vector_store if vector_store is not None else get_vector_store()
            try:
                store.delete_document_vectors(db, document_id)
            except VectorStoreError as exc:
                db.rollback()
                raise DocumentDeletionError from exc

            db.delete(deleting_document)
            db.commit()
        except SQLAlchemyError as exc:
            try:
                db.rollback()
            except SQLAlchemyError:
                pass
            if not DocumentService._document_is_absent(db, document_id):
                raise DocumentDeletionError from exc

    @staticmethod
    def _deletion_is_prepared(
        db: Session,
        document_id: UUID,
        course_id: int,
        storage_provider: str,
        storage_key: str,
    ) -> bool:
        try:
            with Session(bind=db.get_bind()) as verification_db:
                return (
                    verification_db.scalar(
                        select(UploadedDocument.id).where(
                            UploadedDocument.id == document_id,
                            UploadedDocument.course_id == course_id,
                            UploadedDocument.status == "deleting",
                            UploadedDocument.storage_provider == storage_provider,
                            UploadedDocument.storage_key == storage_key,
                        )
                    )
                    is not None
                )
        except SQLAlchemyError:
            return False

    @staticmethod
    def _document_is_absent(db: Session, document_id: UUID) -> bool:
        try:
            with Session(bind=db.get_bind()) as verification_db:
                return verification_db.get(UploadedDocument, document_id) is None
        except SQLAlchemyError:
            return False

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
                if existing.status == "deleting":
                    db.rollback()
                    raise DocumentDeletionInProgressError
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
