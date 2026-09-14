"""Durable, idempotent cleanup for accounts pending permanent erasure."""

from dataclasses import dataclass
from datetime import datetime, timezone
from math import ceil
from uuid import UUID

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from backend.app.database import begin_serialized_write
from backend.app.models import (
    Course,
    CreditTransaction,
    DocumentGenerationLock,
    GeneratedOutput,
    GenerationJob,
    ProfileDocument,
    Quiz,
    RateLimitBucket,
    UploadedDocument,
    User,
)
from services.vector_store import VectorStore, VectorStoreError, get_vector_store
from storage.base import Storage, StorageError
from utils.rate_limit import rate_limit_key


class AccountDeletionError(Exception):
    """An account purge could not finish; its tombstone remains retryable."""

    def __init__(self, error_code: str) -> None:
        super().__init__(error_code)
        self.error_code = error_code


@dataclass(frozen=True, slots=True)
class _DocumentResource:
    document_id: UUID
    storage_provider: str
    storage_key: str


@dataclass(frozen=True, slots=True)
class _AccountResources:
    course_ids: tuple[int, ...]
    course_documents: tuple[_DocumentResource, ...]
    profile_documents: tuple[_DocumentResource, ...]

    @property
    def document_ids(self) -> tuple[UUID, ...]:
        return tuple(
            dict.fromkeys(
                [doc.document_id for doc in self.course_documents]
                + [doc.document_id for doc in self.profile_documents]
            )
        )


