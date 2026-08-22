"""Durable vector storage behind one lifecycle-complete interface.

Two backends exist because the two supported deployments have different
databases. PostgreSQL keeps vectors in the same transactional boundary as the
chunks they describe; SQLite deployments cannot, so they persist vectors in a
local Chroma collection instead. Callers depend on this interface, never on
which one is configured. See docs/vector_storage.md.
"""

import logging
import threading
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session

from backend.app.config import (
    VECTOR_BACKEND_CHROMA,
    VECTOR_BACKEND_PGVECTOR,
    settings,
)
from backend.app.models import EMBEDDING_DIMENSIONS, ChunkEmbedding

logger = logging.getLogger(__name__)

CHROMA_COLLECTION_NAME = "lumina_chunks"
SIMILARITY_METRIC = "cosine"


class VectorStoreError(RuntimeError):
    """The vector store could not complete a lifecycle operation."""

    retryable = True


class VectorStoreConfigurationError(VectorStoreError):
    retryable = False


@dataclass(frozen=True, slots=True)
class VectorRecord:
    """One chunk's vector plus the metadata every backend must retain."""

    chunk_id: int
    document_id: UUID
    course_id: int
    chunk_index: int
    embedding: list[float]

    def validate(self) -> None:
        if len(self.embedding) != EMBEDDING_DIMENSIONS:
            raise ValueError(
                f"Vector records must contain {EMBEDDING_DIMENSIONS} values, "
                f"got {len(self.embedding)}"
            )
        if self.chunk_index < 0:
            raise ValueError("Vector records must carry a nonnegative chunk index")


@dataclass(frozen=True, slots=True)
class SearchResult:
    """One ranked vector hit, resolved by the retrieval layer to its chunk."""

    chunk_id: int
    document_id: UUID
    course_id: int
    chunk_index: int
    similarity: float


class VectorStore(Protocol):
    def replace_document_vectors(
        self,
        session: Session,
        *,
        document_id: UUID,
        course_id: int,
        records: Sequence[VectorRecord],
        embedding_provider: str,
        embedding_model: str,
    ) -> None: ...

    def upsert_document_vectors(
        self,
        session: Session,
        *,
        document_id: UUID,
        course_id: int,
        records: Sequence[VectorRecord],
        embedding_provider: str,
        embedding_model: str,
    ) -> None: ...

    def delete_document_vectors(self, session: Session, document_id: UUID) -> None: ...

    def delete_chunk_vectors(
        self, session: Session, document_id: UUID, chunk_ids: Iterable[int]
    ) -> None: ...

    def delete_course_vectors(self, session: Session, course_id: int) -> None: ...

    def chunk_ids_with_vectors(
        self, session: Session, document_id: UUID
    ) -> set[int]: ...

    def count_document_vectors(self, session: Session, document_id: UUID) -> int: ...

    def count_course_vectors(self, session: Session, course_id: int) -> int: ...

    def search(
        self,
        session: Session,
        *,
        course_id: int,
        query_embedding: list[float],
        limit: int,
    ) -> list[SearchResult]: ...


def _validated(
    records: Sequence[VectorRecord],
    *,
    document_id: UUID,
    course_id: int,
) -> list[VectorRecord]:
    validated: list[VectorRecord] = []
    seen: set[int] = set()
    for record in records:
        record.validate()
        if record.document_id != document_id or record.course_id != course_id:
            raise ValueError(
                "Vector records must belong to the document being replaced"
            )
        if record.chunk_id in seen:
            raise ValueError("Vector records must not repeat a chunk")
        seen.add(record.chunk_id)
        validated.append(record)
    return validated


