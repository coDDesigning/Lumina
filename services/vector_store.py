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
from backend.app.models import (
    EMBEDDING_DIMENSIONS,
    ChunkEmbedding,
    ProfileChunkEmbedding,
)

logger = logging.getLogger(__name__)

CHROMA_COLLECTION_NAME = "lumina_chunks"
CHROMA_PROFILE_COLLECTION_NAME = "lumina_profile_chunks"
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
        self, session: Session, document_id: UUID, *, embedding_model: str
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
        embedding_model: str,
        document_ids: Sequence[UUID] | None = None,
    ) -> list[SearchResult]: ...

    def replace_profile_document_vectors(
        self,
        session: Session,
        *,
        document_id: UUID,
        user_id: int,
        records: Sequence[VectorRecord],
        embedding_provider: str,
        embedding_model: str,
    ) -> None: ...

    def profile_chunk_ids_with_vectors(
        self, session: Session, document_id: UUID, *, embedding_model: str
    ) -> set[int]: ...

    def delete_profile_document_vectors(
        self, session: Session, document_id: UUID
    ) -> None: ...

    def delete_user_profile_vectors(self, session: Session, user_id: int) -> None: ...

    def search_profile(
        self,
        session: Session,
        *,
        user_id: int,
        query_embedding: list[float],
        limit: int,
        embedding_model: str,
        document_ids: Sequence[UUID] | None = None,
    ) -> list[SearchResult]: ...


def _narrowing_documents(document_ids: Sequence[UUID] | None) -> list[UUID] | None:
    """The deduplicated document filter for one search, or ``None`` for all.

    An empty selection is an error rather than a synonym for the whole course.
    Widening it silently would be the whole-corpus fallback this retrieval path
    exists to refuse, and it would answer a question the caller did not ask.
    """
    if document_ids is None:
        return None
    identifiers = list(dict.fromkeys(document_ids))
    if not identifiers:
        raise ValueError("Search document_ids must not be empty when supplied")
    return identifiers


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
                "All vector records in a replacement batch must match the batch "
                "document_id and course_id."
            )
        if record.chunk_id in seen:
            raise ValueError(
                "Vector replacement batches must not contain duplicate chunk_ids."
            )
        seen.add(record.chunk_id)
        validated.append(record)
    return validated


