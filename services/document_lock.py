"""Durable generation locks that hold across process and container boundaries.

A document must not be erased while a generation is still reading it. The
generation runs in the worker process and the delete that would erase it runs in
the API process, so a lock kept in module state is invisible to the only checker
there is: the invariant silently disappears for every queued generation. The
hold is therefore a database row, written and committed by the reader and read
back by the deleter inside the same transaction that tombstones the document.

Holds are shared. Several generations may read one document at once, so each
hold is its own row and the document is locked while any unexpired row names it.
Each hold carries a lease, because a worker killed mid-generation cannot release
its own row and must not block that document's deletion forever.
"""

import logging
import os
import socket
from collections.abc import Generator, Iterable
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from sqlalchemy import delete, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from backend.app.config import settings
from backend.app.database import begin_serialized_write
from backend.app.models import (
    DocumentGenerationLock,
    ProfileDocument,
    UploadedDocument,
)

logger = logging.getLogger(__name__)

# A generation attempt cannot outlive its own job timeout, so a lease of that
# plus a margin covers the longest legitimate hold. Anything still holding after
# that is a crashed process, not a reader.
GENERATION_LOCK_LEASE_MARGIN_SECONDS = 300


class GenerationLockError(Exception):
    """A generation lock could not be recorded durably."""


def generation_lock_lease_seconds() -> float:
    return float(
        settings.generation_job_attempt_timeout_seconds
        + GENERATION_LOCK_LEASE_MARGIN_SECONDS
    )


def _holder_identity() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"[:255]


def _tombstoned_ids(session: Session, document_ids: list[UUID]) -> set[UUID]:
    """Return the requested ids whose document is already pending erasure.

    A lock taken after the tombstone was committed would block a deletion that
    is already under way, so such a hold is dropped before it becomes visible.

    On PostgreSQL the document rows are read ``FOR SHARE``, which is what orders
    this hold against a delete: the deleter holds those same rows ``FOR UPDATE``
    from before it looks for locks until it commits its tombstone, so either it
    sees this hold or this hold sees its tombstone. SQLite needs no equivalent,
    because both transactions contend for the one database write lock.
    """
    if session.get_bind().dialect.name == "postgresql":
        documents = [
            *session.scalars(
                select(UploadedDocument)
                .where(UploadedDocument.id.in_(document_ids))
                .with_for_update(read=True, of=UploadedDocument)
            ),
            *session.scalars(
                select(ProfileDocument)
                .where(ProfileDocument.id.in_(document_ids))
                .with_for_update(read=True, of=ProfileDocument)
            ),
        ]
        return {document.id for document in documents if document.status == "deleting"}

    tombstoned = set(
        session.scalars(
            select(UploadedDocument.id).where(
                UploadedDocument.id.in_(document_ids),
                UploadedDocument.status == "deleting",
            )
        ).all()
    )
    tombstoned.update(
        session.scalars(
            select(ProfileDocument.id).where(
                ProfileDocument.id.in_(document_ids),
                ProfileDocument.status == "deleting",
            )
        ).all()
    )
    return tombstoned


def _acquire(
    db: Session,
    document_ids: list[UUID],
    holder_token: UUID,
) -> None:
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(seconds=generation_lock_lease_seconds())
    holder = _holder_identity()
    # A separate session, because these rows must be visible to another process
    # immediately and the caller's own transaction is not ours to commit.
    with Session(bind=db.get_bind()) as session:
        begin_serialized_write(session)
        session.execute(
            delete(DocumentGenerationLock).where(
                DocumentGenerationLock.expires_at <= now
            )
        )
        session.add_all(
            [
                DocumentGenerationLock(
                    document_id=document_id,
                    holder_token=holder_token,
                    holder=holder,
                    acquired_at=now,
                    expires_at=expires_at,
                )
                for document_id in document_ids
            ]
        )
        session.flush()
        tombstoned = _tombstoned_ids(session, document_ids)
        if tombstoned:
            session.execute(
                delete(DocumentGenerationLock).where(
                    DocumentGenerationLock.holder_token == holder_token,
                    DocumentGenerationLock.document_id.in_(tombstoned),
                )
            )
        session.commit()


def _release(db: Session, holder_token: UUID) -> None:
    with Session(bind=db.get_bind()) as session:
        begin_serialized_write(session)
        session.execute(
            delete(DocumentGenerationLock).where(
                DocumentGenerationLock.holder_token == holder_token
            )
        )
        session.commit()


@contextmanager
def acquire_generation_locks(
    db: Session,
    document_ids: Iterable[UUID],
) -> Generator[None, None, None]:
    """Hold every named document against deletion for the duration of the block."""
    unique_ids = sorted(set(document_ids))
    if not unique_ids:
        yield
        return

    holder_token = uuid4()
    try:
        _acquire(db, unique_ids, holder_token)
    except SQLAlchemyError as exc:
        # Reading material without a recorded hold is the very failure this
        # module exists to prevent, so the generation stops instead.
        raise GenerationLockError(
            "Could not record generation locks for the requested documents"
        ) from exc

    try:
        yield
    finally:
        try:
            _release(db, holder_token)
        except SQLAlchemyError:
            # The lease bounds the damage: the hold expires on its own.
            logger.exception(
                "Could not release generation locks for holder %s", holder_token
            )


def is_document_locked_for_generation(db: Session, document_id: UUID) -> bool:
    """Report whether any live generation, in any process, is reading the document."""
    now = datetime.now(timezone.utc)
    return (
        db.scalar(
            select(func.count())
            .select_from(DocumentGenerationLock)
            .where(
                DocumentGenerationLock.document_id == document_id,
                DocumentGenerationLock.expires_at > now,
            )
        )
        or 0
    ) > 0


def release_expired_generation_locks(db: Session) -> int:
    """Delete the holds whose lease ran out; returns how many were removed."""
    now = datetime.now(timezone.utc)
    with Session(bind=db.get_bind()) as session:
        begin_serialized_write(session)
        result = session.execute(
            delete(DocumentGenerationLock).where(
                DocumentGenerationLock.expires_at <= now
            )
        )
        session.commit()
        return int(result.rowcount or 0)


def reset_generation_locks(db: Session) -> None:
    """Drop every generation lock (primarily for tests)."""
    with Session(bind=db.get_bind()) as session:
        begin_serialized_write(session)
        session.execute(delete(DocumentGenerationLock))
        session.commit()
