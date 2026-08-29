import json
from types import SimpleNamespace

from google.genai import errors as genai_errors
import httpx
import pytest

from backend.app.config import IMPLEMENTED_EMBEDDING_PROVIDERS
from backend.app.models import EMBEDDING_DIMENSIONS
import services.embeddings as embeddings
from services.embeddings import (
    EmbeddingConfigurationError,
    EmbeddingConnectionError,
    EmbeddingDimensionMismatchError,
    EmbeddingError,
    EmbeddingInvalidResponseError,
    EmbeddingProviderError,
    EmbeddingRateLimitError,
    EmbeddingTimeoutError,
    GeminiEmbeddingProvider,
    OllamaEmbeddingProvider,
    configured_embedding_identity,
    get_embedding_provider,
    is_transient_embedding_error,
)

OLLAMA_SETTINGS = SimpleNamespace(
    embedding_provider="ollama",
    gemini_api_key=None,
    ollama_base_url="http://ollama.test:11434",
    ollama_embedding_model="nomic-embed-text",
    gemini_embedding_model="gemini-embedding-001",
    embedding_batch_size=2,
    embedding_timeout_seconds=42,
)

GEMINI_SETTINGS = SimpleNamespace(
    embedding_provider="gemini",
    gemini_api_key="test-key",
    ollama_base_url="http://ollama.test:11434",
    ollama_embedding_model="nomic-embed-text",
    gemini_embedding_model="gemini-embedding-001",
    embedding_batch_size=2,
    embedding_timeout_seconds=42,
)


def _vector(seed: float) -> list[float]:
    return [seed] * EMBEDDING_DIMENSIONS


def _ollama_provider(monkeypatch, handler):
    monkeypatch.setattr(embeddings, "settings", OLLAMA_SETTINGS)
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return OllamaEmbeddingProvider(client=client)


def test_ollama_sends_the_configured_model_and_input(monkeypatch) -> None:
    captured: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        return httpx.Response(200, json={"embeddings": [_vector(0.1)]})

    provider = _ollama_provider(monkeypatch, handler)
    result = provider.embed_documents(["only chunk"])

    assert captured == [
        {"model": "nomic-embed-text", "input": ["only chunk"], "truncate": True}
    ]
    assert result == [_vector(0.1)]


def test_ollama_posts_to_the_embed_endpoint(monkeypatch) -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json={"embeddings": [_vector(0.1)]})

    provider = _ollama_provider(monkeypatch, handler)
    provider.embed_documents(["chunk"])

    assert seen == ["http://ollama.test:11434/api/embed"]


