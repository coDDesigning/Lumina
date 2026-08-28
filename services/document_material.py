"""The complete text of named documents, cited and bounded.

Retrieval answers "which passages of this course are about X". This module
answers a different question: "what does this document say, all of it, in
order". Extracting the questions from a past exam paper needs the second one,
because a paper's last question is not less of a question for ranking lower
against a similarity query, and a budget spent in relevance order would drop it
without ever saying so.

Passages are headed with the same citation keys retrieval uses, so a model can
attribute a question to the page it came from and the application can resolve
that attribution against passages it actually supplied. The keys are built here
rather than through ``build_supplied_citations``, because that helper keys
retrieval hits and these are chunk rows read straight out of the table.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models import DocumentChunk, UploadedDocument
from services.citations import (
    SuppliedCitation,
    citation_header,
    citation_key,
    document_label,
)
from services.course_material import CHUNK_SEPARATOR

CITATION_HEADER_SEPARATOR = "\n"

READY_STATUS = "ready"


@dataclass(frozen=True)
class DocumentMaterial:
    """One or more whole documents, assembled into bounded prompt material."""

    text: str
    chunks_used: int
    chunks_available: int
    truncated: bool
    document_ids: tuple[UUID, ...] = ()
    citations: tuple[SuppliedCitation, ...] = ()

    @property
    def is_empty(self) -> bool:
        return not self.text

    @property
    def citation_map(self) -> dict[str, SuppliedCitation]:
        return {citation.key: citation for citation in self.citations}


def load_document_material(
    db: Session,
    course_id: int,
    document_ids: Sequence[UUID],
    *,
    max_characters: int,
) -> DocumentMaterial:
    """Assemble the whole text of the named documents, in document order.

    The course scope is mandatory and applied in the query, so a document
    belonging to another course cannot be read even if its identifier is
    known. Only ``ready`` documents contribute, for the same reason retrieval
    excludes the others: a document being reprocessed still holds its old
    chunks.

    Truncation is reported rather than hidden. Reading a paper in order means
    the budget runs out at the end, so a caller that has to cut a paper short
    can say which questions it may not have seen.
    """
    if max_characters <= 0:
        raise ValueError("max_characters must be a positive integer.")
    if not document_ids:
        return DocumentMaterial(
            text="", chunks_used=0, chunks_available=0, truncated=False
        )

    identifiers = list(dict.fromkeys(document_ids))
    rows = db.execute(
        select(
            DocumentChunk,
            UploadedDocument.created_at,
            UploadedDocument.original_file_name,
        )
        .join(UploadedDocument, DocumentChunk.document_id == UploadedDocument.id)
        .where(
            DocumentChunk.course_id == course_id,
            DocumentChunk.document_id.in_(identifiers),
            UploadedDocument.status == READY_STATUS,
        )
        .order_by(
            UploadedDocument.created_at,
            DocumentChunk.document_id,
            DocumentChunk.chunk_index,
        )
    ).all()

    available = [row for row in rows if row[0].text and row[0].text.strip()]
    if not available:
        return DocumentMaterial(
            text="", chunks_used=0, chunks_available=0, truncated=False
        )

    file_names = {row[0].document_id: row[2] for row in available}
    labels = {
        document_id: document_label(name) for document_id, name in file_names.items()
    }

    kept: list[DocumentChunk] = []
    length = 0
    truncated = False
    for chunk, _, _ in available:
        stripped = chunk.text.strip()
        header = citation_header(
            key=citation_key(len(kept) + 1),
            label=labels[chunk.document_id],
            page_start=chunk.page_number,
            page_end=chunk.end_page_number,
        )
        addition = (
            len(stripped)
            + len(header)
            + len(CITATION_HEADER_SEPARATOR)
            + (len(CHUNK_SEPARATOR) if kept else 0)
        )
        if length + addition > max_characters:
            truncated = True
            break
        kept.append(chunk)
        length += addition

    if not kept:
        return DocumentMaterial(
            text="",
            chunks_used=0,
            chunks_available=len(available),
            truncated=True,
        )

    citations = tuple(
        SuppliedCitation(
            key=citation_key(position),
            chunk_id=chunk.id,
            document_id=chunk.document_id,
            document_label=labels[chunk.document_id],
            page_start=chunk.page_number,
            page_end=chunk.end_page_number,
        )
        for position, chunk in enumerate(kept, start=1)
    )
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

    return DocumentMaterial(
        text=CHUNK_SEPARATOR.join(passages),
        chunks_used=len(kept),
        chunks_available=len(available),
        truncated=truncated,
        document_ids=tuple(dict.fromkeys(chunk.document_id for chunk in kept)),
        citations=citations,
    )
