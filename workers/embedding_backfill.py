"""Idempotent embedding backfill for chunks that predate vector indexing.

Reconciles the vector store against the current chunks of every ready
document: embeds what is missing, optionally prunes vectors whose chunk is
gone, and leaves everything already correct untouched. Rerunning it is always
safe, and it commits per document so an interrupted run keeps its progress.
"""

import argparse
import logging
import signal
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.config import (
    DEFAULT_EMBEDDING_BACKFILL_BATCH_SIZE,
    DEFAULT_EMBEDDING_BACKFILL_INTERVAL_SECONDS,
    settings,
)
from backend.app.database import SessionLocal
from backend.app.models import (
    Course,
    DocumentChunk,
    ProfileDocument,
    ProfileDocumentChunk,
    UploadedDocument,
)
from backend.app.observability import configure_logging, emit_emf_metrics
from backend.app.readiness import ReadinessError, check_readiness
from services.embeddings import (
    EmbeddingProvider,
    configured_embedding_identity,
    get_embedding_provider,
)
from services.vector_store import VectorRecord, VectorStore, get_vector_store
from storage.base import Storage
from storage.dependencies import get_storage

logger = logging.getLogger(__name__)
SessionFactory = Callable[[], Session]

DEFAULT_BATCH_SIZE = DEFAULT_EMBEDDING_BACKFILL_BATCH_SIZE


class StopEvent(Protocol):
    def is_set(self) -> bool: ...

    def wait(self, timeout: float) -> bool: ...


class _SignalStopEvent:
    """Lock-free stop flag written by Python's main-thread signal handler."""

    def __init__(self) -> None:
        self.requested = False

    def is_set(self) -> bool:
        return self.requested

    def wait(self, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while not self.requested:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(0.1, remaining))
        return self.requested


@dataclass
class BackfillReport:
    documents_examined: int = 0
    documents_updated: int = 0
    vectors_missing: int = 0
    vectors_written: int = 0
    vectors_pruned: int = 0
    profile_documents_examined: int = 0
    profile_documents_updated: int = 0

    def summary(self) -> str:
        return (
            f"examined={self.documents_examined} updated={self.documents_updated} "
            f"missing={self.vectors_missing} written={self.vectors_written} "
            f"pruned={self.vectors_pruned} "
            f"profile_examined={self.profile_documents_examined} "
            f"profile_updated={self.profile_documents_updated}"
        )


def _ready_document_ids(
    session: Session,
    *,
    course_id: int | None,
    document_id: UUID | None,
) -> list[UUID]:
    statement = (
        select(UploadedDocument.id)
        .join(Course, Course.id == UploadedDocument.course_id)
        .where(
            UploadedDocument.status == "ready",
            Course.is_deleted.is_(False),
        )
        .order_by(UploadedDocument.created_at, UploadedDocument.id)
    )
    if course_id is not None:
        statement = statement.where(UploadedDocument.course_id == course_id)
    if document_id is not None:
        statement = statement.where(UploadedDocument.id == document_id)
    return list(session.scalars(statement).all())


def _ready_profile_document_ids(
    session: Session,
    *,
    document_id: UUID | None,
) -> list[UUID]:
    statement = (
        select(ProfileDocument.id)
        .where(ProfileDocument.status == "ready")
        .order_by(ProfileDocument.created_at, ProfileDocument.id)
    )
    if document_id is not None:
        statement = statement.where(ProfileDocument.id == document_id)
    return list(session.scalars(statement).all())


def check_backfill_ready(
    *,
    session_factory: SessionFactory = SessionLocal,
    storage: Storage | None = None,
    vector_store: VectorStore | None = None,
    embedding_provider: EmbeddingProvider | None = None,
) -> None:
    if storage is None:
        storage = get_storage()
    if vector_store is None:
        vector_store = get_vector_store()
    if embedding_provider is None:
        embedding_provider = get_embedding_provider()
    with session_factory() as session:
        check_readiness(session, storage)


