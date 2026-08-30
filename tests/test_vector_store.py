from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from backend.app.models import (
    ChunkEmbedding,
    Course,
    DocumentChunk,
    EMBEDDING_DIMENSIONS,
    Role,
    UploadedDocument,
    User,
)
import services.vector_store as vector_store
from services.vector_store import (
    ChromaVectorStore,
    PgVectorStore,
    VectorRecord,
    VectorStoreError,
    get_vector_store,
    reset_vector_store,
)

PROVIDER = "ollama"
MODEL = "nomic-embed-text"


def _vector(seed: float) -> list[float]:
    return [seed] * EMBEDDING_DIMENSIONS


@pytest.fixture
def chroma_store(tmp_path):
    store = ChromaVectorStore(persist_directory=str(tmp_path / "chroma"))
    yield store


@pytest.fixture
def pgvector_store():
    return PgVectorStore()


@pytest.fixture(params=["pgvector", "chroma"])
def store(request, tmp_path):
    if request.param == "pgvector":
        return PgVectorStore()
    return ChromaVectorStore(persist_directory=str(tmp_path / "chroma"))


def _seed_document(
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
            name="Vector user",
            email=email,
            password_hash="not-a-real-hash",
            role=role,
        )
        course = Course(title="Vector course", owner=user)
        session.add_all((user, course))
    document_id = uuid4()
    document = UploadedDocument(
        id=document_id,
        original_file_name="vectors.pdf",
        file_type="pdf",
        mime_type="application/pdf",
        file_size=128,
        file_hash=document_id.hex * 2,
        uploader=course.owner,
        course=course,
        storage_provider="local",
        storage_key=f"local/{document_id}.pdf",
        status="ready",
    )
    chunks = [
        DocumentChunk(
            document=document,
            course=course,
            chunk_index=index,
            page_number=index + 1,
            end_page_number=index + 1,
            text=f"Chunk {index}",
        )
        for index in range(chunk_count)
    ]
    session.add_all([document, *chunks])
    session.flush()
    return course, document, chunks


def _records(chunks, document: UploadedDocument, seed: float) -> list[VectorRecord]:
    return [
        VectorRecord(
            chunk_id=chunk.id,
            document_id=document.id,
            course_id=document.course_id,
            chunk_index=chunk.chunk_index,
            embedding=_vector(seed + index),
        )
        for index, chunk in enumerate(chunks)
    ]


def _replace(store, session, document, chunks, seed=0.1) -> None:
    store.replace_document_vectors(
        session,
        document_id=document.id,
        course_id=document.course_id,
        records=_records(chunks, document, seed),
        embedding_provider=PROVIDER,
        embedding_model=MODEL,
    )


def test_replace_stores_one_vector_per_chunk(
    store, session_factory: sessionmaker[Session]
) -> None:
    with session_factory() as session:
        _, document, chunks = _seed_document(
            session, email="vs-create@example.com", chunk_count=3
        )
        _replace(store, session, document, chunks)
        session.commit()

        assert store.count_document_vectors(session, document.id) == 3
        assert store.chunk_ids_with_vectors(
            session, document.id, embedding_model=MODEL
        ) == {chunk.id for chunk in chunks}


def test_replace_is_idempotent_for_the_same_chunks(
    store, session_factory: sessionmaker[Session]
) -> None:
    with session_factory() as session:
        _, document, chunks = _seed_document(
            session, email="vs-idempotent@example.com", chunk_count=3
        )
        _replace(store, session, document, chunks)
        session.commit()
        _replace(store, session, document, chunks, seed=0.9)
        session.commit()

        assert store.count_document_vectors(session, document.id) == 3


