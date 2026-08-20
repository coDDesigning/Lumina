"""Course-isolated retrieval assembled into bounded prompt material.

This is the retrieval-backed replacement for ``services/course_material.py``.
It ranks one course's chunks against a query, discards anything below the
configured similarity floor, and assembles the survivors into a character
bounded string.

There is deliberately no fallback to whole-corpus assembly. A request that
matches nothing is answered as such, never silently widened, so a study guide
can never be built from material the request did not ask for.

The module reads no settings: every bound is supplied by the calling feature,
the same way ``load_course_material`` takes its character budget today.
"""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models import UploadedDocument
from services.course_material import (
    CHUNK_SEPARATOR,
    CourseMaterial,
    count_available_chunks,
)
from services.embeddings import (
    EmbeddingError,
    EmbeddingProvider,
    EmbeddingRateLimitError,
    EmbeddingTimeoutError,
)
from services.semantic_retrieval import RetrievedChunk, retrieve_course_chunks
from services.vector_store import VectorStore, VectorStoreError
from utils.ai_errors import (
    NO_RELEVANT_MATERIAL_MESSAGE,
    NoRelevantCourseMaterialError,
    RetrievalRateLimitedError,
    RetrievalTimeoutError,
    RetrievalUnavailableError,
)

RETRIEVAL_FAILED_MESSAGE = "Course material could not be retrieved."

READY_STATUS = "ready"


class RetrievalMaterialError(RuntimeError):
    """Retrieval-backed material could not be assembled."""


class NoRelevantMaterialError(RetrievalMaterialError, NoRelevantCourseMaterialError):
    """Nothing in this course cleared the similarity floor for this query."""


class MaterialRetrievalError(RetrievalMaterialError, RetrievalUnavailableError):
    """Embedding or vector-store failure while retrieving course material."""


class MaterialRetrievalTimeoutError(MaterialRetrievalError, RetrievalTimeoutError):
    """Retrieval did not answer in time."""


class MaterialRetrievalRateLimitError(
    MaterialRetrievalError, RetrievalRateLimitedError
):
    """Retrieval was rate limited."""


@dataclass(frozen=True)
class RetrievedCourseMaterial(CourseMaterial):
    """``CourseMaterial`` plus the retrieval diagnostics the caller may report."""

    chunks_retrieved: int
    chunks_ranked: int
    lowest_similarity: float | None
    highest_similarity: float | None

    @property
    def retrieval_narrowed(self) -> bool:
        return self.chunks_used < self.chunks_available


def _validate(
    *, query: str, limit: int, min_similarity: float, max_characters: int
) -> None:
    if not query or not query.strip():
        raise ValueError("Retrieval requires a non-blank query")
    if limit < 1:
        raise ValueError("Retrieval limit must be a positive integer")
    if not 0.0 <= min_similarity <= 1.0:
        raise ValueError("Retrieval min_similarity must be between 0.0 and 1.0")
    if max_characters <= 0:
        raise ValueError("max_characters must be a positive integer.")


def _rank(
    db: Session,
    course_id: int,
    *,
    query: str,
    limit: int,
    provider: EmbeddingProvider | None,
    store: VectorStore | None,
) -> list[RetrievedChunk]:
    try:
        return retrieve_course_chunks(
            db,
            course_id=course_id,
            query=query,
            limit=limit,
            provider=provider,
            store=store,
        )
    except EmbeddingTimeoutError as exc:
        raise MaterialRetrievalTimeoutError(RETRIEVAL_FAILED_MESSAGE) from exc
    except EmbeddingRateLimitError as exc:
        raise MaterialRetrievalRateLimitError(RETRIEVAL_FAILED_MESSAGE) from exc
    except (EmbeddingError, VectorStoreError) as exc:
        raise MaterialRetrievalError(RETRIEVAL_FAILED_MESSAGE) from exc


def _ready_document_order(
    db: Session, document_ids: set[UUID]
) -> dict[UUID, tuple[datetime, str]]:
    """Corpus position of each ready document among ``document_ids``.

    Doubles as the ready-only filter. A document keeps its old chunks and vectors
    while it is being reprocessed, so ranking alone would surface material the
    whole-corpus assembler excludes; a document missing from this map is dropped
    rather than ordered.
    """
    rows = db.execute(
        select(UploadedDocument.id, UploadedDocument.created_at).where(
            UploadedDocument.id.in_(document_ids),
            UploadedDocument.status == READY_STATUS,
        )
    ).all()
    return {row.id: (row.created_at, str(row.id)) for row in rows}


def load_retrieved_material(
    db: Session,
    course_id: int,
    *,
    query: str,
    limit: int,
    min_similarity: float,
    max_characters: int,
    provider: EmbeddingProvider | None = None,
    store: VectorStore | None = None,
) -> RetrievedCourseMaterial:
    """Assemble bounded prompt material from one course's most relevant chunks.

    Selection spends the character budget in similarity order so the most
    relevant material is never the part that gets cut. Emission is in corpus
    order so the prompt still reads as coherent prose.
    """
    _validate(
        query=query,
        limit=limit,
        min_similarity=min_similarity,
        max_characters=max_characters,
    )

    ranked = _rank(
        db, course_id, query=query, limit=limit, provider=provider, store=store
    )
    order = _ready_document_order(db, {chunk.document_id for chunk in ranked})
    survivors = [
        chunk
        for chunk in ranked
        if chunk.document_id in order
        and chunk.similarity >= min_similarity
        and chunk.text
        and chunk.text.strip()
    ]
    if not survivors:
        raise NoRelevantMaterialError(NO_RELEVANT_MATERIAL_MESSAGE)

    kept: list[RetrievedChunk] = []
    length = 0
    truncated = False
    for chunk in survivors:
        stripped = chunk.text.strip()
        addition = len(stripped) + (len(CHUNK_SEPARATOR) if kept else 0)
        if length + addition > max_characters:
            truncated = True
            break
        kept.append(chunk)
        length += addition

    if not kept:
        raise NoRelevantMaterialError(NO_RELEVANT_MATERIAL_MESSAGE)

    kept.sort(
        key=lambda chunk: (
            order[chunk.document_id],
            chunk.chunk_index,
            chunk.chunk_id,
        )
    )

    similarities = [chunk.similarity for chunk in kept]

    return RetrievedCourseMaterial(
        text=CHUNK_SEPARATOR.join(chunk.text.strip() for chunk in kept),
        chunks_used=len(kept),
        chunks_available=count_available_chunks(db, course_id),
        truncated=truncated,
        chunks_retrieved=len(survivors),
        chunks_ranked=len(ranked),
        lowest_similarity=min(similarities),
        highest_similarity=max(similarities),
    )