class PgVectorStore:
    """Vectors as relational rows, written through the caller's transaction.

    Deleting by document is redundant with the foreign key cascade, but it is
    issued explicitly so both backends behave identically and are covered by
    the same tests.
    """

    backend = VECTOR_BACKEND_PGVECTOR

    def replace_document_vectors(
        self,
        session: Session,
        *,
        document_id: UUID,
        course_id: int,
        records: Sequence[VectorRecord],
        embedding_provider: str,
        embedding_model: str,
    ) -> None:
        validated = _validated(records, document_id=document_id, course_id=course_id)
        session.execute(
            delete(ChunkEmbedding).where(ChunkEmbedding.document_id == document_id)
        )
        session.flush()
        session.add_all(
            ChunkEmbedding(
                chunk_id=record.chunk_id,
                document_id=record.document_id,
                course_id=record.course_id,
                chunk_index=record.chunk_index,
                embedding=record.embedding,
                embedding_provider=embedding_provider,
                embedding_model=embedding_model,
                dimensions=EMBEDDING_DIMENSIONS,
            )
            for record in validated
        )
        session.flush()

    def upsert_document_vectors(
        self,
        session: Session,
        *,
        document_id: UUID,
        course_id: int,
        records: Sequence[VectorRecord],
        embedding_provider: str,
        embedding_model: str,
    ) -> None:
        validated = _validated(records, document_id=document_id, course_id=course_id)
        if not validated:
            return
        chunk_ids = [record.chunk_id for record in validated]
        session.execute(
            delete(ChunkEmbedding).where(ChunkEmbedding.chunk_id.in_(chunk_ids))
        )
        session.flush()
        session.add_all(
            ChunkEmbedding(
                chunk_id=record.chunk_id,
                document_id=record.document_id,
                course_id=record.course_id,
                chunk_index=record.chunk_index,
                embedding=record.embedding,
                embedding_provider=embedding_provider,
                embedding_model=embedding_model,
                dimensions=EMBEDDING_DIMENSIONS,
            )
            for record in validated
        )
        session.flush()

    def delete_chunk_vectors(
        self, session: Session, document_id: UUID, chunk_ids: Iterable[int]
    ) -> None:
        identifiers = list(chunk_ids)
        if not identifiers:
            return
        session.execute(
            delete(ChunkEmbedding).where(
                ChunkEmbedding.document_id == document_id,
                ChunkEmbedding.chunk_id.in_(identifiers),
            )
        )
        session.flush()

    def delete_document_vectors(self, session: Session, document_id: UUID) -> None:
        session.execute(
            delete(ChunkEmbedding).where(ChunkEmbedding.document_id == document_id)
        )
        session.flush()

    def delete_course_vectors(self, session: Session, course_id: int) -> None:
        session.execute(
            delete(ChunkEmbedding).where(ChunkEmbedding.course_id == course_id)
        )
        session.flush()

    def chunk_ids_with_vectors(self, session: Session, document_id: UUID) -> set[int]:
        return set(
            session.scalars(
                select(ChunkEmbedding.chunk_id).where(
                    ChunkEmbedding.document_id == document_id
                )
            ).all()
        )

    def count_document_vectors(self, session: Session, document_id: UUID) -> int:
        return int(
            session.scalar(
                select(func.count())
                .select_from(ChunkEmbedding)
                .where(ChunkEmbedding.document_id == document_id)
            )
            or 0
        )

    def count_course_vectors(self, session: Session, course_id: int) -> int:
        return int(
            session.scalar(
                select(func.count())
                .select_from(ChunkEmbedding)
                .where(ChunkEmbedding.course_id == course_id)
            )
            or 0
        )

    def search(
        self,
        session: Session,
        *,
        course_id: int,
        query_embedding: list[float],
        limit: int,
    ) -> list[SearchResult]:
        if len(query_embedding) != EMBEDDING_DIMENSIONS:
            raise ValueError(
                f"Query embeddings must contain {EMBEDDING_DIMENSIONS} values, "
                f"got {len(query_embedding)}"
            )
        if limit < 1:
            raise ValueError("Search limit must be a positive integer")
        if session.get_bind().dialect.name != "postgresql":
            raise VectorStoreConfigurationError(
                "pgvector similarity search requires a PostgreSQL connection."
            )
        query = "[" + ",".join(repr(value) for value in query_embedding) + "]"
        statement = text(
            "SELECT chunk_id, document_id, course_id, chunk_index, "
            "1.0 - (embedding <=> CAST(:query AS vector)) AS similarity "
            "FROM chunk_embeddings "
            "WHERE course_id = :course_id "
            "ORDER BY embedding <=> CAST(:query AS vector) "
            "LIMIT :limit"
        )
        try:
            session.execute(text("SET LOCAL hnsw.iterative_scan = 'strict_order'"))
            session.execute(text("SET LOCAL hnsw.max_scan_tuples = 20000"))
            rows = session.execute(
                statement,
                {"query": query, "course_id": course_id, "limit": limit},
            )
        except Exception as exc:
            raise VectorStoreError("The vector store could not be searched.") from exc
        return [
            SearchResult(
                chunk_id=row.chunk_id,
                document_id=row.document_id,
                course_id=row.course_id,
                chunk_index=row.chunk_index,
                similarity=row.similarity,
            )
            for row in rows
        ]