def test_replace_with_fewer_chunks_drops_the_stale_vectors(
    store, session_factory: sessionmaker[Session]
) -> None:
    """Reprocessing that yields fewer chunks must not leave orphans behind."""
    with session_factory() as session:
        _, document, chunks = _seed_document(
            session, email="vs-shrink@example.com", chunk_count=5
        )
        _replace(store, session, document, chunks)
        session.commit()
        assert store.count_document_vectors(session, document.id) == 5

        surviving = chunks[:2]
        for stale in chunks[2:]:
            session.delete(stale)
        session.flush()
        _replace(store, session, document, surviving)
        session.commit()

        assert store.count_document_vectors(session, document.id) == 2
        assert store.chunk_ids_with_vectors(
            session, document.id, embedding_model=MODEL
        ) == {chunk.id for chunk in surviving}


def test_replace_with_more_chunks_covers_every_new_chunk(
    store, session_factory: sessionmaker[Session]
) -> None:
    with session_factory() as session:
        _, document, chunks = _seed_document(
            session, email="vs-grow@example.com", chunk_count=2
        )
        _replace(store, session, document, chunks)
        session.commit()

        extra = [
            DocumentChunk(
                document=document,
                course=document.course,
                chunk_index=index,
                page_number=index + 1,
                end_page_number=index + 1,
                text=f"Chunk {index}",
            )
            for index in (2, 3)
        ]
        session.add_all(extra)
        session.flush()
        _replace(store, session, document, chunks + extra)
        session.commit()

        assert store.count_document_vectors(session, document.id) == 4


def test_delete_document_vectors_removes_only_that_document(
    store, session_factory: sessionmaker[Session]
) -> None:
    with session_factory() as session:
        course, first, first_chunks = _seed_document(
            session, email="vs-doc-delete@example.com", chunk_count=2
        )
        _, second, second_chunks = _seed_document(
            session, email="unused@example.com", chunk_count=3, course=course
        )
        _replace(store, session, first, first_chunks)
        _replace(store, session, second, second_chunks)
        session.commit()

        store.delete_document_vectors(session, first.id)
        session.commit()

        assert store.count_document_vectors(session, first.id) == 0
        assert store.count_document_vectors(session, second.id) == 3


def test_delete_course_vectors_removes_every_document_in_the_course(
    store, session_factory: sessionmaker[Session]
) -> None:
    with session_factory() as session:
        course, first, first_chunks = _seed_document(
            session, email="vs-course-delete@example.com", chunk_count=2
        )
        _, second, second_chunks = _seed_document(
            session, email="unused@example.com", chunk_count=3, course=course
        )
        _replace(store, session, first, first_chunks)
        _replace(store, session, second, second_chunks)
        session.commit()

        store.delete_course_vectors(session, course.id)
        session.commit()

        assert store.count_course_vectors(session, course.id) == 0


def test_delete_is_safe_when_nothing_is_stored(
    store, session_factory: sessionmaker[Session]
) -> None:
    with session_factory() as session:
        store.delete_document_vectors(session, uuid4())
        store.delete_course_vectors(session, 987654)
        session.commit()

        assert store.count_document_vectors(session, uuid4()) == 0


def test_replace_rejects_a_record_from_another_document(
    store, session_factory: sessionmaker[Session]
) -> None:
    with session_factory() as session:
        _, document, chunks = _seed_document(
            session, email="vs-foreign@example.com", chunk_count=1
        )
        foreign = [
            VectorRecord(
                chunk_id=chunks[0].id,
                document_id=uuid4(),
                course_id=document.course_id,
                chunk_index=0,
                embedding=_vector(0.1),
            )
        ]

        with pytest.raises(ValueError):
            store.replace_document_vectors(
                session,
                document_id=document.id,
                course_id=document.course_id,
                records=foreign,
                embedding_provider=PROVIDER,
                embedding_model=MODEL,
            )


def test_pgvector_store_records_provider_attribution(
    pgvector_store, session_factory: sessionmaker[Session]
) -> None:
    with session_factory() as session:
        _, document, chunks = _seed_document(
            session, email="vs-attribution@example.com", chunk_count=1
        )
        _replace(pgvector_store, session, document, chunks)
        session.commit()

        stored = session.scalar(
            select(ChunkEmbedding).where(ChunkEmbedding.document_id == document.id)
        )
        assert stored is not None
        assert stored.embedding_provider == PROVIDER
        assert stored.embedding_model == MODEL
        assert stored.dimensions == EMBEDDING_DIMENSIONS
        assert stored.chunk_index == 0


