import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from uuid import uuid4

from backend.app.models import (
    EMBEDDING_DIMENSIONS,
    Course,
    DocumentChunk,
    Role,
    UploadedDocument,
    User,
)
from services.embeddings import (
    EmbeddingDimensionMismatchError,
    EmbeddingTimeoutError,
)
from services.semantic_retrieval import retrieve_course_chunks
from services.vector_store import (
    ChromaVectorStore,
    VectorRecord,
    VectorStoreError,
)

PROVIDER = "ollama"
MODEL = "nomic-embed-text"


def _directional(seed: float) -> list[float]:
    values = [0.0] * EMBEDDING_DIMENSIONS
    values[0] = 1.0
    values[1] = seed
    return values


QUERY = _directional(0.0)


class StubEmbeddingProvider:
    def __init__(
        self,
        *,
        query_vector: list[float] | None = None,
        error: Exception | None = None,
    ) -> None:
        self._query_vector = query_vector if query_vector is not None else QUERY
        self._error = error
        self.embed_query_calls: list[str] = []

    def embed_documents(self, texts):
        raise AssertionError("retrieval must never embed documents")

    def embed_query(self, text: str) -> list[float]:
        self.embed_query_calls.append(text)
        if self._error is not None:
            raise self._error
        return list(self._query_vector)


@pytest.fixture
def retrieval_store(tmp_path):
    store = ChromaVectorStore(persist_directory=str(tmp_path / "chroma"))
    yield store


def _seed_course(
    session: Session,
    *,
    email: str,
    chunk_count: int,
    course: Course | None = None,
) -> tuple[Course, UploadedDocument, list[DocumentChunk]]:
    if course is None:
        role = session.scalar(select(Role).where(Role.name == "user"))
        assert role is not None
        user = User(
            name="Retrieval user",
            email=email,
            password_hash="not-a-real-hash",
            role=role,
        )
        course = Course(title="Retrieval course", owner=user)
        session.add_all((user, course))
    document_id = uuid4()
    document = UploadedDocument(
        id=document_id,
        original_file_name="retrieval.txt",
        file_type="txt",
        mime_type="text/plain",
        file_size=64,
        file_hash=document_id.hex * 2,
        uploader=course.owner,
        course=course,
        storage_provider="local",
        storage_key=f"local/{document_id}.txt",
        status="ready",
    )
    chunks = [
        DocumentChunk(
            document=document,
            course=course,
            chunk_index=index,
            text=f"Chunk {index}",
        )
        for index in range(chunk_count)
    ]
    session.add_all([document, *chunks])
    session.flush()
    return course, document, chunks


def _replace(
    store: ChromaVectorStore,
    session: Session,
    document: UploadedDocument,
    chunks: list[DocumentChunk],
    seed: float = 0.2,
) -> None:
    store.replace_document_vectors(
        session,
        document_id=document.id,
        course_id=document.course_id,
        records=[
            VectorRecord(
                chunk_id=chunk.id,
                document_id=document.id,
                course_id=document.course_id,
                chunk_index=chunk.chunk_index,
                embedding=_directional(seed + index),
            )
            for index, chunk in enumerate(chunks)
        ],
        embedding_provider=PROVIDER,
        embedding_model=MODEL,
    )


def test_retrieval_returns_ranked_chunks_with_text(
    retrieval_store, session_factory: sessionmaker[Session]
) -> None:
    provider = StubEmbeddingProvider()
    with session_factory() as session:
        _, document, chunks = _seed_course(
            session, email="sr-rank@example.com", chunk_count=3
        )
        _replace(retrieval_store, session, document, chunks)
        session.commit()

        retrieved = retrieve_course_chunks(
            session,
            course_id=document.course_id,
            query="chunk query",
            limit=3,
            provider=provider,
            store=retrieval_store,
        )
        assert [chunk.chunk_id for chunk in retrieved] == [
            chunks[0].id,
            chunks[1].id,
            chunks[2].id,
        ]
        assert [chunk.text for chunk in retrieved] == ["Chunk 0", "Chunk 1", "Chunk 2"]
        assert [chunk.similarity for chunk in retrieved] == sorted(
            (chunk.similarity for chunk in retrieved), reverse=True
        )
        assert provider.embed_query_calls == ["chunk query"]


def test_retrieval_is_isolated_to_the_requested_course(
    retrieval_store, session_factory: sessionmaker[Session]
) -> None:
    provider = StubEmbeddingProvider()
    with session_factory() as session:
        course, document, chunks = _seed_course(
            session, email="sr-scope@example.com", chunk_count=2
        )
        _replace(retrieval_store, session, document, chunks)
        _, other, other_chunks = _seed_course(
            session, email="sr-other@example.com", chunk_count=2
        )
        _replace(retrieval_store, session, other, other_chunks, seed=0.0)
        session.commit()

        retrieved = retrieve_course_chunks(
            session,
            course_id=course.id,
            query="chunk query",
            limit=4,
            provider=provider,
            store=retrieval_store,
        )
        assert {chunk.course_id for chunk in retrieved} == {course.id}
        assert {chunk.chunk_id for chunk in retrieved} == {chunk.id for chunk in chunks}


