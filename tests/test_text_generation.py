import json
from types import SimpleNamespace

import httpx
import pytest

import services.text_generation as text_generation
from backend.app.config import IMPLEMENTED_AI_PROVIDERS

OLLAMA_SETTINGS = SimpleNamespace(
    ai_provider="ollama",
    gemini_api_key=None,
    ollama_base_url="http://ollama.test:11434",
    ollama_model="qwen3:8b",
    ollama_timeout_seconds=42,
)


def _ollama_provider(monkeypatch, handler):
    monkeypatch.setattr(text_generation, "settings", OLLAMA_SETTINGS)
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return text_generation.OllamaTextGenerationProvider(client=client)


def _ollama_envelope(response_text: str, **extra: object) -> dict[str, object]:
    return {
        "model": "qwen3:8b",
        "response": response_text,
        "done": True,
        **extra,
    }


def test_gemini_provider_parses_json_response(monkeypatch) -> None:
    class FakeModels:
        def generate_content(self, **kwargs):
            return SimpleNamespace(text='{"title": "Test Guide"}')

    class FakeClient:
        def __init__(self, api_key: str) -> None:
            self.models = FakeModels()

    monkeypatch.setattr(
        text_generation,
        "settings",
        SimpleNamespace(gemini_api_key="test-key"),
    )
    monkeypatch.setattr(text_generation.genai, "Client", FakeClient)

    provider = text_generation.GeminiTextGenerationProvider()

    result = provider.generate_json("Test prompt")

    assert result == {"title": "Test Guide"}


def test_gemini_provider_rejects_empty_response(monkeypatch) -> None:
    class FakeModels:
        def generate_content(self, **kwargs):
            return SimpleNamespace(text="")

    class FakeClient:
        def __init__(self, api_key: str) -> None:
            self.models = FakeModels()

    monkeypatch.setattr(
        text_generation,
        "settings",
        SimpleNamespace(gemini_api_key="test-key"),
    )
    monkeypatch.setattr(text_generation.genai, "Client", FakeClient)

    provider = text_generation.GeminiTextGenerationProvider()

    try:
        provider.generate_json("Test prompt")
    except text_generation.TextGenerationError as exc:
        assert "empty response" in str(exc)
    else:
        raise AssertionError("Expected TextGenerationError")


def test_get_text_generation_provider_returns_gemini(monkeypatch) -> None:
    monkeypatch.setattr(
        text_generation,
        "settings",
        SimpleNamespace(
            ai_provider="gemini",
            gemini_api_key="test-key",
        ),
    )

    class FakeGeminiProvider:
        pass

    monkeypatch.setattr(
        text_generation,
        "GeminiTextGenerationProvider",
        FakeGeminiProvider,
    )

    provider = text_generation.get_text_generation_provider()

    assert isinstance(provider, FakeGeminiProvider)


def test_get_text_generation_provider_rejects_unimplemented_provider(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        text_generation,
        "settings",
        SimpleNamespace(
            ai_provider="openai",
            gemini_api_key=None,
        ),
    )

    try:
        text_generation.get_text_generation_provider()
    except text_generation.TextGenerationError as exc:
        assert "not implemented" in str(exc)
    else:
        raise AssertionError("Expected TextGenerationError")


def test_get_text_generation_provider_returns_ollama(monkeypatch) -> None:
    monkeypatch.setattr(text_generation, "settings", OLLAMA_SETTINGS)

    provider = text_generation.get_text_generation_provider()

    assert isinstance(provider, text_generation.OllamaTextGenerationProvider)


def test_gemini_provider_returns_text_response(monkeypatch) -> None:
    class FakeModels:
        def generate_content(self, **kwargs):
            return SimpleNamespace(text="Generated tutor response")

    class FakeClient:
        def __init__(self, api_key: str) -> None:
            self.models = FakeModels()

    monkeypatch.setattr(
        text_generation,
        "settings",
        SimpleNamespace(gemini_api_key="test-key"),
    )
    monkeypatch.setattr(text_generation.genai, "Client", FakeClient)

    provider = text_generation.GeminiTextGenerationProvider()

    result = provider.generate_text("Test prompt")

    assert result == "Generated tutor response"


def test_gemini_provider_rejects_empty_text_response(monkeypatch) -> None:
    class FakeModels:
        def generate_content(self, **kwargs):
            return SimpleNamespace(text="")

    class FakeClient:
        def __init__(self, api_key: str) -> None:
            self.models = FakeModels()

    monkeypatch.setattr(
        text_generation,
        "settings",
        SimpleNamespace(gemini_api_key="test-key"),
    )
    monkeypatch.setattr(text_generation.genai, "Client", FakeClient)

    provider = text_generation.GeminiTextGenerationProvider()

    try:
        provider.generate_text("Test prompt")
    except text_generation.TextGenerationError as exc:
        assert "empty response" in str(exc)
    else:
        raise AssertionError("Expected TextGenerationError")


def test_ollama_provider_sends_configured_request(monkeypatch) -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=_ollama_envelope("Generated tutor response"))

    provider = _ollama_provider(monkeypatch, handler)

    result = provider.generate_text("Explain binary trees")

    assert result == "Generated tutor response"
    assert len(captured) == 1
    request = captured[0]
    assert request.method == "POST"
    assert str(request.url) == "http://ollama.test:11434/api/generate"
    payload = json.loads(request.content)
    assert payload["model"] == "qwen3:8b"
    assert payload["prompt"] == "Explain binary trees"
    assert payload["stream"] is False
    assert "format" not in payload


