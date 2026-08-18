from dataclasses import dataclass

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from backend.app.models import DocumentChunk, UploadedDocument

CHUNK_SEPARATOR = "\n\n"

_STREAM_BATCH_SIZE = 200


@dataclass(frozen=True)
class CourseMaterial:
    text: str
    chunks_used: int
    chunks_available: int
    truncated: bool

    @property
    def is_empty(self) -> bool:
        return not self.text


def _ready_chunks(statement: Select, course_id: int) -> Select:
    return statement.join(
        UploadedDocument,
        DocumentChunk.document_id == UploadedDocument.id,
    ).where(
        DocumentChunk.course_id == course_id,
        UploadedDocument.status == "ready",
    )


def count_available_chunks(db: Session, course_id: int) -> int:
    statement = _ready_chunks(
        select(func.count()).select_from(DocumentChunk),
        course_id,
    )
    return db.scalar(statement) or 0


def load_course_material(
    db: Session,
    course_id: int,
    *,
    max_characters: int,
) -> CourseMaterial:
    if max_characters <= 0:
        raise ValueError("max_characters must be a positive integer.")

    statement = (
        _ready_chunks(select(DocumentChunk.text), course_id)
        .order_by(
            UploadedDocument.created_at,
            UploadedDocument.id,
            DocumentChunk.chunk_index,
            DocumentChunk.id,
        )
        .execution_options(yield_per=_STREAM_BATCH_SIZE)
    )

    parts: list[str] = []
    length = 0
    truncated = False

    chunks = db.scalars(statement)
    try:
        for text in chunks:
            stripped = (text or "").strip()
            if not stripped:
                continue
            addition = len(stripped) + (len(CHUNK_SEPARATOR) if parts else 0)
            if length + addition > max_characters:
                truncated = True
                break
            parts.append(stripped)
            length += addition
    finally:
        chunks.close()

    return CourseMaterial(
        text=CHUNK_SEPARATOR.join(parts),
        chunks_used=len(parts),
        chunks_available=count_available_chunks(db, course_id),
        truncated=truncated,
    )