class ChromaVectorStore:
    """Vectors in a local persistent Chroma collection.

    Chroma cannot join the relational transaction, so callers write here after
    flushing chunk ids and before committing: a failure rolls the relational
    work back, and a retry replaces this document's vectors wholesale rather
    than adding to them.
    """

    backend = VECTOR_BACKEND_CHROMA

    def __init__(self, persist_directory: str | None = None) -> None:
        self._persist_directory = (
            persist_directory
            if persist_directory is not None
            else settings.chroma_persist_directory
        )
        self._client = None
        self._collection = None

    def _get_collection(self):
        if self._collection is not None:
            return self._collection
        try:
            import chromadb

            self._client = chromadb.PersistentClient(path=self._persist_directory)
            self._collection = self._client.get_or_create_collection(
                name=CHROMA_COLLECTION_NAME,
                embedding_function=None,
                configuration={"hnsw": {"space": SIMILARITY_METRIC}},
            )
        except Exception as exc:
            raise VectorStoreError("The vector store could not be opened.") from exc
        return self._collection

    def close(self) -> None:
        self._collection = None
        self._client = None

    @staticmethod
    def _collection_is_gone(exc: Exception) -> bool:
        if type(exc).__name__ in {"NotFoundError", "InvalidCollectionException"}:
            return True
        return "does not exist" in str(exc).lower()

    def _run(self, operation, message: str):
        """Run one collection call, re-resolving a handle the store has replaced.

        The collection handle is cached for the life of the process, so a store
        rebuilt underneath a long-running API would otherwise fail every later
        request until that process restarted.
        """
        try:
            return operation(self._get_collection())
        except VectorStoreError:
            raise
        except Exception as exc:
            if not self._collection_is_gone(exc):
                raise VectorStoreError(message) from exc
        self.close()
        try:
            return operation(self._get_collection())
        except VectorStoreError:
            raise
        except Exception as exc:
            raise VectorStoreError(message) from exc

    @staticmethod
    def _metadata(
        record: VectorRecord,
        embedding_provider: str,
        embedding_model: str,
    ) -> dict:
        return {
            "chunk_id": record.chunk_id,
            "document_id": str(record.document_id),
            "course_id": record.course_id,
            "chunk_index": record.chunk_index,
            "embedding_provider": embedding_provider,
            "embedding_model": embedding_model,
        }

    def replace_document_vectors(
        self,
        session: Session,
        *,
        document_id: UUID,
        course_id: int,
        records: Sequence[VectorRecord],
        embedding_provider: str,
        embedding_model: str,
    ) -> None:
        validated = _validated(records, document_id=document_id, course_id=course_id)

        def operation(collection) -> None:
            collection.delete(where={"document_id": str(document_id)})
            if validated:
                collection.upsert(
                    ids=[str(record.chunk_id) for record in validated],
                    embeddings=[record.embedding for record in validated],
                    metadatas=[
                        self._metadata(record, embedding_provider, embedding_model)
                        for record in validated
                    ],
                )

        self._run(
            operation,
            "The vector store could not record the document embeddings.",
        )

    def upsert_document_vectors(
        self,
        session: Session,
        *,
        document_id: UUID,
        course_id: int,
        records: Sequence[VectorRecord],
        embedding_provider: str,
        embedding_model: str,
    ) -> None:
        validated = _validated(records, document_id=document_id, course_id=course_id)
        if not validated:
            return
        self._run(
            lambda collection: collection.upsert(
                ids=[str(record.chunk_id) for record in validated],
                embeddings=[record.embedding for record in validated],
                metadatas=[
                    self._metadata(record, embedding_provider, embedding_model)
                    for record in validated
                ],
            ),
            "The vector store could not record the document embeddings.",
        )

    def delete_chunk_vectors(
        self, session: Session, document_id: UUID, chunk_ids: Iterable[int]
    ) -> None:
        identifiers = [str(chunk_id) for chunk_id in chunk_ids]
        if not identifiers:
            return
        self._run(
            lambda collection: collection.delete(ids=identifiers),
            "The vector store could not remove the requested embeddings.",
        )

    def _delete_where(self, where: dict) -> None:
        self._run(
            lambda collection: collection.delete(where=where),
            "The vector store could not remove the requested embeddings.",
        )

    def delete_document_vectors(self, session: Session, document_id: UUID) -> None:
        self._delete_where({"document_id": str(document_id)})

    def delete_course_vectors(self, session: Session, course_id: int) -> None:
        self._delete_where({"course_id": course_id})

    def _get_where(self, where: dict) -> dict:
        return self._run(
            lambda collection: collection.get(where=where, include=["metadatas"]),
            "The vector store could not be read.",
        )

    def chunk_ids_with_vectors(self, session: Session, document_id: UUID) -> set[int]:
        found = self._get_where({"document_id": str(document_id)})
        return {int(identifier) for identifier in found.get("ids", [])}

    def count_document_vectors(self, session: Session, document_id: UUID) -> int:
        return len(self._get_where({"document_id": str(document_id)}).get("ids", []))

    def count_course_vectors(self, session: Session, course_id: int) -> int:
        return len(self._get_where({"course_id": course_id}).get("ids", []))

    def search(
        self,
        session: Session,
        *,
        course_id: int,
        query_embedding: list[float],
        limit: int,
    ) -> list[SearchResult]:
        if len(query_embedding) != EMBEDDING_DIMENSIONS:
            raise ValueError(
                f"Query embeddings must contain {EMBEDDING_DIMENSIONS} values, "
                f"got {len(query_embedding)}"
            )
        if limit < 1:
            raise ValueError("Search limit must be a positive integer")
        found = self._run(
            lambda collection: collection.query(
                query_embeddings=[query_embedding],
                where={"course_id": course_id},
                n_results=limit,
                include=["metadatas", "distances"],
            ),
            "The vector store could not be searched.",
        )
        ids = found.get("ids", [[]])[0]
        metadatas = found.get("metadatas", [[]])[0] or []
        distances = found.get("distances", [[]])[0] or []
        return [
            SearchResult(
                chunk_id=int(identifier),
                document_id=UUID(metadata["document_id"]),
                course_id=metadata["course_id"],
                chunk_index=metadata["chunk_index"],
                similarity=1.0 - distance,
            )
            for identifier, metadata, distance in zip(
                ids, metadatas, distances, strict=True
            )
        ]

    def metadata_for_chunk(self, chunk_id: int) -> dict:
        found = self._run(
            lambda collection: collection.get(
                ids=[str(chunk_id)], include=["metadatas"]
            ),
            "The vector store could not be read.",
        )
        metadatas = found.get("metadatas") or []
        if not metadatas:
            raise VectorStoreError("The requested embedding is not stored.")
        return dict(metadatas[0])


_store: VectorStore | None = None
_store_lock = threading.Lock()


def reset_vector_store() -> None:
    """Drop the cached store so a reconfigured process builds a new one."""
    global _store
    with _store_lock:
        _store = None


def build_vector_store() -> VectorStore:
    backend = settings.vector_backend
    if backend == VECTOR_BACKEND_PGVECTOR:
        return PgVectorStore()
    if backend == VECTOR_BACKEND_CHROMA:
        return ChromaVectorStore()
    raise VectorStoreConfigurationError(
        f"Vector backend '{backend}' is not implemented."
    )


def get_vector_store() -> VectorStore:
    global _store
    with _store_lock:
        if _store is None:
            _store = build_vector_store()
        return _store