def test_batches_preserve_input_order_across_boundaries(monkeypatch) -> None:
    batches: list[list[str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        texts = json.loads(request.content)["input"]
        batches.append(texts)
        return httpx.Response(
            200,
            json={"embeddings": [_vector(float(text)) for text in texts]},
        )

    provider = _ollama_provider(monkeypatch, handler)
    result = provider.embed_documents(["1", "2", "3", "4", "5"])

    assert batches == [["1", "2"], ["3", "4"], ["5"]]
    assert result == [
        _vector(1.0),
        _vector(2.0),
        _vector(3.0),
        _vector(4.0),
        _vector(5.0),
    ]


def test_embed_query_returns_a_single_vector(monkeypatch) -> None:
    provider = _ollama_provider(
        monkeypatch,
        lambda request: httpx.Response(200, json={"embeddings": [_vector(0.7)]}),
    )

    assert provider.embed_query("what is binary search?") == _vector(0.7)


def test_empty_input_never_calls_the_provider(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no request should be made for empty input")

    provider = _ollama_provider(monkeypatch, handler)

    assert provider.embed_documents([]) == []


def test_timeout_maps_to_a_retryable_embedding_timeout(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("too slow", request=request)

    provider = _ollama_provider(monkeypatch, handler)

    with pytest.raises(EmbeddingTimeoutError) as excinfo:
        provider.embed_documents(["chunk"])
    assert is_transient_embedding_error(excinfo.value)


def test_transport_failure_maps_to_a_retryable_connection_error(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    provider = _ollama_provider(monkeypatch, handler)

    with pytest.raises(EmbeddingConnectionError) as excinfo:
        provider.embed_documents(["chunk"])
    assert is_transient_embedding_error(excinfo.value)


def test_rate_limit_maps_to_a_retryable_error(monkeypatch) -> None:
    provider = _ollama_provider(
        monkeypatch, lambda request: httpx.Response(429, json={})
    )

    with pytest.raises(EmbeddingRateLimitError) as excinfo:
        provider.embed_documents(["chunk"])
    assert is_transient_embedding_error(excinfo.value)


def test_server_error_maps_to_a_retryable_provider_error(monkeypatch) -> None:
    provider = _ollama_provider(
        monkeypatch, lambda request: httpx.Response(503, json={})
    )

    with pytest.raises(EmbeddingProviderError) as excinfo:
        provider.embed_documents(["chunk"])
    assert is_transient_embedding_error(excinfo.value)


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"embeddings": "not-a-list"},
        {"embeddings": []},
        {"embeddings": [[]]},
        {"embeddings": [None]},
        {"embeddings": [["not", "numbers"] * 384]},
    ],
)
def test_malformed_payloads_are_permanent_failures(monkeypatch, payload) -> None:
    provider = _ollama_provider(
        monkeypatch, lambda request: httpx.Response(200, json=payload)
    )

    with pytest.raises(EmbeddingInvalidResponseError) as excinfo:
        provider.embed_documents(["chunk"])
    assert not is_transient_embedding_error(excinfo.value)


def test_non_json_response_is_a_permanent_failure(monkeypatch) -> None:
    provider = _ollama_provider(
        monkeypatch, lambda request: httpx.Response(200, text="<html>nope</html>")
    )

    with pytest.raises(EmbeddingInvalidResponseError):
        provider.embed_documents(["chunk"])


def test_vector_count_must_match_input_count(monkeypatch) -> None:
    provider = _ollama_provider(
        monkeypatch,
        lambda request: httpx.Response(200, json={"embeddings": [_vector(0.1)]}),
    )

    with pytest.raises(EmbeddingInvalidResponseError, match="count"):
        provider.embed_documents(["one", "two"])


def test_wrong_width_is_a_permanent_dimension_mismatch(monkeypatch) -> None:
    provider = _ollama_provider(
        monkeypatch,
        lambda request: httpx.Response(200, json={"embeddings": [[0.1] * 512]}),
    )

    with pytest.raises(EmbeddingDimensionMismatchError) as excinfo:
        provider.embed_documents(["chunk"])
    assert not is_transient_embedding_error(excinfo.value)


def test_non_finite_values_are_rejected(monkeypatch) -> None:
    values = ["1.0"] * EMBEDDING_DIMENSIONS
    values[7] = "NaN"
    body = '{"embeddings": [[' + ", ".join(values) + "]]}"
    provider = _ollama_provider(
        monkeypatch,
        lambda request: httpx.Response(
            200,
            content=body.encode("utf-8"),
            headers={"content-type": "application/json"},
        ),
    )

    with pytest.raises(EmbeddingInvalidResponseError, match="finite"):
        provider.embed_documents(["chunk"])


def test_ollama_honours_the_configured_timeout(monkeypatch) -> None:
    timeouts: list[object] = []

    def handler(request: httpx.Request) -> httpx.Response:
        timeouts.append(request.extensions.get("timeout"))
        return httpx.Response(200, json={"embeddings": [_vector(0.1)]})

    provider = _ollama_provider(monkeypatch, handler)
    provider.embed_documents(["chunk"])

    assert timeouts[0]["read"] == 42


class _FakeGeminiModels:
    def __init__(self, responses, recorder):
        self._responses = responses
        self._recorder = recorder

    def embed_content(self, *, model, contents, config=None):
        self._recorder.append((model, list(contents)))
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class _FakeGeminiClient:
    def __init__(self, responses, recorder):
        self.models = _FakeGeminiModels(responses, recorder)


def _gemini_provider(monkeypatch, responses, recorder):
    monkeypatch.setattr(embeddings, "settings", GEMINI_SETTINGS)
    monkeypatch.setattr(
        embeddings.genai,
        "Client",
        lambda **kwargs: _FakeGeminiClient(responses, recorder),
    )
    return GeminiEmbeddingProvider()


def test_gemini_uses_the_configured_model_and_preserves_order(monkeypatch) -> None:
    recorder: list[tuple[str, list[str]]] = []
    responses = [
        SimpleNamespace(
            embeddings=[
                SimpleNamespace(values=_vector(1.0)),
                SimpleNamespace(values=_vector(2.0)),
            ]
        ),
        SimpleNamespace(embeddings=[SimpleNamespace(values=_vector(3.0))]),
    ]
    provider = _gemini_provider(monkeypatch, responses, recorder)

    result = provider.embed_documents(["a", "b", "c"])

    assert [model for model, _ in recorder] == [
        "gemini-embedding-001",
        "gemini-embedding-001",
    ]
    assert [texts for _, texts in recorder] == [["a", "b"], ["c"]]
    assert result == [_vector(1.0), _vector(2.0), _vector(3.0)]


def test_gemini_honours_the_configured_timeout(monkeypatch) -> None:
    client_options: list[object] = []

    monkeypatch.setattr(embeddings, "settings", GEMINI_SETTINGS)
    monkeypatch.setattr(
        embeddings.genai,
        "Client",
        lambda **kwargs: (
            client_options.append(kwargs["http_options"]) or _FakeGeminiClient([], [])
        ),
    )

    GeminiEmbeddingProvider()

    assert client_options[0].timeout == 42_000


def test_gemini_timeout_is_retryable(monkeypatch) -> None:
    recorder: list[tuple[str, list[str]]] = []
    provider = _gemini_provider(
        monkeypatch,
        [httpx.ReadTimeout("too slow")],
        recorder,
    )

    with pytest.raises(EmbeddingTimeoutError) as excinfo:
        provider.embed_documents(["a"])
    assert is_transient_embedding_error(excinfo.value)


def test_gemini_rate_limit_is_retryable(monkeypatch) -> None:
    recorder: list[tuple[str, list[str]]] = []
    provider = _gemini_provider(
        monkeypatch,
        [genai_errors.APIError(429, "Too Many Requests")],
        recorder,
    )

    with pytest.raises(EmbeddingRateLimitError) as excinfo:
        provider.embed_documents(["a"])
    assert is_transient_embedding_error(excinfo.value)


def test_gemini_auth_failure_is_permanent(monkeypatch) -> None:
    recorder: list[tuple[str, list[str]]] = []
    provider = _gemini_provider(
        monkeypatch,
        [genai_errors.APIError(401, "Invalid Key")],
        recorder,
    )

    with pytest.raises(EmbeddingError) as excinfo:
        provider.embed_documents(["a"])
    assert not is_transient_embedding_error(excinfo.value)


def test_gemini_malformed_response_is_permanent(monkeypatch) -> None:
    recorder: list[tuple[str, list[str]]] = []
    provider = _gemini_provider(
        monkeypatch,
        [SimpleNamespace(embeddings=None)],
        recorder,
    )

    with pytest.raises(EmbeddingInvalidResponseError):
        provider.embed_documents(["a"])


def test_gemini_requires_an_api_key(monkeypatch) -> None:
    monkeypatch.setattr(
        embeddings,
        "settings",
        SimpleNamespace(**{**GEMINI_SETTINGS.__dict__, "gemini_api_key": None}),
    )

    with pytest.raises(EmbeddingConfigurationError):
        GeminiEmbeddingProvider()


@pytest.mark.parametrize("provider_name", IMPLEMENTED_EMBEDDING_PROVIDERS)
def test_factory_constructs_every_implemented_provider(
    monkeypatch, provider_name
) -> None:
    monkeypatch.setattr(
        embeddings,
        "settings",
        SimpleNamespace(
            **{**GEMINI_SETTINGS.__dict__, "embedding_provider": provider_name}
        ),
    )
    monkeypatch.setattr(
        embeddings.genai,
        "Client",
        lambda **kwargs: _FakeGeminiClient([], []),
    )

    assert get_embedding_provider() is not None


def test_factory_rejects_an_unimplemented_provider(monkeypatch) -> None:
    monkeypatch.setattr(
        embeddings,
        "settings",
        SimpleNamespace(**{**OLLAMA_SETTINGS.__dict__, "embedding_provider": "openai"}),
    )

    with pytest.raises(EmbeddingConfigurationError):
        get_embedding_provider()


def test_configured_identity_reports_provider_and_model(monkeypatch) -> None:
    monkeypatch.setattr(embeddings, "settings", OLLAMA_SETTINGS)
    assert configured_embedding_identity() == ("ollama", "nomic-embed-text")

    monkeypatch.setattr(embeddings, "settings", GEMINI_SETTINGS)
    assert configured_embedding_identity() == ("gemini", "gemini-embedding-001")