def test_chroma_records_the_required_metadata(
    chroma_store, session_factory: sessionmaker[Session]
) -> None:
    with session_factory() as session:
        _, document, chunks = _seed_document(
            session, email="vs-chroma-meta@example.com", chunk_count=1
        )
        _replace(chroma_store, session, document, chunks)
        session.commit()

        metadata = chroma_store.metadata_for_chunk(chunks[0].id)
        assert metadata["chunk_id"] == chunks[0].id
        assert metadata["document_id"] == str(document.id)
        assert metadata["course_id"] == document.course_id
        assert metadata["chunk_index"] == 0
        assert metadata["embedding_provider"] == PROVIDER
        assert metadata["embedding_model"] == MODEL


def test_chroma_vectors_survive_a_client_restart(
    tmp_path, session_factory: sessionmaker[Session]
) -> None:
    """Durability is the whole point of a persistent vector store."""
    persist_directory = str(tmp_path / "chroma")
    with session_factory() as session:
        _, document, chunks = _seed_document(
            session, email="vs-restart@example.com", chunk_count=4
        )
        first = ChromaVectorStore(persist_directory=persist_directory)
        _replace(first, session, document, chunks)
        session.commit()
        assert first.count_document_vectors(session, document.id) == 4
        first.close()

        restarted = ChromaVectorStore(persist_directory=persist_directory)
        assert restarted.count_document_vectors(session, document.id) == 4
        assert restarted.chunk_ids_with_vectors(
            session, document.id, embedding_model=MODEL
        ) == {chunk.id for chunk in chunks}


def test_chroma_ignores_a_legacy_collection_of_a_superseded_width(
    tmp_path, session_factory: sessionmaker[Session]
) -> None:
    """A store left at 768 by an earlier model must not block writes at the current width."""
    import chromadb

    if EMBEDDING_DIMENSIONS == 768:
        pytest.skip("The superseded width is the current one.")

    persist_directory = str(tmp_path / "chroma")
    legacy = chromadb.PersistentClient(path=persist_directory)
    legacy.get_or_create_collection(
        name="lumina_chunks",
        embedding_function=None,
        configuration={"hnsw": {"space": vector_store.SIMILARITY_METRIC}},
    ).upsert(
        ids=["1"],
        embeddings=[[0.1] * 768],
        metadatas=[
            {"document_id": str(uuid4()), "embedding_model": "gemini-embedding-001"}
        ],
    )

    with session_factory() as session:
        _, document, chunks = _seed_document(
            session, email="vs-width@example.com", chunk_count=2
        )
        store = ChromaVectorStore(persist_directory=persist_directory)
        _replace(store, session, document, chunks)
        session.commit()
        assert store.count_document_vectors(session, document.id) == 2

    assert legacy.get_collection("lumina_chunks").count() == 1


def test_chroma_recovers_when_the_collection_is_rebuilt_underneath_it(
    tmp_path, session_factory: sessionmaker[Session]
) -> None:
    """A rebuilt store must not break search until the API process restarts."""
    import chromadb

    persist_directory = str(tmp_path / "chroma")
    with session_factory() as session:
        _, document, chunks = _seed_document(
            session, email="vs-stale@example.com", chunk_count=3
        )
        store = ChromaVectorStore(persist_directory=persist_directory)
        _replace(store, session, document, chunks)
        session.commit()
        assert store.count_document_vectors(session, document.id) == 3

        rebuilder = chromadb.PersistentClient(path=persist_directory)
        rebuilder.delete_collection(vector_store.CHROMA_COLLECTION_NAME)
        rebuilder.get_or_create_collection(
            name=vector_store.CHROMA_COLLECTION_NAME,
            embedding_function=None,
            configuration={"hnsw": {"space": vector_store.SIMILARITY_METRIC}},
        )

        assert store.count_document_vectors(session, document.id) == 0

        _replace(store, session, document, chunks)
        assert store.count_document_vectors(session, document.id) == 3


