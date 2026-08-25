"""Idempotent embedding backfill for chunks that predate vector indexing.

Reconciles the vector store against the current chunks of every ready
document: embeds what is missing, optionally prunes vectors whose chunk is
gone, and leaves everything already correct untouched. Rerunning it is always
safe, and it commits per document so an interrupted run keeps its progress.
"""

import argparse
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.config import settings
from backend.app.database import SessionLocal
from backend.app.models import Course, DocumentChunk, UploadedDocument
from backend.app.observability import configure_logging
from services.embeddings import (
    EmbeddingProvider,
    configured_embedding_identity,
    get_embedding_provider,
)
from services.vector_store import VectorRecord, VectorStore, get_vector_store

logger = logging.getLogger(__name__)
SessionFactory = Callable[[], Session]

DEFAULT_BATCH_SIZE = 64


@dataclass
class BackfillReport:
    documents_examined: int = 0
    documents_updated: int = 0
    vectors_missing: int = 0
    vectors_written: int = 0
    vectors_pruned: int = 0

    def summary(self) -> str:
        return (
            f"examined={self.documents_examined} updated={self.documents_updated} "
            f"missing={self.vectors_missing} written={self.vectors_written} "
            f"pruned={self.vectors_pruned}"
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
    chunks = list(
        session.scalars(
            select(DocumentChunk)
            .where(DocumentChunk.document_id == document.id)
            .order_by(DocumentChunk.chunk_index)
        ).all()
    )
    if not chunks:
        return

    stored_ids = vector_store.chunk_ids_with_vectors(session, document.id)
    current_ids = {chunk.id for chunk in chunks}
    missing = [chunk for chunk in chunks if chunk.id not in stored_ids]
    orphans = stored_ids - current_ids

    report.vectors_missing += len(missing)
    if not missing and not (prune_orphans and orphans):
        return
    if dry_run:
        return

    provider_name, model_name = configured_embedding_identity()
    records: list[VectorRecord] = []
    for start in range(0, len(missing), batch_size):
        batch = missing[start : start + batch_size]
        vectors = embedding_provider.embed_documents([chunk.text for chunk in batch])
        if len(vectors) != len(batch):
            raise RuntimeError(
                f"Embedding provider returned {len(vectors)} vectors for "
                f"{len(batch)} chunks"
            )
        records.extend(
            VectorRecord(
                chunk_id=chunk.id,
                document_id=document.id,
                course_id=document.course_id,
                chunk_index=chunk.chunk_index,
                embedding=vector,
            )
            for chunk, vector in zip(batch, vectors)
        )

    if records:
        vector_store.upsert_document_vectors(
            session,
            document_id=document.id,
            course_id=document.course_id,
            records=records,
            embedding_provider=provider_name,
            embedding_model=model_name,
        )
    if prune_orphans and orphans:
        vector_store.delete_chunk_vectors(session, document.id, orphans)
        report.vectors_pruned += len(orphans)

    report.vectors_written += len(records)
    if records or (prune_orphans and orphans):
        report.documents_updated += 1


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
        with session_factory() as session:
            document = session.get(UploadedDocument, identifier)
            if document is None or document.status != "ready":
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

    logger.info("Embedding backfill finished: %s", report.summary())
    return report


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Backfill embeddings for chunks that have no current vector.",
    )
    parser.add_argument("--course-id", type=int, default=None)
    parser.add_argument("--document-id", type=str, default=None)
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--prune-orphans", action="store_true")
    arguments = parser.parse_args(argv)

    if arguments.batch_size <= 0:
        parser.error("--batch-size must be a positive integer")

    configure_logging(service="maintenance", environment=settings.app_env)
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
