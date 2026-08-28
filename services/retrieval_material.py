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

from collections.abc import Callable, Sequence
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
from services.citations import (
    SuppliedCitation,
    build_supplied_citations,
    citation_header,
    document_label,
)
from services.semantic_retrieval import RetrievedChunk, retrieve_course_chunks
from services.vector_store import VectorStore, VectorStoreError
from utils.ai_errors import (
    MATERIAL_NOT_INDEXED_MESSAGE,
    NO_RELEVANT_MATERIAL_MESSAGE,
    CourseMaterialNotIndexedError,
    NoRelevantCourseMaterialError,
    RetrievalRateLimitedError,
    RetrievalTimeoutError,
    RetrievalUnavailableError,
)

RETRIEVAL_FAILED_MESSAGE = "Course material could not be retrieved."

CITATION_HEADER_SEPARATOR = "\n"

READY_STATUS = "ready"


class RetrievalMaterialError(RuntimeError):
    """Retrieval-backed material could not be assembled."""


class NoRelevantMaterialError(RetrievalMaterialError, NoRelevantCourseMaterialError):
    """Nothing in this course cleared the similarity floor for this query."""


class MaterialNotIndexedError(RetrievalMaterialError, CourseMaterialNotIndexedError):
    """This course has ready chunks, but none of them are in the vector store.

    Ranking returning nothing at all is an indexing gap rather than a relevance
    miss: the query never got the chance to match. Reporting it as a miss is
    what sends a student off widening a topic that was never the problem.
    """


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

    chunks_retrieved: int = 0
    chunks_ranked: int = 0
    lowest_similarity: float | None = None
    highest_similarity: float | None = None
    citations: tuple[SuppliedCitation, ...] = ()
    # What the caller asked to read, as distinct from ``document_ids``, which
    # is what the budget actually reached. Persisting both is what makes a
    # stored generation auditable against the sources it claimed to use.
    document_ids_requested: tuple[UUID, ...] = ()

    @property
    def retrieval_narrowed(self) -> bool:
        return self.chunks_used < self.chunks_available

    @property
    def citation_map(self) -> dict[str, SuppliedCitation]:
        return {citation.key: citation for citation in self.citations}


def _validate(
    *,
    query: str,
    limit: int,
    min_similarity: float,
    max_characters: int,
    document_ids: Sequence[UUID] | None,
) -> None:
    if not query or not query.strip():
        raise ValueError("Retrieval requires a non-blank query")
    if limit < 1:
        raise ValueError("Retrieval limit must be a positive integer")
    if not 0.0 <= min_similarity <= 1.0:
        raise ValueError("Retrieval min_similarity must be between 0.0 and 1.0")
    if max_characters <= 0:
        raise ValueError("max_characters must be a positive integer.")
    if document_ids is not None and not list(document_ids):
        raise ValueError("Retrieval document_ids must not be empty when supplied")


def _rank(
    db: Session,
    course_id: int,
    *,
    query: str,
    limit: int,
    document_ids: Sequence[UUID] | None,
    provider: EmbeddingProvider | None,
    store: VectorStore | None,
) -> list[RetrievedChunk]:
    try:
        return retrieve_course_chunks(
            db,
            course_id=course_id,
            query=query,
            limit=limit,
            document_ids=document_ids,
            provider=provider,
            store=store,
        )
    except EmbeddingTimeoutError as exc:
        raise MaterialRetrievalTimeoutError(RETRIEVAL_FAILED_MESSAGE) from exc
    except EmbeddingRateLimitError as exc:
        raise MaterialRetrievalRateLimitError(RETRIEVAL_FAILED_MESSAGE) from exc
    except (EmbeddingError, VectorStoreError) as exc:
        raise MaterialRetrievalError(RETRIEVAL_FAILED_MESSAGE) from exc


@dataclass(frozen=True)
class _ReadyDocument:
    order: tuple[datetime, str]
    file_name: str


def _ready_documents(
    db: Session, document_ids: set[UUID]
) -> dict[UUID, _ReadyDocument]:
    """Corpus position and file name of each ready document among ``document_ids``.

    Doubles as the ready-only filter. A document keeps its old chunks and vectors
    while it is being reprocessed, so ranking alone would surface material the
    whole-corpus assembler excludes; a document missing from this map is dropped
    rather than ordered.
    """
    rows = db.execute(
        select(
            UploadedDocument.id,
            UploadedDocument.created_at,
            UploadedDocument.original_file_name,
        ).where(
            UploadedDocument.id.in_(document_ids),
            UploadedDocument.status == READY_STATUS,
        )
    ).all()
    return {
        row.id: _ReadyDocument(
            order=(row.created_at, str(row.id)),
            file_name=row.original_file_name,
        )
        for row in rows
    }