def _backfill_document(
    session: Session,
    document: UploadedDocument,
    *,
    vector_store: VectorStore,
    embedding_provider: EmbeddingProvider,
    batch_size: int,
    dry_run: bool,
    prune_orphans: bool,
    report: BackfillReport,
) -> None:
    provider_name, model_name = configured_embedding_identity()
    document_id = document.id
    course_id = document.course_id

    chunk_rows = [
        (chunk.id, chunk.chunk_index, chunk.text)
        for chunk in session.scalars(
            select(DocumentChunk)
            .where(DocumentChunk.document_id == document_id)
            .order_by(DocumentChunk.chunk_index)
        ).all()
    ]
    if not chunk_rows:
        return

    stored_ids = vector_store.chunk_ids_with_vectors(
        session, document_id, embedding_model=model_name
    )
    current_ids = {chunk_id for chunk_id, _index, _text in chunk_rows}
    missing = [row for row in chunk_rows if row[0] not in stored_ids]
    orphans = stored_ids - current_ids

    report.vectors_missing += len(missing)
    if not missing and not (prune_orphans and orphans):
        return
    if dry_run:
        return

    # Embedding a batch takes seconds, and the first call loads the local model
    # (hundreds of MB). Holding the read transaction open across that is what
    # trips PostgreSQL's idle-in-transaction timeout, so release it first and
    # let the write below open a fresh one.
    session.commit()

    records: list[VectorRecord] = []
    for start in range(0, len(missing), batch_size):
        batch = missing[start : start + batch_size]
        vectors = embedding_provider.embed_documents(
            [text for _id, _index, text in batch]
        )
        if len(vectors) != len(batch):
            raise RuntimeError(
                f"Embedding provider returned {len(vectors)} vectors for "
                f"{len(batch)} chunks"
            )
        records.extend(
            VectorRecord(
                chunk_id=chunk_id,
                document_id=document_id,
                course_id=course_id,
                chunk_index=chunk_index,
                embedding=vector,
            )
            for (chunk_id, chunk_index, _text), vector in zip(batch, vectors)
        )

    if records:
        vector_store.upsert_document_vectors(
            session,
            document_id=document_id,
            course_id=course_id,
            records=records,
            embedding_provider=provider_name,
            embedding_model=model_name,
        )
    if prune_orphans and orphans:
        vector_store.delete_chunk_vectors(session, document_id, orphans)
        report.vectors_pruned += len(orphans)

    report.vectors_written += len(records)
    if records or (prune_orphans and orphans):
        report.documents_updated += 1


def _backfill_profile_document(
    session: Session,
    document: ProfileDocument,
    *,
    vector_store: VectorStore,
    embedding_provider: EmbeddingProvider,
    batch_size: int,
    dry_run: bool,
    report: BackfillReport,
) -> None:
    """Re-embed a profile document whose vectors are absent or another model's.

    The whole document is replaced rather than patched: profile documents are
    small and rare, so a full replacement is cheaper than a second upsert path
    that would have to stay in step with the course one.
    """
    provider_name, model_name = configured_embedding_identity()
    document_id = document.id
    user_id = document.user_id

    chunk_rows = [
        (chunk.id, chunk.chunk_index, chunk.text)
        for chunk in session.scalars(
            select(ProfileDocumentChunk)
            .where(ProfileDocumentChunk.document_id == document_id)
            .order_by(ProfileDocumentChunk.chunk_index)
        ).all()
    ]
    if not chunk_rows:
        return

    stored_ids = vector_store.profile_chunk_ids_with_vectors(
        session, document_id, embedding_model=model_name
    )
    missing = [
        chunk_id for chunk_id, _index, _text in chunk_rows if chunk_id not in stored_ids
    ]
    report.vectors_missing += len(missing)
    if not missing:
        return
    if dry_run:
        return

    # Release the read transaction before the slow embedding work; see
    # _backfill_document for why.
    session.commit()

    vectors: list[list[float]] = []
    for start in range(0, len(chunk_rows), batch_size):
        batch = chunk_rows[start : start + batch_size]
        produced = embedding_provider.embed_documents(
            [text for _id, _index, text in batch]
        )
        if len(produced) != len(batch):
            raise RuntimeError(
                f"Embedding provider returned {len(produced)} vectors for "
                f"{len(batch)} chunks"
            )
        vectors.extend(produced)

    vector_store.replace_profile_document_vectors(
        session,
        document_id=document_id,
        user_id=user_id,
        records=[
            VectorRecord(
                chunk_id=chunk_id,
                document_id=document_id,
                course_id=user_id,
                chunk_index=chunk_index,
                embedding=vector,
            )
            for (chunk_id, chunk_index, _text), vector in zip(
                chunk_rows, vectors, strict=True
            )
        ],
        embedding_provider=provider_name,
        embedding_model=model_name,
    )
    report.vectors_written += len(vectors)
    report.profile_documents_updated += 1


