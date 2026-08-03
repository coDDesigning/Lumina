"""Persistence operations for uploaded documents."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models import UploadedDocument

DOCUMENT_STATUSES = frozenset({"pending", "processing", "completed", "failed"})


class DocumentRepository:
    """Keep document queries separate from upload orchestration."""

    @staticmethod
    def get_by_id(db: Session, document_id: UUID) -> UploadedDocument | None:
        return db.get(UploadedDocument, document_id)

    @staticmethod
    def get_by_course_and_hash(
        db: Session,
        course_id: int,
        file_hash: str,
    ) -> UploadedDocument | None:
        return db.scalar(
            select(UploadedDocument).where(
                UploadedDocument.course_id == course_id,
                UploadedDocument.file_hash == file_hash,
            )
        )

    @staticmethod
    def create(db: Session, **values: object) -> UploadedDocument:
        document = UploadedDocument(**values)
        db.add(document)
        db.flush()
        return document

    @staticmethod
    def update_status(
        db: Session,
        document_id: UUID,
        status: str,
        processing_error: str | None = None,
    ) -> UploadedDocument | None:
        if status not in DOCUMENT_STATUSES:
            raise ValueError(f"Unsupported document status: {status}")

        document = DocumentRepository.get_by_id(db, document_id)
        if document is None:
            return None

        document.status = status
        document.processing_error = processing_error
        db.flush()
        return document

    @staticmethod
    def delete(db: Session, document_id: UUID) -> bool:
        document = DocumentRepository.get_by_id(db, document_id)
        if document is None:
            return False

        db.delete(document)
        db.flush()
        return True