def _reserved_header_width(
    survivors: list[RetrievedChunk], documents: dict[UUID, _ReadyDocument]
) -> Callable[[RetrievedChunk], int]:
    widest_key = "S" + "9" * len(str(len(survivors)))
    labels = {
        document_id: document_label(document.file_name)
        for document_id, document in documents.items()
    }

    def reserve(chunk: RetrievedChunk) -> int:
        header = citation_header(
            key=widest_key,
            label=labels[chunk.document_id],
            page_start=chunk.page_number,
            page_end=chunk.end_page_number,
        )
        return len(header) + len(CITATION_HEADER_SEPARATOR)

    return reserve


def _assemble_text(
    kept: list[RetrievedChunk], citations: tuple[SuppliedCitation, ...]
) -> str:
    if not citations:
        return CHUNK_SEPARATOR.join(chunk.text.strip() for chunk in kept)

    passages = [
        citation_header(
            key=citation.key,
            label=citation.document_label,
            page_start=citation.page_start,
            page_end=citation.page_end,
        )
        + CITATION_HEADER_SEPARATOR
        + chunk.text.strip()
        for chunk, citation in zip(kept, citations, strict=True)
    ]
    return CHUNK_SEPARATOR.join(passages)


def load_retrieved_material(
    db: Session,
    course_id: int,
    *,
    query: str,
    limit: int,
    min_similarity: float,
    max_characters: int,
    include_citations: bool,
    document_ids: Sequence[UUID] | None = None,
    provider: EmbeddingProvider | None = None,
    store: VectorStore | None = None,
) -> RetrievedCourseMaterial:
    """Assemble bounded prompt material from one course's most relevant chunks.

    Selection spends the character budget in similarity order so the most
    relevant material is never the part that gets cut. Emission is in corpus
    order so the prompt still reads as coherent prose.

    ``document_ids`` narrows the search to sources the caller chose. It never
    widens the course scope, and an empty selection is rejected rather than
    quietly treated as the whole course.
    """
    _validate(
        query=query,
        limit=limit,
        min_similarity=min_similarity,
        max_characters=max_characters,
        document_ids=document_ids,
    )
    requested = tuple(dict.fromkeys(document_ids)) if document_ids is not None else ()

    ranked = _rank(
        db,
        course_id,
        query=query,
        limit=limit,
        document_ids=document_ids,
        provider=provider,
        store=store,
    )
    # Nothing ranked at all means the course holds no vectors: the caller has
    # already established it has ready chunks, so this is an indexing gap.
    if not ranked:
        raise MaterialNotIndexedError(MATERIAL_NOT_INDEXED_MESSAGE)

    documents = _ready_documents(db, {chunk.document_id for chunk in ranked})
    survivors = [
        chunk
        for chunk in ranked
        if chunk.document_id in documents
        and chunk.similarity >= min_similarity
        and chunk.text
        and chunk.text.strip()
    ]
    if not survivors:
        raise NoRelevantMaterialError(NO_RELEVANT_MATERIAL_MESSAGE)

    reserve = (
        _reserved_header_width(survivors, documents) if include_citations else None
    )

    kept: list[RetrievedChunk] = []
    length = 0
    truncated = False
    for chunk in survivors:
        stripped = chunk.text.strip()
        addition = len(stripped) + (len(CHUNK_SEPARATOR) if kept else 0)
        if reserve is not None:
            addition += reserve(chunk)
        if length + addition > max_characters:
            truncated = True
            break
        kept.append(chunk)
        length += addition

    if not kept:
        raise NoRelevantMaterialError(NO_RELEVANT_MATERIAL_MESSAGE)

    kept.sort(
        key=lambda chunk: (
            documents[chunk.document_id].order,
            chunk.chunk_index,
            chunk.chunk_id,
        )
    )

    similarities = [chunk.similarity for chunk in kept]
    citations = (
        build_supplied_citations(
            kept,
            documents={
                document_id: document.file_name
                for document_id, document in documents.items()
            },
        )
        if include_citations
        else ()
    )

    return RetrievedCourseMaterial(
        text=_assemble_text(kept, citations),
        chunks_used=len(kept),
        chunks_available=count_available_chunks(db, course_id),
        truncated=truncated,
        document_ids=tuple(dict.fromkeys(chunk.document_id for chunk in kept)),
        chunks_retrieved=len(survivors),
        chunks_ranked=len(ranked),
        lowest_similarity=min(similarities),
        highest_similarity=max(similarities),
        citations=citations,
        document_ids_requested=requested,
    )