def run_backfill(
    *,
    session_factory: SessionFactory = SessionLocal,
    vector_store: VectorStore | None = None,
    embedding_provider: EmbeddingProvider | None = None,
    course_id: int | None = None,
    document_id: UUID | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    dry_run: bool = False,
    prune_orphans: bool = False,
    stop_event: StopEvent | None = None,
) -> BackfillReport:
    if vector_store is None:
        vector_store = get_vector_store()
    if embedding_provider is None:
        embedding_provider = get_embedding_provider()

    report = BackfillReport()
    with session_factory() as session:
        document_ids = _ready_document_ids(
            session,
            course_id=course_id,
            document_id=document_id,
        )

    for identifier in document_ids:
        if stop_event is not None and stop_event.is_set():
            break
        with session_factory() as session:
            document = session.get(UploadedDocument, identifier)
            if document is None or document.status != "ready":
                continue
            course = session.get(Course, document.course_id)
            if course is None or course.is_deleted:
                continue
            report.documents_examined += 1
            _backfill_document(
                session,
                document,
                vector_store=vector_store,
                embedding_provider=embedding_provider,
                batch_size=batch_size,
                dry_run=dry_run,
                prune_orphans=prune_orphans,
                report=report,
            )
            if dry_run:
                session.rollback()
            else:
                session.commit()

    # A course filter has no profile meaning, so profile documents are only
    # reconciled on an unscoped run.
    if course_id is None:
        with session_factory() as session:
            profile_ids = _ready_profile_document_ids(session, document_id=document_id)

        for identifier in profile_ids:
            if stop_event is not None and stop_event.is_set():
                break
            with session_factory() as session:
                profile_document = session.get(ProfileDocument, identifier)
                if profile_document is None or profile_document.status != "ready":
                    continue
                report.profile_documents_examined += 1
                _backfill_profile_document(
                    session,
                    profile_document,
                    vector_store=vector_store,
                    embedding_provider=embedding_provider,
                    batch_size=batch_size,
                    dry_run=dry_run,
                    report=report,
                )
                if dry_run:
                    session.rollback()
                else:
                    session.commit()

    emit_emf_metrics(
        {
            "BackfillDocumentsExamined": report.documents_examined,
            "BackfillDocumentsUpdated": report.documents_updated,
            "BackfillVectorsMissing": report.vectors_missing,
            "BackfillVectorsWritten": report.vectors_written,
            "BackfillVectorsPruned": report.vectors_pruned,
        },
        dimensions={"Service": "embedding_backfill", "Environment": settings.app_env},
    )
    logger.info("Embedding backfill finished: %s", report.summary())
    return report