def test_chroma_reopens_a_handle_another_process_invalidated(
    tmp_path, session_factory: sessionmaker[Session]
) -> None:
    """A worker writing to the same store must not break reads until restart."""

    class StaleCollection:
        def __init__(self) -> None:
            self.calls = 0

        def get(self, **kwargs):
            self.calls += 1
            raise RuntimeError("Error executing plan: Internal error: Error finding id")

    with session_factory() as session:
        _, document, chunks = _seed_document(
            session, email="vs-reopen@example.com", chunk_count=3
        )
        store = ChromaVectorStore(persist_directory=str(tmp_path / "chroma"))
        _replace(store, session, document, chunks)
        session.commit()
        assert store.count_document_vectors(session, document.id) == 3

        stale = StaleCollection()
        store._collection = stale

        assert store.count_document_vectors(session, document.id) == 3
        assert stale.calls == 1


def test_chroma_reopen_clears_the_shared_system_cache(
    tmp_path, session_factory: sessionmaker[Session], monkeypatch
) -> None:
    """Chroma hands back a cached system, so a reopen must clear it to see writes."""
    from chromadb.api.client import SharedSystemClient

    cleared: list[bool] = []
    monkeypatch.setattr(
        SharedSystemClient,
        "clear_system_cache",
        classmethod(lambda cls: cleared.append(True)),
    )

    class StaleCollection:
        def get(self, **kwargs):
            raise RuntimeError("Error executing plan: Internal error: Error finding id")

    with session_factory() as session:
        _, document, chunks = _seed_document(
            session, email="vs-cache@example.com", chunk_count=2
        )
        store = ChromaVectorStore(persist_directory=str(tmp_path / "chroma"))
        _replace(store, session, document, chunks)
        session.commit()

        store._collection = StaleCollection()
        assert store.count_document_vectors(session, document.id) == 2

    assert cleared


def test_chroma_wraps_client_failures_as_vector_store_errors(
    chroma_store, session_factory: sessionmaker[Session], monkeypatch
) -> None:
    class BrokenCollection:
        def delete(self, **kwargs):
            raise RuntimeError("chroma exploded")

        def upsert(self, **kwargs):
            raise RuntimeError("chroma exploded")

        def get(self, **kwargs):
            raise RuntimeError("chroma exploded")

        def count(self, **kwargs):
            raise RuntimeError("chroma exploded")

    monkeypatch.setattr(chroma_store, "_get_collection", BrokenCollection)

    with session_factory() as session:
        with pytest.raises(VectorStoreError):
            chroma_store.delete_document_vectors(session, uuid4())
        with pytest.raises(VectorStoreError):
            chroma_store.count_document_vectors(session, uuid4())


def test_factory_honours_the_configured_backend(monkeypatch, tmp_path) -> None:
    reset_vector_store()
    monkeypatch.setattr(
        vector_store,
        "settings",
        SimpleNamespace(
            vector_backend="pgvector",
            chroma_persist_directory=str(tmp_path / "chroma"),
        ),
    )
    assert isinstance(get_vector_store(), PgVectorStore)

    reset_vector_store()
    monkeypatch.setattr(
        vector_store,
        "settings",
        SimpleNamespace(
            vector_backend="chroma",
            chroma_persist_directory=str(tmp_path / "chroma"),
        ),
    )
    assert isinstance(get_vector_store(), ChromaVectorStore)
    reset_vector_store()


def test_factory_rejects_an_unknown_backend(monkeypatch, tmp_path) -> None:
    reset_vector_store()
    monkeypatch.setattr(
        vector_store,
        "settings",
        SimpleNamespace(
            vector_backend="faiss",
            chroma_persist_directory=str(tmp_path / "chroma"),
        ),
    )

    with pytest.raises(VectorStoreError):
        get_vector_store()
    reset_vector_store()