def test_ollama_provider_requests_json_format(monkeypatch) -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=_ollama_envelope('{"title": "Test Guide"}'))

    provider = _ollama_provider(monkeypatch, handler)

    result = provider.generate_json("Build a study guide")

    assert result == {"title": "Test Guide"}
    assert json.loads(captured[0].content)["format"] == "json"


def test_ollama_provider_parses_fenced_json(monkeypatch) -> None:
    fenced = '```json\n{"title": "Fenced Guide"}\n```'

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_ollama_envelope(fenced))

    provider = _ollama_provider(monkeypatch, handler)

    assert provider.generate_json("Prompt") == {"title": "Fenced Guide"}


def test_ollama_provider_reports_generation_metadata(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=_ollama_envelope(
                "Answer",
                prompt_eval_count=26,
                eval_count=298,
            ),
        )

    provider = _ollama_provider(monkeypatch, handler)

    _, metadata = provider.generate_text_with_metadata("Prompt")

    assert metadata.provider == "ollama"
    assert metadata.model == "qwen3:8b"
    assert metadata.prompt_tokens == 26
    assert metadata.completion_tokens == 298
    assert metadata.total_tokens == 324
    assert metadata.latency_ms is not None


def test_ollama_provider_maps_connection_failure(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    provider = _ollama_provider(monkeypatch, handler)

    with pytest.raises(text_generation.TextGenerationConnectionError):
        provider.generate_text("Prompt")


def test_ollama_provider_maps_timeout(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    provider = _ollama_provider(monkeypatch, handler)

    with pytest.raises(text_generation.TextGenerationTimeoutError):
        provider.generate_text("Prompt")


def test_ollama_provider_rejects_http_error_without_returning_body(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="internal ollama failure")

    provider = _ollama_provider(monkeypatch, handler)

    with pytest.raises(text_generation.TextGenerationResponseError) as exc_info:
        provider.generate_text("Prompt")

    assert "internal ollama failure" not in str(exc_info.value)


def test_ollama_provider_rejects_malformed_envelope(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"foo": "bar"})

    provider = _ollama_provider(monkeypatch, handler)

    with pytest.raises(text_generation.TextGenerationResponseError):
        provider.generate_text("Prompt")


def test_ollama_provider_rejects_non_json_envelope(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not an envelope")

    provider = _ollama_provider(monkeypatch, handler)

    with pytest.raises(text_generation.TextGenerationResponseError):
        provider.generate_text("Prompt")


@pytest.mark.parametrize("response_text", ["", "      ", "\n\t "])
def test_ollama_provider_rejects_empty_response(monkeypatch, response_text) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_ollama_envelope(response_text))

    provider = _ollama_provider(monkeypatch, handler)

    with pytest.raises(text_generation.TextGenerationResponseError, match="empty"):
        provider.generate_text("Prompt")


@pytest.mark.parametrize(
    "generated",
    ['{"title": "x",', "Sure! Here is your study guide:", "[1, 2, 3]"],
)
def test_ollama_provider_rejects_unusable_json_output(monkeypatch, generated) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_ollama_envelope(generated))

    provider = _ollama_provider(monkeypatch, handler)

    with pytest.raises(text_generation.TextGenerationResponseError):
        provider.generate_json("Prompt")


def test_ollama_provider_applies_configured_timeout(monkeypatch) -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=_ollama_envelope("Answer"))

    provider = _ollama_provider(monkeypatch, handler)
    provider.generate_text("Prompt")

    assert captured[0].extensions["timeout"]["read"] == 42


def test_configured_provider_identity_follows_settings(monkeypatch) -> None:
    monkeypatch.setattr(text_generation, "settings", OLLAMA_SETTINGS)

    assert text_generation.configured_provider_identity() == ("ollama", "qwen3:8b")

    monkeypatch.setattr(
        text_generation,
        "settings",
        SimpleNamespace(ai_provider="gemini", gemini_api_key="key"),
    )

    assert text_generation.configured_provider_identity() == (
        "gemini",
        text_generation.GeminiTextGenerationProvider.MODEL,
    )


def test_providers_reuse_one_http_client_instead_of_leaking_pools(
    monkeypatch,
) -> None:
    monkeypatch.setattr(text_generation, "settings", OLLAMA_SETTINGS)
    monkeypatch.setattr(text_generation, "_shared_http_client", None)

    first = text_generation.OllamaTextGenerationProvider()
    second = text_generation.OllamaTextGenerationProvider()

    assert first._client is second._client
    assert not first._client.is_closed


def test_ollama_provider_rejects_redirect_instead_of_following_it(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "http://elsewhere.test/"})

    provider = _ollama_provider(monkeypatch, handler)

    with pytest.raises(text_generation.TextGenerationResponseError, match="HTTP 302"):
        provider.generate_text("Prompt")


def test_every_implemented_provider_is_constructible(monkeypatch) -> None:
    monkeypatch.setattr(
        text_generation.genai,
        "Client",
        lambda api_key: SimpleNamespace(models=None),
    )

    for provider_name in IMPLEMENTED_AI_PROVIDERS:
        monkeypatch.setattr(
            text_generation,
            "settings",
            SimpleNamespace(
                ai_provider=provider_name,
                gemini_api_key="test-key",
                ollama_base_url="http://ollama.test:11434",
                ollama_model="qwen3:8b",
                ollama_timeout_seconds=42,
            ),
        )

        provider = text_generation.get_text_generation_provider()

        assert hasattr(provider, "generate_text")
        assert hasattr(provider, "generate_json")


def test_provider_errors_remain_catchable_as_text_generation_error() -> None:
    for error_type in (
        text_generation.TextGenerationConnectionError,
        text_generation.TextGenerationTimeoutError,
        text_generation.TextGenerationResponseError,
    ):
        assert issubclass(error_type, text_generation.TextGenerationError)