def run_backfill_worker(
    *,
    interval_seconds: float = DEFAULT_EMBEDDING_BACKFILL_INTERVAL_SECONDS,
    once: bool = False,
    stop_event: StopEvent | None = None,
    session_factory: SessionFactory = SessionLocal,
    storage: Storage | None = None,
    vector_store: VectorStore | None = None,
    embedding_provider: EmbeddingProvider | None = None,
    course_id: int | None = None,
    document_id: UUID | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    dry_run: bool = False,
    prune_orphans: bool = False,
) -> None:
    stop = stop_event or threading.Event()
    if stop.is_set():
        return
    if storage is None:
        storage = get_storage()
    if vector_store is None:
        vector_store = get_vector_store()
    if embedding_provider is None:
        embedding_provider = get_embedding_provider()

    check_backfill_ready(
        session_factory=session_factory,
        storage=storage,
        vector_store=vector_store,
        embedding_provider=embedding_provider,
    )
    if stop.is_set():
        return

    logger.info(
        "Embedding backfill worker started (interval=%.1fs, batch_size=%d, prune_orphans=%s)",
        interval_seconds,
        batch_size,
        prune_orphans,
    )
    try:
        while not stop.is_set():
            try:
                run_backfill(
                    session_factory=session_factory,
                    vector_store=vector_store,
                    embedding_provider=embedding_provider,
                    course_id=course_id,
                    document_id=document_id,
                    batch_size=batch_size,
                    dry_run=dry_run,
                    prune_orphans=prune_orphans,
                    stop_event=stop,
                )
            except Exception:
                logger.exception("Embedding backfill execution failed")
            if once or stop.is_set():
                break
            stop.wait(interval_seconds)
    finally:
        logger.info("Embedding backfill worker stopped")


def _install_shutdown_handlers(stop_event: _SignalStopEvent) -> None:
    def request_shutdown(_signum: int, _frame: object) -> None:
        stop_event.requested = True

    signal.signal(signal.SIGTERM, request_shutdown)
    signal.signal(signal.SIGINT, request_shutdown)


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Backfill embeddings for chunks that have no current vector.",
    )
    parser.add_argument("--course-id", type=int, default=None)
    parser.add_argument("--document-id", type=str, default=None)
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--prune-orphans", action="store_true")
    parser.add_argument(
        "--interval-seconds",
        type=float,
        default=None,
        help="Run continuously with given sleep interval in seconds.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run at most one backfill cycle and exit.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Check dependencies and readiness without running backfill.",
    )
    arguments = parser.parse_args(argv)

    if arguments.batch_size <= 0:
        parser.error("--batch-size must be a positive integer")

    configure_logging(service="maintenance", environment=settings.app_env)

    if arguments.check:
        try:
            check_backfill_ready()
        except ReadinessError as exc:
            logger.error("Embedding backfill readiness check failed: %s", exc)
            raise SystemExit(1) from None
        logger.info("Embedding backfill readiness check succeeded")
        return

    if arguments.interval_seconds is not None:
        if arguments.interval_seconds < 0:
            parser.error("--interval-seconds must be a non-negative number")
        stop_event = _SignalStopEvent()
        _install_shutdown_handlers(stop_event)
        try:
            run_backfill_worker(
                interval_seconds=arguments.interval_seconds,
                once=arguments.once,
                stop_event=stop_event,
                course_id=arguments.course_id,
                document_id=UUID(arguments.document_id)
                if arguments.document_id
                else None,
                batch_size=arguments.batch_size,
                dry_run=arguments.dry_run,
                prune_orphans=arguments.prune_orphans,
            )
        except ReadinessError as exc:
            logger.error("Embedding backfill readiness check failed: %s", exc)
            raise SystemExit(1) from None
        return

    report = run_backfill(
        course_id=arguments.course_id,
        document_id=UUID(arguments.document_id) if arguments.document_id else None,
        batch_size=arguments.batch_size,
        dry_run=arguments.dry_run,
        prune_orphans=arguments.prune_orphans,
    )
    print(report.summary())


if __name__ == "__main__":
    main()
