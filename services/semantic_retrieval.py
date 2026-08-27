"""Course-isolated semantic retrieval over the configured vector store.

Callers never learn which backend answered: the query is embedded, ranked
against the vectors of one course only, and resolved back to chunk text. A
vector whose chunk row is gone (for example during the Chroma replication
window) is skipped, so retrieval degrades gracefully instead of surfacing
stale metadata. See docs/vector_storage.md.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models import EMBEDDING_DIMENSIONS, DocumentChunk
from services.embeddings import (
    EmbeddingDimensionMismatchError,
    EmbeddingProvider,
    get_embedding_provider,
)
from services.vector_store import VectorStore, get_vector_store


@dataclass(frozen=True, slots=True)
class RetrievedChunk:
    """One ranked chunk with its text, ready for prompt construction."""

    chunk_id: int
    document_id: UUID
    course_id: int
    chunk_index: int
    similarity: float
    text: str
    page_number: int | None = None
    end_page_number: int | None = None


def retrieve_course_chunks(
    db: Session,
    *,
    course_id: int,
    query: str,
    limit: int,
    document_ids: Sequence[UUID] | None = None,
    store: VectorStore | None = None,
    provider: EmbeddingProvider | None = None,
) -> list[RetrievedChunk]:
    """Rank the chunks of one course by semantic similarity to the query.

    The course scope is mandatory: nothing from another course can be
    returned. Results are ordered best-first and exclude chunks that no
    longer exist in the relational database.

    ``document_ids`` narrows the search to a subset of that course, for a
    caller that asked a question of particular sources. It can only ever
    narrow: the course predicate is applied first and independently, and an
    empty selection is rejected rather than read as "everything".
    """
    if not query or not query.strip():
        raise ValueError("Retrieval requires a non-blank query")
    if limit < 1:
        raise ValueError("Retrieval limit must be a positive integer")
    identifiers: list[UUID] | None = None
    if document_ids is not None:
        identifiers = list(dict.fromkeys(document_ids))
        if not identifiers:
            raise ValueError("Retrieval document_ids must not be empty when supplied")

    embedding_provider = provider or get_embedding_provider()
    query_embedding = embedding_provider.embed_query(query)
    if len(query_embedding) != EMBEDDING_DIMENSIONS:
        raise EmbeddingDimensionMismatchError(
            f"Query embeddings must contain {EMBEDDING_DIMENSIONS} values, "
            f"got {len(query_embedding)}."
        )

    results = (store or get_vector_store()).search(
        db,
        course_id=course_id,
        query_embedding=query_embedding,
        limit=limit,
        document_ids=identifiers,
    )
    if not results:
        return []

    # The course predicate is repeated here rather than trusted to the vector
    # store alone. Both layers already hold the ids, so making the mandatory
    # scope true relationally as well costs nothing and closes the gap a stale
    # metadata row would otherwise leave open.
    predicates = [
        DocumentChunk.id.in_([result.chunk_id for result in results]),
        DocumentChunk.course_id == course_id,
    ]
    if identifiers is not None:
        predicates.append(DocumentChunk.document_id.in_(identifiers))
    chunks = {
        chunk.id: chunk
        for chunk in db.scalars(select(DocumentChunk).where(*predicates)).all()
    }
    retrieved: list[RetrievedChunk] = []
    for result in results:
        chunk = chunks.get(result.chunk_id)
        if chunk is None or not chunk.text or not chunk.text.strip():
            continue
        retrieved.append(
            RetrievedChunk(
                chunk_id=result.chunk_id,
                document_id=result.document_id,
                course_id=result.course_id,
                chunk_index=result.chunk_index,
                similarity=result.similarity,
                text=chunk.text,
                page_number=chunk.page_number,
                end_page_number=chunk.end_page_number,
            )
        )
    return retrieved