class AccountDeletionService:
    """Erase an account only after every external resource has been removed."""

    @staticmethod
    def _resources(db: Session, user_id: int) -> _AccountResources:
        course_ids = tuple(
            db.scalars(
                select(Course.id).where(Course.owner_id == user_id).order_by(Course.id)
            ).all()
        )
        course_documents = tuple(
            _DocumentResource(
                document_id=row[0],
                storage_provider=row[1],
                storage_key=row[2],
            )
            for row in db.execute(
                select(
                    UploadedDocument.id,
                    UploadedDocument.storage_provider,
                    UploadedDocument.storage_key,
                )
                .outerjoin(Course, Course.id == UploadedDocument.course_id)
                .where(
                    or_(
                        UploadedDocument.user_id == user_id,
                        Course.owner_id == user_id,
                    )
                )
                .order_by(UploadedDocument.id)
            ).all()
        )
        profile_documents = tuple(
            _DocumentResource(
                document_id=row[0],
                storage_provider=row[1],
                storage_key=row[2],
            )
            for row in db.execute(
                select(
                    ProfileDocument.id,
                    ProfileDocument.storage_provider,
                    ProfileDocument.storage_key,
                )
                .where(ProfileDocument.user_id == user_id)
                .order_by(ProfileDocument.id)
            ).all()
        )
        return _AccountResources(
            course_ids=course_ids,
            course_documents=course_documents,
            profile_documents=profile_documents,
        )

    @staticmethod
    def _prepare(db: Session, user_id: int) -> _AccountResources | None:
        db.rollback()
        begin_serialized_write(db)
        user = db.scalar(
            select(User).where(User.id == user_id).with_for_update(of=User)
        )
        if user is None:
            db.rollback()
            return None
        if user.deletion_requested_at is None:
            db.rollback()
            raise AccountDeletionError("account_not_tombstoned")

        attempted_at = datetime.now(timezone.utc)
        user.deletion_attempt_count += 1
        user.deletion_last_attempt_at = attempted_at
        user.deletion_last_error_code = None
        db.execute(
            update(Course)
            .where(Course.owner_id == user_id)
            .values(is_deleted=True, updated_at=attempted_at)
        )
        db.execute(
            update(UploadedDocument)
            .where(
                or_(
                    UploadedDocument.user_id == user_id,
                    UploadedDocument.course_id.in_(
                        select(Course.id).where(Course.owner_id == user_id)
                    ),
                )
            )
            .values(status="deleting", updated_at=attempted_at)
        )
        db.execute(
            update(ProfileDocument)
            .where(ProfileDocument.user_id == user_id)
            .values(status="deleting", updated_at=attempted_at)
        )
        db.commit()

        resources = AccountDeletionService._resources(db, user_id)
        db.rollback()
        return resources

    @staticmethod
    def _record_failure(db: Session, user_id: int, error_code: str) -> None:
        try:
            db.rollback()
            begin_serialized_write(db)
            db.execute(
                update(User)
                .where(
                    User.id == user_id,
                    User.deletion_requested_at.is_not(None),
                )
                .values(deletion_last_error_code=error_code)
            )
            db.commit()
        except SQLAlchemyError:
            db.rollback()

    @staticmethod
    def _has_active_generation_lock(db: Session, resources: _AccountResources) -> bool:
        if not resources.document_ids:
            return False
        now = datetime.now(timezone.utc)
        return (
            db.scalar(
                select(func.count())
                .select_from(DocumentGenerationLock)
                .where(
                    DocumentGenerationLock.document_id.in_(resources.document_ids),
                    DocumentGenerationLock.expires_at > now,
                )
            )
            or 0
        ) > 0

    @staticmethod
    def _delete_storage(storage: Storage, resources: _AccountResources) -> None:
        for doc in (
            *resources.course_documents,
            *resources.profile_documents,
        ):
            if doc.storage_provider != storage.provider:
                raise AccountDeletionError("account_storage_cleanup_failed")
            try:
                storage.delete(doc.storage_key)
            except (StorageError, ValueError, OSError) as exc:
                raise AccountDeletionError("account_storage_cleanup_failed") from exc

    @staticmethod
    def _set_purge_timeout(db: Session, operation_timeout_seconds: float) -> None:
        if db.get_bind().dialect.name != "postgresql":
            return
        timeout = f"{max(1, ceil(operation_timeout_seconds * 1000))}ms"
        db.scalar(select(func.set_config("lock_timeout", timeout, True)))
        db.scalar(select(func.set_config("statement_timeout", timeout, True)))

    @staticmethod
    def _delete_rate_limit_buckets(db: Session, user_id: int, email: str) -> None:
        db.execute(
            delete(RateLimitBucket).where(
                or_(
                    RateLimitBucket.key == f"generation:user:{user_id}",
                    RateLimitBucket.key == f"client_error:user:{user_id}",
                    RateLimitBucket.key.startswith(
                        f"client_error:fingerprint:{user_id}:"
                    ),
                    RateLimitBucket.key == rate_limit_key("login:account", email),
                )
            )
        )

    @staticmethod
    def purge(
        db: Session,
        user_id: int,
        storage: Storage,
        vector_store: VectorStore | None = None,
        *,
        operation_timeout_seconds: float,
    ) -> bool:
        """Finish one account purge, returning false if another worker did it."""
        try:
            resources = AccountDeletionService._prepare(db, user_id)
        except SQLAlchemyError as exc:
            AccountDeletionService._record_failure(
                db, user_id, "account_database_cleanup_failed"
            )
            raise AccountDeletionError("account_database_cleanup_failed") from exc
        if resources is None:
            return False

        if AccountDeletionService._has_active_generation_lock(db, resources):
            AccountDeletionService._record_failure(
                db, user_id, "account_cleanup_deferred"
            )
            raise AccountDeletionError("account_cleanup_deferred")

        try:
            AccountDeletionService._delete_storage(storage, resources)
        except AccountDeletionError as exc:
            AccountDeletionService._record_failure(db, user_id, exc.error_code)
            raise

        store = vector_store if vector_store is not None else get_vector_store()
        try:
            db.rollback()
            begin_serialized_write(db)
            AccountDeletionService._set_purge_timeout(db, operation_timeout_seconds)
            user = db.scalar(
                select(User)
                .where(
                    User.id == user_id,
                    User.deletion_requested_at.is_not(None),
                )
                .with_for_update(of=User)
            )
            if user is None:
                db.rollback()
                return False

            current = AccountDeletionService._resources(db, user_id)
            for doc in current.course_documents:
                store.delete_document_vectors(db, doc.document_id)
            for course_id in current.course_ids:
                store.delete_course_vectors(db, course_id)
            for doc in current.profile_documents:
                store.delete_profile_document_vectors(db, doc.document_id)
            store.delete_user_profile_vectors(db, user_id)

            if current.document_ids:
                db.execute(
                    delete(DocumentGenerationLock).where(
                        DocumentGenerationLock.document_id.in_(current.document_ids)
                    )
                )
            # These attribution links use SET NULL for ordinary user deletion.
            # Account erasure removes artifacts authored by this account instead.
            db.execute(delete(GenerationJob).where(GenerationJob.user_id == user_id))
            db.execute(
                delete(GeneratedOutput).where(GeneratedOutput.user_id == user_id)
            )
            db.execute(delete(Quiz).where(Quiz.user_id == user_id))

            # Other users' ledgers remain immutable accounting records, but no
            # deleted administrator email remains as their actor label.
            db.execute(
                update(CreditTransaction)
                .where(CreditTransaction.actor_user_id == user_id)
                .values(actor_user_id=None, actor_label="Deleted administrator")
            )
            AccountDeletionService._delete_rate_limit_buckets(db, user_id, user.email)
            result = db.execute(delete(User).where(User.id == user_id))
            if result.rowcount != 1:
                db.rollback()
                return False
            db.commit()
            return True
        except VectorStoreError as exc:
            db.rollback()
            AccountDeletionService._record_failure(
                db, user_id, "account_vector_cleanup_failed"
            )
            raise AccountDeletionError("account_vector_cleanup_failed") from exc
        except SQLAlchemyError as exc:
            db.rollback()
            try:
                with Session(bind=db.get_bind()) as verification_db:
                    if verification_db.get(User, user_id) is None:
                        return True
            except SQLAlchemyError:
                pass
            AccountDeletionService._record_failure(
                db, user_id, "account_database_cleanup_failed"
            )
            raise AccountDeletionError("account_database_cleanup_failed") from exc
