import sys
from types import SimpleNamespace

import pytest

from backend.app.config import EMBEDDING_PROVIDER_LOCAL
from backend.app.embedding_models import EmbeddingModelSpec
import services.embeddings as embeddings
from services.embeddings import (
    EmbeddingConfigurationError,
    EmbeddingDimensionMismatchError,
    EmbeddingError,
    EmbeddingInvalidResponseError,
    EmbeddingProviderError,
    LocalEmbeddingProvider,
    configured_embedding_identity,
    get_embedding_provider,
    is_transient_embedding_error,
)

ASYMMETRIC = EmbeddingModelSpec(
    model_id="test/asymmetric",
    dimensions=4,
    query_prefix="query: ",
    passage_prefix="passage: ",
    max_sequence_length=512,
)
SYMMETRIC = EmbeddingModelSpec(
    model_id="test/symmetric",
    dimensions=4,
    query_prefix="",
    passage_prefix="",
    max_sequence_length=512,
)


class RecordingModel:
    """Stands in for fastembed's TextEmbedding without loading a graph."""

    def __init__(self, vectors=None, error: Exception | None = None) -> None:
        self.seen: list[str] = []
        self._vectors = vectors
        self._error = error

    def embed(self, texts, batch_size=None):
        self.seen.extend(texts)
        if self._error is not None:
            raise self._error
        if self._vectors is not None:
            return iter(self._vectors)
        return iter([[0.1, 0.2, 0.3, 0.4] for _ in texts])


@pytest.fixture(autouse=True)
def asymmetric_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(embeddings, "EMBEDDING_MODEL", ASYMMETRIC)


def _provider(model: RecordingModel) -> LocalEmbeddingProvider:
    return LocalEmbeddingProvider(model=model)


def test_query_carries_only_the_query_prefix() -> None:
    model = RecordingModel()

    _provider(model).embed_query("what is entropy")

    assert model.seen == ["query: what is entropy"]


def test_documents_carry_only_the_passage_prefix() -> None:
    model = RecordingModel()

    _provider(model).embed_documents(["first", "second"])

    assert model.seen == ["passage: first", "passage: second"]


def test_a_symmetric_model_adds_no_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(embeddings, "EMBEDDING_MODEL", SYMMETRIC)
    model = RecordingModel()

    provider = _provider(model)
    provider.embed_query("q")
    provider.embed_documents(["d"])

    assert model.seen == ["q", "d"]


def test_embedding_returns_one_vector_per_text() -> None:
    vectors = _provider(RecordingModel()).embed_documents(["a", "b", "c"])

    assert len(vectors) == 3
    assert all(len(vector) == ASYMMETRIC.dimensions for vector in vectors)


def test_query_returns_a_single_vector() -> None:
    assert len(_provider(RecordingModel()).embed_query("q")) == ASYMMETRIC.dimensions


def test_wrong_width_is_a_dimension_mismatch() -> None:
    model = RecordingModel(vectors=[[0.1, 0.2]])

    with pytest.raises(EmbeddingDimensionMismatchError):
        _provider(model).embed_query("q")


def test_wrong_vector_count_is_an_invalid_response() -> None:
    model = RecordingModel(vectors=[[0.1, 0.2, 0.3, 0.4]])

    with pytest.raises(EmbeddingInvalidResponseError):
        _provider(model).embed_documents(["a", "b"])


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_values_are_an_invalid_response(value: float) -> None:
    model = RecordingModel(vectors=[[0.1, 0.2, 0.3, value]])

    with pytest.raises(EmbeddingInvalidResponseError):
        _provider(model).embed_query("q")


def test_non_numeric_values_are_an_invalid_response() -> None:
    model = RecordingModel(vectors=[["a", "b", "c", "d"]])

    with pytest.raises(EmbeddingInvalidResponseError):
        _provider(model).embed_query("q")


def test_an_empty_vector_is_an_invalid_response() -> None:
    model = RecordingModel(vectors=[[]])

    with pytest.raises(EmbeddingInvalidResponseError):
        _provider(model).embed_query("q")


def test_a_model_failure_becomes_a_provider_error() -> None:
    model = RecordingModel(error=RuntimeError("onnx exploded"))

    with pytest.raises(EmbeddingProviderError):
        _provider(model).embed_query("q")


def test_a_model_failure_requeues_but_a_malformed_vector_does_not() -> None:
    with pytest.raises(EmbeddingError) as failure:
        _provider(RecordingModel(error=RuntimeError("onnx exploded"))).embed_query("q")
    assert is_transient_embedding_error(failure.value)

    with pytest.raises(EmbeddingError) as malformed:
        _provider(RecordingModel(vectors=[[0.1, 0.2]])).embed_query("q")
    assert not is_transient_embedding_error(malformed.value)


def test_a_missing_cache_names_the_fetch_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refuses_to_load(**kwargs):
        raise RuntimeError("no such model in cache")

    # A stand-in module keeps the suite independent of whether the ONNX
    # library is installed, which is the point of never loading it here.
    monkeypatch.setitem(
        sys.modules, "fastembed", SimpleNamespace(TextEmbedding=refuses_to_load)
    )
    monkeypatch.setattr(embeddings, "_shared_model", None)
    monkeypatch.setattr(
        embeddings,
        "settings",
        SimpleNamespace(
            embedding_model_cache_directory="/nonexistent-embedding-cache",
            embedding_batch_size=32,
        ),
    )

    with pytest.raises(EmbeddingConfigurationError, match="fetch_embedding_model"):
        embeddings.load_shared_model()


def test_a_missing_library_is_reported_as_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "fastembed", None)
    monkeypatch.setattr(embeddings, "_shared_model", None)

    with pytest.raises(EmbeddingConfigurationError, match="fastembed is not installed"):
        embeddings.load_shared_model()


def test_identity_reports_the_local_provider_and_pinned_model() -> None:
    assert configured_embedding_identity() == (
        EMBEDDING_PROVIDER_LOCAL,
        ASYMMETRIC.model_id,
    )


def test_factory_returns_the_local_provider() -> None:
    assert isinstance(get_embedding_provider(), LocalEmbeddingProvider)
