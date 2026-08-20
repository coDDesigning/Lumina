"""Course-isolated retrieval assembled into bounded prompt material.

These tests pin the contract study guide generation depends on: only retrieved
chunks reach the prompt, anything below the similarity floor is discarded, the
character budget still bounds the result, and a failure is never widened back
into whole-corpus assembly.
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models import (
    EMBEDDING_DIMENSIONS,
    Course,
    DocumentChunk,
    Role,
    UploadedDocument,
    User,
)
from services.course_material import CHUNK_SEPARATOR, CourseMaterial
from services.embeddings import (
    EmbeddingAuthError,
    EmbeddingConfigurationError,
    EmbeddingConnectionError,
    EmbeddingDimensionMismatchError,
    EmbeddingInvalidResponseError,
    EmbeddingProviderError,
    EmbeddingRateLimitError,
    EmbeddingTimeoutError,
)
from services.retrieval_material import (
    MaterialNotIndexedError,
    MaterialRetrievalError,
    MaterialRetrievalRateLimitError,
    MaterialRetrievalTimeoutError,
    NoRelevantMaterialError,
    RetrievedCourseMaterial,
    load_retrieved_material,
)
from services.vector_store import (
    ChromaVectorStore,
    SearchResult,
    VectorRecord,
    VectorStoreConfigurationError,
    VectorStoreError,
)

PROVIDER = "ollama"
MODEL = "nomic-embed-text"

BUDGET = 100_000

# Raw failure text can name hosts, models and payloads, so it must never survive
# translation into a curated error.
SECRET = "embed-host-9000 leaked internal detail"


def _directional(seed: float) -> list[float]:
    values = [0.0] * EMBEDDING_DIMENSIONS
    values[0] = 1.0
    values[1] = seed
    return values


QUERY_VECTOR = _directional(0.0)


class StubEmbeddingProvider:
    def __init__(self, *, error: Exception | None = None) -> None:
        self._error = error
        self.embed_query_calls: list[str] = []

    def embed_documents(self, texts):
        raise AssertionError("retrieval must never embed documents")

    def embed_query(self, text: str) -> list[float]:
        self.embed_query_calls.append(text)
        if self._error is not None:
            raise self._error
        return list(QUERY_VECTOR)


class StubVectorStore:
    """A store with exactly the similarities a test wants to reason about."""

    def __init__(
        self,
        results: list[SearchResult] | None = None,
        *,
        error: Exception | None = None,
    ) -> None:
        self._results = results or []
        self._error = error
        self.search_calls: list[dict] = []

    def search(self, session, *, course_id, query_embedding, limit):
        self.search_calls.append({"course_id": course_id, "limit": limit})
        if self._error is not None:
            raise self._error
        return self._results[:limit]


@pytest.fixture
def retrieval_store(tmp_path):
    return ChromaVectorStore(persist_directory=str(tmp_path / "chroma"))


def _seed_course(
    session: Session,
    *,
    email: str,
    texts: list[str],
    course: Course | None = None,
    created_at: datetime | None = None,
    status: str = "ready",
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
        status=status,
    )
    if created_at is not None:
        document.created_at = created_at
    chunks = [
        DocumentChunk(
            document=document,
            course=course,
            chunk_index=index,
            text=text,
        )
        for index, text in enumerate(texts)
    ]
    session.add_all([document, *chunks])
    session.flush()
    return course, document, chunks


def _index(
    store: ChromaVectorStore,
    session: Session,
    document: UploadedDocument,
    chunks: list[DocumentChunk],
    seeds: list[float],
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
                embedding=_directional(seed),
            )
            for chunk, seed in zip(chunks, seeds, strict=True)
        ],
        embedding_provider=PROVIDER,
        embedding_model=MODEL,
    )


def _load(db: Session, course_id: int, **overrides):
    arguments = {
        "query": "retrieval query",
        "limit": 10,
        "min_similarity": 0.0,
        "max_characters": BUDGET,
        "provider": StubEmbeddingProvider(),
    }
    arguments.update(overrides)
    return load_retrieved_material(db, course_id, **arguments)


def test_returns_only_the_retrieved_chunks_of_the_requested_course(
    db_session: Session, retrieval_store: ChromaVectorStore
) -> None:
    course, document, chunks = _seed_course(
        db_session, email="owner-a@example.com", texts=["alpha", "beta"]
    )
    _other, other_document, other_chunks = _seed_course(
        db_session, email="owner-b@example.com", texts=["forbidden"]
    )
    _index(retrieval_store, db_session, document, chunks, [0.0, 0.1])
    _index(retrieval_store, db_session, other_document, other_chunks, [0.0])

    material = _load(db_session, course.id, store=retrieval_store)

    assert "alpha" in material.text
    assert "beta" in material.text
    assert "forbidden" not in material.text
    assert material.chunks_used == 2


def test_result_is_a_course_material_so_existing_reporting_keeps_working(
    db_session: Session, retrieval_store: ChromaVectorStore
) -> None:
    course, document, chunks = _seed_course(
        db_session, email="shape@example.com", texts=["alpha"]
    )
    _index(retrieval_store, db_session, document, chunks, [0.0])

    material = _load(db_session, course.id, store=retrieval_store)

    assert isinstance(material, RetrievedCourseMaterial)
    assert isinstance(material, CourseMaterial)
    assert not material.is_empty


def test_chunks_available_still_counts_every_ready_chunk_in_the_course(
    db_session: Session, retrieval_store: ChromaVectorStore
) -> None:
    """Coverage reporting stays truthful about the corpus, not the retrieved subset."""
    course, document, chunks = _seed_course(
        db_session, email="available@example.com", texts=["alpha", "beta", "gamma"]
    )
    _index(retrieval_store, db_session, document, chunks, [0.0, 0.1, 0.2])

    material = _load(db_session, course.id, store=retrieval_store, limit=1)

    assert material.chunks_used == 1
    assert material.chunks_retrieved == 1
    assert material.chunks_available == 3


def test_drops_chunks_below_the_similarity_floor(
    db_session: Session, retrieval_store: ChromaVectorStore
) -> None:
    course, document, chunks = _seed_course(
        db_session, email="floor@example.com", texts=["relevant", "unrelated"]
    )
    # cosine([1,0], [1,s]) is 1/sqrt(1+s^2): seed 0.0 gives 1.00, seed 4.0 gives 0.24.
    _index(retrieval_store, db_session, document, chunks, [0.0, 4.0])

    material = _load(db_session, course.id, store=retrieval_store, min_similarity=0.5)

    assert "relevant" in material.text
    assert "unrelated" not in material.text
    assert material.chunks_ranked == 2
    assert material.chunks_retrieved == 1
    assert material.lowest_similarity is not None
    assert material.lowest_similarity >= 0.5


def test_raises_when_nothing_clears_the_similarity_floor(
    db_session: Session, retrieval_store: ChromaVectorStore
) -> None:
    course, document, chunks = _seed_course(
        db_session, email="nomatch@example.com", texts=["unrelated"]
    )
    _index(retrieval_store, db_session, document, chunks, [4.0])

    with pytest.raises(NoRelevantMaterialError):
        _load(db_session, course.id, store=retrieval_store, min_similarity=0.9)


def test_raises_when_the_course_has_no_indexed_material(
    db_session: Session, retrieval_store: ChromaVectorStore
) -> None:
    """Ready chunks with no vectors is an indexing gap, not a relevance miss.

    Reporting it as a relevance miss tells the student to broaden a topic that
    was never the problem, so it has to be its own error.
    """
    course, _document, _chunks = _seed_course(
        db_session, email="novectors@example.com", texts=["alpha"]
    )

    with pytest.raises(MaterialNotIndexedError):
        _load(db_session, course.id, store=retrieval_store)


def test_a_relevance_miss_is_not_reported_as_an_indexing_gap(
    db_session: Session, retrieval_store: ChromaVectorStore
) -> None:
    course, document, chunks = _seed_course(
        db_session, email="stillindexed@example.com", texts=["unrelated"]
    )
    _index(retrieval_store, db_session, document, chunks, [4.0])

    with pytest.raises(NoRelevantMaterialError) as excinfo:
        _load(db_session, course.id, store=retrieval_store, min_similarity=0.9)

    assert not isinstance(excinfo.value, MaterialNotIndexedError)


def test_skips_chunks_whose_document_is_not_ready(
    db_session: Session, retrieval_store: ChromaVectorStore
) -> None:
    """A document being reprocessed keeps its old chunks and vectors until the new
    run commits, so retrieval must apply the same ready-only rule the whole-corpus
    assembler does or a study guide can quote material that is being replaced."""
    course, ready_document, ready_chunks = _seed_course(
        db_session, email="ready@example.com", texts=["ready-material"]
    )
    _course, stale_document, stale_chunks = _seed_course(
        db_session,
        email="ready@example.com",
        texts=["stale-material"],
        course=course,
        status="processing",
    )
    _index(retrieval_store, db_session, ready_document, ready_chunks, [0.1])
    _index(retrieval_store, db_session, stale_document, stale_chunks, [0.0])

    material = _load(db_session, course.id, store=retrieval_store)

    assert "ready-material" in material.text
    assert "stale-material" not in material.text
    assert material.chunks_used == 1
    assert material.chunks_used <= material.chunks_available


@pytest.mark.parametrize("status", ["uploaded", "processing", "failed", "deleting"])
def test_raises_when_every_match_belongs_to_an_unready_document(
    db_session: Session, retrieval_store: ChromaVectorStore, status: str
) -> None:
    course, document, chunks = _seed_course(
        db_session,
        email=f"unready-{status}@example.com",
        texts=["stale-material"],
        status=status,
    )
    _index(retrieval_store, db_session, document, chunks, [0.0])

    with pytest.raises(NoRelevantMaterialError):
        _load(db_session, course.id, store=retrieval_store)


def test_skips_hits_whose_chunk_row_is_gone(db_session: Session) -> None:
    """A vector surviving its chunk must not resurrect deleted material."""
    course, document, chunks = _seed_course(
        db_session, email="ghost@example.com", texts=["alpha"]
    )
    store = StubVectorStore(
        [
            SearchResult(
                chunk_id=chunks[0].id,
                document_id=document.id,
                course_id=course.id,
                chunk_index=0,
                similarity=0.9,
            ),
            SearchResult(
                chunk_id=chunks[0].id + 9_999,
                document_id=document.id,
                course_id=course.id,
                chunk_index=1,
                similarity=0.8,
            ),
        ]
    )

    material = _load(db_session, course.id, store=store)

    assert material.text == "alpha"
    assert material.chunks_used == 1


def test_bounds_the_material_to_the_character_budget(
    db_session: Session, retrieval_store: ChromaVectorStore
) -> None:
    course, document, chunks = _seed_course(
        db_session, email="budget@example.com", texts=["a" * 50, "b" * 50, "c" * 50]
    )
    _index(retrieval_store, db_session, document, chunks, [0.0, 0.1, 0.2])

    material = _load(db_session, course.id, store=retrieval_store, max_characters=110)

    assert len(material.text) <= 110
    assert material.truncated is True
    assert material.chunks_used == 2
    assert material.chunks_retrieved == 3


def test_reports_truncation_only_for_the_character_budget(
    db_session: Session, retrieval_store: ChromaVectorStore
) -> None:
    """Retrieval narrowing the corpus is the normal case, not a truncation."""
    course, document, chunks = _seed_course(
        db_session, email="narrow@example.com", texts=["alpha", "beta", "gamma"]
    )
    _index(retrieval_store, db_session, document, chunks, [0.0, 0.1, 0.2])

    material = _load(db_session, course.id, store=retrieval_store, limit=1)

    assert material.chunks_used == 1
    assert material.chunks_available == 3
    assert material.truncated is False


def test_emits_retained_chunks_in_corpus_order(
    db_session: Session, retrieval_store: ChromaVectorStore
) -> None:
    """The budget spends on relevance, but the prompt reads in document order."""
    earlier = datetime.now(UTC) - timedelta(days=1)
    course, first_document, first_chunks = _seed_course(
        db_session,
        email="order@example.com",
        texts=["earlier-one", "earlier-two"],
        created_at=earlier,
    )
    _course, later_document, later_chunks = _seed_course(
        db_session,
        email="order@example.com",
        texts=["later-one"],
        course=course,
        created_at=datetime.now(UTC),
    )
    # The later document is the best match; corpus order must still win emission.
    _index(retrieval_store, db_session, first_document, first_chunks, [0.5, 0.4])
    _index(retrieval_store, db_session, later_document, later_chunks, [0.0])

    material = _load(db_session, course.id, store=retrieval_store)

    assert material.text == CHUNK_SEPARATOR.join(
        ["earlier-one", "earlier-two", "later-one"]
    )


def test_is_byte_identical_for_identical_course_state(
    db_session: Session, retrieval_store: ChromaVectorStore
) -> None:
    course, document, chunks = _seed_course(
        db_session, email="stable@example.com", texts=["alpha", "beta", "gamma"]
    )
    _index(retrieval_store, db_session, document, chunks, [0.0, 0.1, 0.2])

    first = _load(db_session, course.id, store=retrieval_store)
    second = _load(db_session, course.id, store=retrieval_store)

    assert first.text == second.text


def test_passes_the_configured_limit_and_course_scope_to_the_store(
    db_session: Session,
) -> None:
    course, document, chunks = _seed_course(
        db_session, email="scope@example.com", texts=["alpha"]
    )
    store = StubVectorStore(
        [
            SearchResult(
                chunk_id=chunks[0].id,
                document_id=document.id,
                course_id=course.id,
                chunk_index=0,
                similarity=0.9,
            )
        ]
    )

    _load(db_session, course.id, store=store, limit=7)

    assert store.search_calls == [{"course_id": course.id, "limit": 7}]


def test_embeds_the_supplied_query_and_never_the_documents(
    db_session: Session, retrieval_store: ChromaVectorStore
) -> None:
    course, document, chunks = _seed_course(
        db_session, email="query@example.com", texts=["alpha"]
    )
    _index(retrieval_store, db_session, document, chunks, [0.0])
    provider = StubEmbeddingProvider()

    _load(
        db_session,
        course.id,
        store=retrieval_store,
        provider=provider,
        query="binary search trees",
    )

    assert provider.embed_query_calls == ["binary search trees"]


@pytest.mark.parametrize(
    ("overrides", "match"),
    [
        ({"query": "   "}, "query"),
        ({"query": ""}, "query"),
        ({"limit": 0}, "limit"),
        ({"limit": -1}, "limit"),
        ({"min_similarity": -0.1}, "similarity"),
        ({"min_similarity": 1.1}, "similarity"),
        ({"max_characters": 0}, "max_characters"),
    ],
)
def test_rejects_impossible_arguments(
    db_session: Session, overrides: dict, match: str
) -> None:
    course, _document, _chunks = _seed_course(
        db_session, email="args@example.com", texts=["alpha"]
    )

    with pytest.raises(ValueError, match=match):
        _load(db_session, course.id, store=StubVectorStore(), **overrides)


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (EmbeddingTimeoutError(SECRET), MaterialRetrievalTimeoutError),
        (EmbeddingRateLimitError(SECRET), MaterialRetrievalRateLimitError),
        (EmbeddingConnectionError(SECRET), MaterialRetrievalError),
        (EmbeddingProviderError(SECRET), MaterialRetrievalError),
        (EmbeddingInvalidResponseError(SECRET), MaterialRetrievalError),
        (EmbeddingDimensionMismatchError(SECRET), MaterialRetrievalError),
        (EmbeddingAuthError(SECRET), MaterialRetrievalError),
        (EmbeddingConfigurationError(SECRET), MaterialRetrievalError),
    ],
)
def test_translates_embedding_failures(
    db_session: Session, failure: Exception, expected: type[Exception]
) -> None:
    course, _document, _chunks = _seed_course(
        db_session, email="embedfail@example.com", texts=["alpha"]
    )

    with pytest.raises(expected) as caught:
        _load(
            db_session,
            course.id,
            store=StubVectorStore(),
            provider=StubEmbeddingProvider(error=failure),
        )

    assert caught.value.__cause__ is failure
    assert str(failure) not in str(caught.value)


@pytest.mark.parametrize(
    "failure", [VectorStoreError(SECRET), VectorStoreConfigurationError(SECRET)]
)
def test_translates_vector_store_failures(
    db_session: Session, failure: Exception
) -> None:
    course, _document, _chunks = _seed_course(
        db_session, email="storefail@example.com", texts=["alpha"]
    )

    with pytest.raises(MaterialRetrievalError) as caught:
        _load(db_session, course.id, store=StubVectorStore(error=failure))

    assert caught.value.__cause__ is failure


def test_never_falls_back_to_whole_corpus_assembly(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A retrieval failure must fail, not quietly re-read the whole course."""
    import services.course_material as course_material_module
    import services.retrieval_material as retrieval_material_module

    def forbidden(*args, **kwargs):
        raise AssertionError("retrieval must never fall back to whole-corpus assembly")

    monkeypatch.setattr(course_material_module, "load_course_material", forbidden)
    monkeypatch.setattr(
        retrieval_material_module, "load_course_material", forbidden, raising=False
    )

    course, _document, _chunks = _seed_course(
        db_session, email="nofallback@example.com", texts=["alpha"]
    )

    with pytest.raises(MaterialRetrievalError):
        _load(db_session, course.id, store=StubVectorStore(error=VectorStoreError("v")))