def _validated_profile(
    records: Sequence[VectorRecord],
    *,
    document_id: UUID,
    user_id: int,
) -> list[VectorRecord]:
    validated: list[VectorRecord] = []
    seen: set[int] = set()
    for record in records:
        record.validate()
        if record.document_id != document_id or record.course_id != user_id:
            raise ValueError(
                "All vector records in a profile replacement batch must match the batch "
                "document_id and user_id."
            )
        if record.chunk_id in seen:
            raise ValueError(
                "Vector replacement batches must not contain duplicate chunk_ids."
            )
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

    def chunk_ids_with_vectors(
        self, session: Session, document_id: UUID, *, embedding_model: str
    ) -> set[int]:
        return set(
            session.scalars(
                select(ChunkEmbedding.chunk_id).where(
                    ChunkEmbedding.document_id == document_id,
                    ChunkEmbedding.embedding_model == embedding_model,
                )
            ).all()
        )

    def profile_chunk_ids_with_vectors(
        self, session: Session, document_id: UUID, *, embedding_model: str
    ) -> set[int]:
        return set(
            session.scalars(
                select(ProfileChunkEmbedding.chunk_id).where(
                    ProfileChunkEmbedding.document_id == document_id,
                    ProfileChunkEmbedding.embedding_model == embedding_model,
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
        embedding_model: str,
        document_ids: Sequence[UUID] | None = None,
    ) -> list[SearchResult]:
        if len(query_embedding) != EMBEDDING_DIMENSIONS:
            raise ValueError(
                f"Query embeddings must contain {EMBEDDING_DIMENSIONS} values, "
                f"got {len(query_embedding)}"
            )
        if limit < 1:
            raise ValueError("Search limit must be a positive integer")
        identifiers = _narrowing_documents(document_ids)
        if session.get_bind().dialect.name != "postgresql":
            raise VectorStoreConfigurationError(
                "pgvector similarity search requires a PostgreSQL connection."
            )
        query = "[" + ",".join(repr(value) for value in query_embedding) + "]"
        # The document filter narrows within the course scope and can never
        # widen it, so the course predicate stays first and unconditional.
        document_filter = (
            " AND document_id = ANY(CAST(:document_ids AS uuid[]))"
            if identifiers
            else ""
        )
        statement = text(
            "SELECT chunk_id, document_id, course_id, chunk_index, "
            "1.0 - (embedding <=> CAST(:query AS vector)) AS similarity "
            "FROM chunk_embeddings "
            "WHERE course_id = :course_id AND embedding_model = :embedding_model"
            + document_filter
            + " "
            "ORDER BY embedding <=> CAST(:query AS vector) "
            "LIMIT :limit"
        )
        parameters: dict[str, object] = {
            "query": query,
            "course_id": course_id,
            "embedding_model": embedding_model,
            "limit": limit,
        }
        if identifiers:
            parameters["document_ids"] = [str(value) for value in identifiers]
        try:
            session.execute(text("SET LOCAL hnsw.iterative_scan = 'strict_order'"))
            session.execute(text("SET LOCAL hnsw.max_scan_tuples = 20000"))
            rows = session.execute(statement, parameters)
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

    def replace_profile_document_vectors(
        self,
        session: Session,
        *,
        document_id: UUID,
        user_id: int,
        records: Sequence[VectorRecord],
        embedding_provider: str,
        embedding_model: str,
    ) -> None:
        validated = _validated_profile(
            records, document_id=document_id, user_id=user_id
        )
        session.execute(
            delete(ProfileChunkEmbedding).where(
                ProfileChunkEmbedding.document_id == document_id
            )
        )
        session.flush()
        if not validated:
            return
        session.add_all(
            ProfileChunkEmbedding(
                chunk_id=record.chunk_id,
                document_id=record.document_id,
                user_id=record.course_id,
                chunk_index=record.chunk_index,
                embedding=record.embedding,
                embedding_provider=embedding_provider,
                embedding_model=embedding_model,
                dimensions=EMBEDDING_DIMENSIONS,
            )
            for record in validated
        )
        session.flush()

    def delete_profile_document_vectors(
        self, session: Session, document_id: UUID
    ) -> None:
        session.execute(
            delete(ProfileChunkEmbedding).where(
                ProfileChunkEmbedding.document_id == document_id
            )
        )
        session.flush()

    def delete_user_profile_vectors(self, session: Session, user_id: int) -> None:
        session.execute(
            delete(ProfileChunkEmbedding).where(
                ProfileChunkEmbedding.user_id == user_id
            )
        )
        session.flush()

    def search_profile(
        self,
        session: Session,
        *,
        user_id: int,
        query_embedding: list[float],
        limit: int,
        embedding_model: str,
        document_ids: Sequence[UUID] | None = None,
    ) -> list[SearchResult]:
        if len(query_embedding) != EMBEDDING_DIMENSIONS:
            raise ValueError(
                f"Query embeddings must contain {EMBEDDING_DIMENSIONS} values, "
                f"got {len(query_embedding)}"
            )
        if limit < 1:
            raise ValueError("Search limit must be a positive integer")
        identifiers = _narrowing_documents(document_ids)
        if session.get_bind().dialect.name != "postgresql":
            raise VectorStoreConfigurationError(
                "pgvector similarity search requires a PostgreSQL connection."
            )
        query = "[" + ",".join(repr(value) for value in query_embedding) + "]"
        document_filter = (
            " AND document_id = ANY(CAST(:document_ids AS uuid[]))"
            if identifiers
            else ""
        )
        statement = text(
            "SELECT chunk_id, document_id, user_id, chunk_index, "
            "1.0 - (embedding <=> CAST(:query AS vector)) AS similarity "
            "FROM profile_chunk_embeddings "
            "WHERE user_id = :user_id AND embedding_model = :embedding_model"
            + document_filter
            + " "
            "ORDER BY embedding <=> CAST(:query AS vector) "
            "LIMIT :limit"
        )
        parameters: dict[str, object] = {
            "query": query,
            "user_id": user_id,
            "embedding_model": embedding_model,
            "limit": limit,
        }
        if identifiers:
            parameters["document_ids"] = [str(value) for value in identifiers]
        try:
            session.execute(text("SET LOCAL hnsw.iterative_scan = 'strict_order'"))
            session.execute(text("SET LOCAL hnsw.max_scan_tuples = 20000"))
            rows = session.execute(statement, parameters)
        except Exception as exc:
            raise VectorStoreError("The vector store could not be searched.") from exc
        return [
            SearchResult(
                chunk_id=row.chunk_id,
                document_id=row.document_id,
                course_id=row.user_id,
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
        self._profile_collection = None

    def _get_collection(self):
        if self._collection is not None:
            return self._collection
        try:
            import chromadb

            if self._client is None:
                self._client = chromadb.PersistentClient(path=self._persist_directory)
            self._collection = self._client.get_or_create_collection(
                name=CHROMA_COLLECTION_NAME,
                embedding_function=None,
                configuration={"hnsw": {"space": SIMILARITY_METRIC}},
            )
        except Exception as exc:
            raise VectorStoreError("The vector store could not be opened.") from exc
        return self._collection

    def _get_profile_collection(self):
        if self._profile_collection is not None:
            return self._profile_collection
        try:
            import chromadb

            if self._client is None:
                self._client = chromadb.PersistentClient(path=self._persist_directory)
            self._profile_collection = self._client.get_or_create_collection(
                name=CHROMA_PROFILE_COLLECTION_NAME,
                embedding_function=None,
                configuration={"hnsw": {"space": SIMILARITY_METRIC}},
            )
        except Exception as exc:
            raise VectorStoreError("The vector store could not be opened.") from exc
        return self._profile_collection

    def close(self) -> None:
        self._collection = None
        self._profile_collection = None
        self._client = None

    def _discard_client(self) -> None:
        """Drop this client and the process-wide system Chroma caches for it.

        ``PersistentClient`` hands back a cached system for a given path, so
        clearing the local references alone would rebind the same stale
        in-memory index instead of reading what another process just wrote.
        """
        self.close()
        try:
            from chromadb.api.client import SharedSystemClient

            SharedSystemClient.clear_system_cache()
        except Exception:
            logger.warning("Chroma system cache could not be cleared")

    def _run(self, operation, message: str):
        """Run one collection call, reopening a handle another process invalidated.

        The client caches the collection and its in-memory index for the life of
        the process, so a worker writing to the same embedded store leaves this
        handle stale: reads then fail until the process restarts. Every operation
        here is idempotent, so reopening once and retrying keeps a long-running
        API serving rather than failing every later request.
        """
        try:
            collection = self._get_collection()
            return operation(collection)
        except VectorStoreError:
            raise
        except Exception:
            self._discard_client()
        try:
            collection = self._get_collection()
            return operation(collection)
        except Exception as exc:
            raise VectorStoreError(message) from exc

    def _run_profile(self, operation, error_message: str):
        collection = self._get_profile_collection()
        try:
            return operation(collection)
        except Exception as exc:
            raise VectorStoreError(error_message) from exc

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

    def _get_profile_where(self, where: dict) -> dict:
        return self._run_profile(
            lambda collection: collection.get(where=where, include=["metadatas"]),
            "The vector store could not be read.",
        )

    def chunk_ids_with_vectors(
        self, session: Session, document_id: UUID, *, embedding_model: str
    ) -> set[int]:
        found = self._get_where(
            {
                "$and": [
                    {"document_id": {"$eq": str(document_id)}},
                    {"embedding_model": {"$eq": embedding_model}},
                ]
            }
        )
        return {int(identifier) for identifier in found.get("ids", [])}

    def profile_chunk_ids_with_vectors(
        self, session: Session, document_id: UUID, *, embedding_model: str
    ) -> set[int]:
        found = self._get_profile_where(
            {
                "$and": [
                    {"document_id": {"$eq": str(document_id)}},
                    {"embedding_model": {"$eq": embedding_model}},
                ]
            }
        )
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
        embedding_model: str,
        document_ids: Sequence[UUID] | None = None,
    ) -> list[SearchResult]:
        if len(query_embedding) != EMBEDDING_DIMENSIONS:
            raise ValueError(
                f"Query embeddings must contain {EMBEDDING_DIMENSIONS} values, "
                f"got {len(query_embedding)}"
            )
        if limit < 1:
            raise ValueError("Search limit must be a positive integer")
        identifiers = _narrowing_documents(document_ids)
        clauses: list[dict] = [
            {"course_id": {"$eq": course_id}},
            {"embedding_model": {"$eq": embedding_model}},
        ]
        if identifiers is not None:
            # _metadata stores document_id as a string, so a UUID operand would
            # match nothing and be indistinguishable from an honest empty result.
            clauses.append(
                {"document_id": {"$in": [str(value) for value in identifiers]}}
            )
        where: dict = {"$and": clauses}
        found = self._run(
            lambda collection: collection.query(
                query_embeddings=[query_embedding],
                where=where,
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

    def replace_profile_document_vectors(
        self,
        session: Session,
        *,
        document_id: UUID,
        user_id: int,
        records: Sequence[VectorRecord],
        embedding_provider: str,
        embedding_model: str,
    ) -> None:
        validated = _validated_profile(
            records, document_id=document_id, user_id=user_id
        )
        self._run_profile(
            lambda collection: collection.delete(
                where={"document_id": str(document_id)}
            ),
            "The vector store could not remove stale profile embeddings.",
        )
        if not validated:
            return
        self._run_profile(
            lambda collection: collection.add(
                ids=[str(record.chunk_id) for record in validated],
                embeddings=[record.embedding for record in validated],
                metadatas=[
                    {
                        "chunk_id": record.chunk_id,
                        "document_id": str(record.document_id),
                        "user_id": user_id,
                        "chunk_index": record.chunk_index,
                        "embedding_provider": embedding_provider,
                        "embedding_model": embedding_model,
                    }
                    for record in validated
                ],
            ),
            "The vector store could not persist the requested profile embeddings.",
        )

    def delete_profile_document_vectors(
        self, session: Session, document_id: UUID
    ) -> None:
        self._run_profile(
            lambda collection: collection.delete(
                where={"document_id": str(document_id)}
            ),
            "The vector store could not remove the requested profile embeddings.",
        )

    def delete_user_profile_vectors(self, session: Session, user_id: int) -> None:
        self._run_profile(
            lambda collection: collection.delete(where={"user_id": user_id}),
            "The vector store could not remove the requested profile embeddings.",
        )

    def search_profile(
        self,
        session: Session,
        *,
        user_id: int,
        query_embedding: list[float],
        limit: int,
        embedding_model: str,
        document_ids: Sequence[UUID] | None = None,
    ) -> list[SearchResult]:
        if len(query_embedding) != EMBEDDING_DIMENSIONS:
            raise ValueError(
                f"Query embeddings must contain {EMBEDDING_DIMENSIONS} values, "
                f"got {len(query_embedding)}"
            )
        if limit < 1:
            raise ValueError("Search limit must be a positive integer")
        identifiers = _narrowing_documents(document_ids)
        clauses: list[dict] = [
            {"user_id": {"$eq": user_id}},
            {"embedding_model": {"$eq": embedding_model}},
        ]
        if identifiers is not None:
            clauses.append(
                {"document_id": {"$in": [str(value) for value in identifiers]}}
            )
        where: dict = {"$and": clauses}
        found = self._run_profile(
            lambda collection: collection.query(
                query_embeddings=[query_embedding],
                where=where,
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
                course_id=metadata["user_id"],
                chunk_index=metadata["chunk_index"],
                similarity=1.0 - distance,
            )
            for identifier, metadata, distance in zip(
                ids, metadatas, distances, strict=True
            )
        ]


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
