"""User-scoped profile document registration, listing, retry, and deletion orchestration."""

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID, uuid4

from fastapi import UploadFile
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from backend.app.database import begin_serialized_write
from backend.app.models import (
    JOB_TYPE_EXTRACT_DOCUMENT,
    ProfileDocument,
    ProfileProcessingJob,
    User,
)
from services.document_hash import calculate_file_hash
from services.document_validation import validate_basic_upload
from services.processing_jobs import (
    ProcessingJobStateError,
    enqueue_profile_document_job,
    retry_failed_profile_job,
)
from services.vector_store import VectorStore, VectorStoreError, get_vector_store
from storage.base import Storage, StorageError, generate_profile_portable_key
from utils.exceptions import ConflictException, NotFoundException

logger = logging.getLogger(__name__)


class ProfileDocumentRegistrationError(Exception):
    """A profile document could not be registered safely."""


class ProfileDocumentDeletionError(Exception):
    """A profile document could not be deleted safely."""


@dataclass(frozen=True, slots=True)
class ProfileDocumentUploadResult:
    document: ProfileDocument
    duplicate: bool


class ProfileDocumentService:
    """Coordinate validation, deduplication, storage, and persistence for profile documents."""

    @staticmethod
    def register(
        db: Session,
        storage: Storage,
        upload: UploadFile,
        user_id: int,
    ) -> ProfileDocumentUploadResult:
        try:
            user_exists = db.scalar(select(User.id).where(User.id == user_id))
        except SQLAlchemyError as exc:
            raise ProfileDocumentRegistrationError from exc

        if user_exists is None:
            raise NotFoundException("User not found")

        try:
            db.rollback()
        except SQLAlchemyError as exc:
            raise ProfileDocumentRegistrationError from exc

        metadata = validate_basic_upload(upload)
        file_hash = calculate_file_hash(upload)

        try:
            begin_serialized_write(db)
            existing = db.scalar(
                select(ProfileDocument).where(
                    ProfileDocument.user_id == user_id,
                    ProfileDocument.file_hash == file_hash,
                )
            )
        except SQLAlchemyError as exc:
            raise ProfileDocumentRegistrationError from exc

        if existing is not None:
            if existing.status == "deleting":
                db.rollback()
                raise ConflictException(
                    "A matching profile document is currently being deleted."
                )
            try:
                file_exists = (
                    existing.storage_provider != storage.provider
                    or storage.exists(existing.storage_key)
                )
            except StorageError as exc:
                raise ProfileDocumentRegistrationError from exc

            if file_exists:
                db.expunge(existing)
                try:
                    db.rollback()
                except SQLAlchemyError as exc:
                    raise ProfileDocumentRegistrationError from exc
                return ProfileDocumentUploadResult(document=existing, duplicate=True)

            try:
                storage.save(existing.storage_key, upload.file)
            except StorageError as exc:
                raise ProfileDocumentRegistrationError from exc
            db.expunge(existing)
            db.rollback()
            return ProfileDocumentUploadResult(document=existing, duplicate=True)

        try:
            db.rollback()
        except SQLAlchemyError as exc:
            raise ProfileDocumentRegistrationError from exc

        document_id = uuid4()
        storage_key = generate_profile_portable_key(
            user_id,
            document_id,
            metadata.file_type,
        )

        try:
            storage.save(storage_key, upload.file)
        except StorageError as exc:
            raise ProfileDocumentRegistrationError from exc

        try:
            begin_serialized_write(db)
            user_exists = db.scalar(
                select(User.id).where(User.id == user_id).with_for_update()
            )
        except SQLAlchemyError as exc:
            ProfileDocumentService._rollback_and_remove(db, storage, storage_key)
            raise ProfileDocumentRegistrationError from exc

        if user_exists is None:
            ProfileDocumentService._rollback_and_remove(db, storage, storage_key)
            raise NotFoundException("User not found")

        document = ProfileDocument(
            id=document_id,
            original_file_name=metadata.original_file_name,
            file_type=metadata.file_type,
            mime_type=metadata.mime_type,
            file_size=metadata.file_size,
            file_hash=file_hash,
            user_id=user_id,
            storage_provider=storage.provider,
            storage_key=storage_key,
            status="uploaded",
        )

        try:
            db.add(document)
            enqueue_profile_document_job(db, document)
            db.commit()
            db.refresh(document)
            return ProfileDocumentUploadResult(document=document, duplicate=False)
        except IntegrityError:
            ProfileDocumentService._rollback_and_remove(db, storage, storage_key)
            # Check if raced duplicate
            existing = db.scalar(
                select(ProfileDocument).where(
                    ProfileDocument.user_id == user_id,
                    ProfileDocument.file_hash == file_hash,
                )
            )
            if existing is not None:
                return ProfileDocumentUploadResult(document=existing, duplicate=True)
            raise ConflictException(
                "A profile document with identical content already exists."
            )
        except Exception as exc:
            ProfileDocumentService._rollback_and_remove(db, storage, storage_key)
            raise ProfileDocumentRegistrationError from exc

    @staticmethod
    def _rollback_and_remove(db: Session, storage: Storage, storage_key: str) -> None:
        try:
            db.rollback()
        except SQLAlchemyError:
            pass
        try:
            storage.delete(storage_key)
        except Exception:
            logger.warning(
                "Could not delete orphan storage key %s after registration error",
                storage_key,
            )

    @staticmethod
    def list_user_documents(db: Session, user_id: int) -> Sequence[ProfileDocument]:
        return db.scalars(
            select(ProfileDocument)
            .where(
                ProfileDocument.user_id == user_id,
                ProfileDocument.status != "deleting",
            )
            .order_by(ProfileDocument.created_at.desc(), ProfileDocument.id.desc())
        ).all()

    @staticmethod
    def get_user_document(
        db: Session, user_id: int, document_id: UUID
    ) -> ProfileDocument | None:
        return db.scalar(
            select(ProfileDocument).where(
                ProfileDocument.id == document_id,
                ProfileDocument.user_id == user_id,
                ProfileDocument.status != "deleting",
            )
        )

    @staticmethod
    def get_document_job(
        db: Session, user_id: int, document_id: UUID
    ) -> ProfileProcessingJob | None:
        return db.scalar(
            select(ProfileProcessingJob).where(
                ProfileProcessingJob.document_id == document_id,
                ProfileProcessingJob.user_id == user_id,
                ProfileProcessingJob.job_type == JOB_TYPE_EXTRACT_DOCUMENT,
            )
        )

    @staticmethod
    def retry_document(
        db: Session, user_id: int, document_id: UUID
    ) -> tuple[ProfileDocument, ProfileProcessingJob]:
        try:
            return retry_failed_profile_job(db, document_id, user_id)
        except ProcessingJobStateError as exc:
            raise ConflictException(str(exc)) from exc

    @staticmethod
    def delete_document(
        db: Session,
        storage: Storage,
        user_id: int,
        document_id: UUID,
        vector_store: VectorStore | None = None,
    ) -> None:
        try:
            begin_serialized_write(db)
            document = db.scalar(
                select(ProfileDocument)
                .where(
                    ProfileDocument.id == document_id,
                    ProfileDocument.user_id == user_id,
                )
                .with_for_update()
            )
        except SQLAlchemyError as exc:
            raise ProfileDocumentDeletionError from exc

        if document is None:
            try:
                db.rollback()
            except SQLAlchemyError:
                pass
            raise NotFoundException("Profile document not found")

        if document.status == "deleting":
            try:
                db.rollback()
            except SQLAlchemyError:
                pass
            raise ConflictException("Document is already being deleted")

        storage_key = document.storage_key
        document.status = "deleting"
        try:
            db.flush()
        except SQLAlchemyError as exc:
            try:
                db.rollback()
            except SQLAlchemyError:
                pass
            raise ProfileDocumentDeletionError from exc

        # Clean vectors
        store = vector_store if vector_store is not None else get_vector_store()
        try:
            store.delete_profile_document_vectors(db, document_id)
        except VectorStoreError as exc:
            logger.warning(
                "Could not delete vectors for profile document %s: %s", document_id, exc
            )

        # Clean storage
        try:
            storage.delete(storage_key)
        except Exception as exc:
            logger.warning("Could not delete storage key %s: %s", storage_key, exc)

        # Delete database row (cascades chunks, pages, visuals, jobs, embeddings)
        try:
            db.delete(document)
            db.commit()
        except SQLAlchemyError as exc:
            try:
                db.rollback()
            except SQLAlchemyError:
                pass
            raise ProfileDocumentDeletionError from exc