def test_vector_record_rejects_a_wrong_width_embedding() -> None:
    with pytest.raises(ValueError):
        VectorRecord(
            chunk_id=1,
            document_id=UUID(int=1),
            course_id=1,
            chunk_index=0,
            embedding=[0.1] * 10,
        ).validate()


def _directional(seed: float) -> list[float]:
    values = [0.0] * EMBEDDING_DIMENSIONS
    values[0] = 1.0
    values[1] = seed
    return values


def _directional_records(
    chunks, document: UploadedDocument, seed: float
) -> list[VectorRecord]:
    return [
        VectorRecord(
            chunk_id=chunk.id,
            document_id=document.id,
            course_id=document.course_id,
            chunk_index=chunk.chunk_index,
            embedding=_directional(seed + index),
        )
        for index, chunk in enumerate(chunks)
    ]


def _replace_directional(store, session, document, chunks, seed=0.2) -> None:
    store.replace_document_vectors(
        session,
        document_id=document.id,
        course_id=document.course_id,
        records=_directional_records(chunks, document, seed),
        embedding_provider=PROVIDER,
        embedding_model=MODEL,
    )


QUERY = _directional(0.0)


def test_search_ranks_hits_by_cosine_similarity(
    chroma_store, session_factory: sessionmaker[Session]
) -> None:
    with session_factory() as session:
        _, document, chunks = _seed_document(
            session, email="vs-search-rank@example.com", chunk_count=3
        )
        _replace_directional(chroma_store, session, document, chunks)
        session.commit()

        ranked = chroma_store.search(
            session,
            course_id=document.course_id,
            query_embedding=QUERY,
            limit=3,
            embedding_model=MODEL,
        )
        assert [result.chunk_id for result in ranked] == [
            chunks[0].id,
            chunks[1].id,
            chunks[2].id,
        ]
        assert [result.similarity for result in ranked] == sorted(
            (result.similarity for result in ranked), reverse=True
        )
        assert ranked[0].chunk_index == 0
        assert ranked[0].document_id == document.id
        assert ranked[0].course_id == document.course_id


def test_search_never_leaks_another_course(
    chroma_store, session_factory: sessionmaker[Session]
) -> None:
    with session_factory() as session:
        course, document, chunks = _seed_document(
            session, email="vs-search-scope@example.com", chunk_count=2
        )
        _replace_directional(chroma_store, session, document, chunks)
        _, other, other_chunks = _seed_document(
            session, email="vs-search-other@example.com", chunk_count=2
        )
        _replace_directional(chroma_store, session, other, other_chunks, seed=0.0)
        session.commit()

        ranked = chroma_store.search(
            session,
            course_id=course.id,
            query_embedding=QUERY,
            limit=4,
            embedding_model=MODEL,
        )
        assert {result.course_id for result in ranked} == {course.id}
        assert {result.chunk_id for result in ranked} == {chunk.id for chunk in chunks}


def test_search_honours_the_limit(
    chroma_store, session_factory: sessionmaker[Session]
) -> None:
    with session_factory() as session:
        _, document, chunks = _seed_document(
            session, email="vs-search-limit@example.com", chunk_count=3
        )
        _replace_directional(chroma_store, session, document, chunks)
        session.commit()

        ranked = chroma_store.search(
            session,
            course_id=document.course_id,
            query_embedding=QUERY,
            limit=2,
            embedding_model=MODEL,
        )
        assert len(ranked) == 2
        assert ranked[0].chunk_id == chunks[0].id