def test_retrieval_skips_chunks_without_a_relational_row(
    retrieval_store, session_factory: sessionmaker[Session]
) -> None:
    provider = StubEmbeddingProvider()
    with session_factory() as session:
        _, document, chunks = _seed_course(
            session, email="sr-stale@example.com", chunk_count=3
        )
        _replace(retrieval_store, session, document, chunks)
        session.commit()

        session.delete(chunks[1])
        session.flush()

        retrieved = retrieve_course_chunks(
            session,
            course_id=document.course_id,
            query="chunk query",
            limit=3,
            provider=provider,
            store=retrieval_store,
        )
        assert [chunk.chunk_id for chunk in retrieved] == [
            chunks[0].id,
            chunks[2].id,
        ]
        session.rollback()


def test_retrieval_skips_chunks_without_text(
    retrieval_store, session_factory: sessionmaker[Session]
) -> None:
    provider = StubEmbeddingProvider()
    with session_factory() as session:
        _, document, chunks = _seed_course(
            session, email="sr-empty-text@example.com", chunk_count=2
        )
        chunks[1].text = ""
        _replace(retrieval_store, session, document, chunks)
        session.commit()

        retrieved = retrieve_course_chunks(
            session,
            course_id=document.course_id,
            query="chunk query",
            limit=3,
            provider=provider,
            store=retrieval_store,
        )
        assert [chunk.chunk_id for chunk in retrieved] == [chunks[0].id]


def test_retrieval_returns_nothing_for_a_course_without_vectors(
    retrieval_store, session_factory: sessionmaker[Session]
) -> None:
    provider = StubEmbeddingProvider()
    with session_factory() as session:
        course, _, _ = _seed_course(
            session, email="sr-empty@example.com", chunk_count=1
        )
        session.commit()

        assert (
            retrieve_course_chunks(
                session,
                course_id=course.id,
                query="chunk query",
                limit=3,
                provider=provider,
                store=retrieval_store,
            )
            == []
        )


def test_retrieval_rejects_a_blank_query(
    retrieval_store, session_factory: sessionmaker[Session]
) -> None:
    provider = StubEmbeddingProvider()
    with session_factory() as session:
        course, _, _ = _seed_course(
            session, email="sr-blank@example.com", chunk_count=1
        )
        with pytest.raises(ValueError):
            retrieve_course_chunks(
                session,
                course_id=course.id,
                query="   ",
                limit=3,
                provider=provider,
                store=retrieval_store,
            )


def test_retrieval_rejects_a_nonpositive_limit(
    retrieval_store, session_factory: sessionmaker[Session]
) -> None:
    provider = StubEmbeddingProvider()
    with session_factory() as session:
        course, _, _ = _seed_course(
            session, email="sr-limit@example.com", chunk_count=1
        )
        with pytest.raises(ValueError):
            retrieve_course_chunks(
                session,
                course_id=course.id,
                query="chunk query",
                limit=0,
                provider=provider,
                store=retrieval_store,
            )


def test_retrieval_propagates_embedding_errors(
    retrieval_store, session_factory: sessionmaker[Session]
) -> None:
    provider = StubEmbeddingProvider(error=EmbeddingTimeoutError())
    with session_factory() as session:
        course, _, _ = _seed_course(
            session, email="sr-embed-fail@example.com", chunk_count=1
        )
        with pytest.raises(EmbeddingTimeoutError):
            retrieve_course_chunks(
                session,
                course_id=course.id,
                query="chunk query",
                limit=3,
                provider=provider,
                store=retrieval_store,
            )


def test_retrieval_rejects_a_wrong_width_query_embedding(
    retrieval_store, session_factory: sessionmaker[Session]
) -> None:
    provider = StubEmbeddingProvider(query_vector=[0.1] * 10)
    with session_factory() as session:
        course, _, _ = _seed_course(
            session, email="sr-width@example.com", chunk_count=1
        )
        with pytest.raises(EmbeddingDimensionMismatchError):
            retrieve_course_chunks(
                session,
                course_id=course.id,
                query="chunk query",
                limit=3,
                provider=provider,
                store=retrieval_store,
            )


def test_retrieval_propagates_store_errors(
    retrieval_store, session_factory: sessionmaker[Session], monkeypatch
) -> None:
    class BrokenCollection:
        def query(self, **kwargs):
            raise RuntimeError("chroma exploded")

    monkeypatch.setattr(retrieval_store, "_get_collection", BrokenCollection)
    provider = StubEmbeddingProvider()
    with session_factory() as session:
        course, _, _ = _seed_course(
            session, email="sr-store-fail@example.com", chunk_count=1
        )
        with pytest.raises(VectorStoreError):
            retrieve_course_chunks(
                session,
                course_id=course.id,
                query="chunk query",
                limit=3,
                provider=provider,
                store=retrieval_store,
            )