def test_search_refreshes_a_stale_index_that_returns_nothing(
    chroma_store, session_factory: sessionmaker[Session]
) -> None:
    """A reader whose in-memory HNSW index lags a writer must not report a
    populated course as unindexed: an empty hit triggers one reopen."""

    class StaleCollection:
        def __init__(self, ids: list[str]) -> None:
            self._ids = ids
            self.queries = 0

        def query(self, **_kwargs):
            self.queries += 1
            return {"ids": [[]], "metadatas": [[]], "distances": [[]]}

        def get(self, **_kwargs):
            # count_course_vectors reads the metadata segment, which stays fresh.
            return {"ids": list(self._ids), "metadatas": [{} for _ in self._ids]}

    with session_factory() as session:
        _, document, chunks = _seed_document(
            session, email="vs-search-stale@example.com", chunk_count=3
        )
        _replace_directional(chroma_store, session, document, chunks)
        session.commit()

        stale = StaleCollection([str(chunk.id) for chunk in chunks])
        chroma_store._collection = stale

        ranked = chroma_store.search(
            session,
            course_id=document.course_id,
            query_embedding=QUERY,
            limit=3,
            embedding_model=MODEL,
        )

    assert stale.queries == 1  # the stale handle answered once, then was dropped
    assert {result.chunk_id for result in ranked} == {chunk.id for chunk in chunks}


def test_search_returns_nothing_for_a_course_without_vectors(
    chroma_store, session_factory: sessionmaker[Session], monkeypatch
) -> None:
    reopened = 0

    def count_reopen() -> None:
        nonlocal reopened
        reopened += 1
        ChromaVectorStore.close(chroma_store)

    monkeypatch.setattr(chroma_store, "_discard_client", count_reopen)

    with session_factory() as session:
        course, _, _ = _seed_document(
            session, email="vs-search-empty@example.com", chunk_count=1
        )
        session.commit()

        assert (
            chroma_store.search(
                session,
                course_id=course.id,
                query_embedding=QUERY,
                limit=3,
                embedding_model=MODEL,
            )
            == []
        )

    # A truly unindexed course must not pay a client reopen on every miss.
    assert reopened == 0


def test_search_rejects_a_wrong_width_embedding(
    store, session_factory: sessionmaker[Session]
) -> None:
    with session_factory() as session:
        course, _, _ = _seed_document(
            session, email="vs-search-width@example.com", chunk_count=1
        )
        with pytest.raises(ValueError):
            store.search(
                session,
                course_id=course.id,
                query_embedding=[0.1] * 10,
                limit=3,
                embedding_model=MODEL,
            )


def test_search_rejects_a_nonpositive_limit(
    store, session_factory: sessionmaker[Session]
) -> None:
    with session_factory() as session:
        course, _, _ = _seed_document(
            session, email="vs-search-limit-zero@example.com", chunk_count=1
        )
        with pytest.raises(ValueError):
            store.search(
                session,
                course_id=course.id,
                query_embedding=QUERY,
                limit=0,
                embedding_model=MODEL,
            )


def test_pgvector_search_requires_postgresql(
    pgvector_store, session_factory: sessionmaker[Session]
) -> None:
    with session_factory() as session:
        course, _, _ = _seed_document(
            session, email="vs-search-dialect@example.com", chunk_count=1
        )
        with pytest.raises(VectorStoreError):
            pgvector_store.search(
                session,
                course_id=course.id,
                query_embedding=QUERY,
                limit=3,
                embedding_model=MODEL,
            )


def test_chroma_wraps_search_failures_as_vector_store_errors(
    chroma_store, session_factory: sessionmaker[Session], monkeypatch
) -> None:
    class BrokenCollection:
        def query(self, **kwargs):
            raise RuntimeError("chroma exploded")

    monkeypatch.setattr(chroma_store, "_get_collection", BrokenCollection)

    with session_factory() as session:
        course, _, _ = _seed_document(
            session, email="vs-search-broken@example.com", chunk_count=1
        )
        with pytest.raises(VectorStoreError):
            chroma_store.search(
                session,
                course_id=course.id,
                query_embedding=QUERY,
                limit=3,
                embedding_model=MODEL,
            )
