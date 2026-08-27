import json
import threading
import time
from types import SimpleNamespace

from google.genai import errors as genai_errors
import httpx
import pytest

from backend.app.config import IMPLEMENTED_AI_PROVIDERS
from schemas.ai_usage import ErrorCategory
import services.text_generation as text_generation
from services.text_generation import (
    ClaudeTextGenerationProvider,
    GeminiTextGenerationProvider,
    GenerationConcurrencyError,
    GenerationMetadata,
    OpenAITextGenerationProvider,
    ReliableTextGenerationProvider,
    TextGenerationAuthError,
    TextGenerationConnectionError,
    TextGenerationEmptyResponseError,
    TextGenerationError,
    TextGenerationProviderError,
    TextGenerationRateLimitError,
    TextGenerationTimeoutError,
    get_text_generation_provider,
    is_transient_generation_error,
)

OLLAMA_SETTINGS = SimpleNamespace(
    ai_provider="ollama",
    ai_fallback_providers="",
    gemini_api_key=None,
    openai_api_key=None,
    anthropic_api_key=None,
    ollama_base_url="http://ollama.test:11434",
    ollama_model="llama3.1",
    ai_generation_timeout_seconds=42,
    ai_generation_max_attempts=3,
    ai_generation_backoff_base_seconds=0.01,
    ai_generation_backoff_max_seconds=0.1,
    ai_generation_max_concurrency=10,
    ai_generation_overall_timeout_seconds=110,
    ollama_temperature=0.2,
    ollama_top_p=0.9,
    ollama_num_ctx=8192,
    ollama_num_predict=4096,
    ollama_repeat_penalty=1.1,
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


class StubProvider:
    def __init__(
        self,
        *,
        provider_name: str = "stub_provider",
        model_name: str = "stub-model-1",
        behaviors: list[object] | None = None,
    ) -> None:
        self.PROVIDER_NAME = provider_name
        self.MODEL = model_name
        self.behaviors = list(behaviors or [])
        self.call_count = 0

    def generate_text_with_metadata(
        self, prompt: str
    ) -> tuple[str, GenerationMetadata]:
        self.call_count += 1
        if self.behaviors:
            item = self.behaviors.pop(0)
            if isinstance(item, Exception):
                raise item
            return str(item), GenerationMetadata(
                provider=self.PROVIDER_NAME,
                model=self.MODEL,
                total_tokens=42,
                latency_ms=10,
            )
        return f"Response for {prompt}", GenerationMetadata(
            provider=self.PROVIDER_NAME,
            model=self.MODEL,
            total_tokens=42,
            latency_ms=10,
        )

    def generate_text(self, prompt: str) -> str:
        text, _ = self.generate_text_with_metadata(prompt)
        return text

    def generate_json_with_metadata(
        self, prompt: str
    ) -> tuple[dict[str, object], GenerationMetadata]:
        self.call_count += 1
        if self.behaviors:
            item = self.behaviors.pop(0)
            if isinstance(item, Exception):
                raise item
            if isinstance(item, dict):
                return item, GenerationMetadata(
                    provider=self.PROVIDER_NAME,
                    model=self.MODEL,
                    total_tokens=42,
                    latency_ms=10,
                )
        return {"data": "ok"}, GenerationMetadata(
            provider=self.PROVIDER_NAME,
            model=self.MODEL,
            total_tokens=42,
            latency_ms=10,
        )

    def generate_json(self, prompt: str) -> dict[str, object]:
        data, _ = self.generate_json_with_metadata(prompt)
        return data


def test_gemini_provider_parses_json_response(monkeypatch) -> None:
    class FakeModels:
        def generate_content(self, **kwargs):
            return SimpleNamespace(text='{"title": "Test Guide"}', usage_metadata=None)

    class FakeClient:
        def __init__(self, api_key: str, http_options=None) -> None:
            self.models = FakeModels()

    monkeypatch.setattr(
        text_generation,
        "settings",
        SimpleNamespace(gemini_api_key="test-key", ai_generation_timeout_seconds=60),
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
        def __init__(self, api_key: str, http_options=None) -> None:
            self.models = FakeModels()

    monkeypatch.setattr(
        text_generation,
        "settings",
        SimpleNamespace(gemini_api_key="test-key", ai_generation_timeout_seconds=60),
    )
    monkeypatch.setattr(text_generation.genai, "Client", FakeClient)

    provider = text_generation.GeminiTextGenerationProvider()

    with pytest.raises(TextGenerationEmptyResponseError) as exc_info:
        provider.generate_json("Test prompt")
    assert exc_info.value.error_category == ErrorCategory.EMPTY_RESPONSE.value


def test_get_text_generation_provider_returns_reliable_gemini(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        text_generation,
        "settings",
        SimpleNamespace(
            ai_provider="gemini",
            ai_fallback_providers="",
            gemini_api_key="test-key",
            openai_api_key=None,
            anthropic_api_key=None,
            ai_generation_timeout_seconds=60,
            ai_generation_max_attempts=3,
            ai_generation_backoff_base_seconds=0.01,
            ai_generation_backoff_max_seconds=0.1,
            ai_generation_max_concurrency=10,
            ai_generation_overall_timeout_seconds=110,
        ),
    )

    # Mock the registry to return our fake provider
    class FakeGeminiProvider:
        PROVIDER_NAME = "gemini"
        MODEL = "gemini-3.6-flash"

        def __init__(self, *args, **kwargs):
            pass

        def generate_text_with_metadata(self, prompt: str):
            return "fake", GenerationMetadata("gemini", "gemini-3.6-flash")

        def generate_text(self, prompt: str):
            return "fake"

        def generate_json_with_metadata(self, prompt: str):
            return {}, GenerationMetadata("gemini", "gemini-3.6-flash")

        def generate_json(self, prompt: str):
            return {}

    monkeypatch.setattr(
        text_generation.ProviderRegistry,
        "get_constructor",
        lambda name: FakeGeminiProvider if name == "gemini" else None,
    )

    provider = text_generation.get_text_generation_provider()

    assert isinstance(provider, text_generation.ReliableTextGenerationProvider)
    assert len(provider.providers) == 1
    assert isinstance(provider.providers[0], FakeGeminiProvider)


def test_get_text_generation_provider_rejects_unimplemented_provider(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        text_generation,
        "settings",
        SimpleNamespace(
            ai_provider="openai",
            ai_fallback_providers="",
            gemini_api_key=None,
            openai_api_key=None,
            anthropic_api_key=None,
            ai_generation_timeout_seconds=60,
            ai_generation_max_attempts=3,
            ai_generation_backoff_base_seconds=0.01,
            ai_generation_backoff_max_seconds=0.1,
            ai_generation_max_concurrency=10,
            ai_generation_overall_timeout_seconds=110,
        ),
    )

    with pytest.raises(TextGenerationAuthError) as exc_info:
        text_generation.get_text_generation_provider()
    assert "OPENAI_API_KEY is not configured" in str(exc_info.value)


def test_get_text_generation_provider_returns_ollama(monkeypatch) -> None:
    monkeypatch.setattr(text_generation, "settings", OLLAMA_SETTINGS)

    provider = text_generation.get_text_generation_provider()

    assert isinstance(provider, text_generation.ReliableTextGenerationProvider)
    assert len(provider.providers) == 1
    assert isinstance(
        provider.providers[0], text_generation.OllamaTextGenerationProvider
    )


def test_gemini_provider_returns_text_response(monkeypatch) -> None:
    class FakeModels:
        def generate_content(self, **kwargs):
            return SimpleNamespace(text="Generated tutor response", usage_metadata=None)

    class FakeClient:
        def __init__(self, api_key: str, http_options=None) -> None:
            self.models = FakeModels()

    monkeypatch.setattr(
        text_generation,
        "settings",
        SimpleNamespace(gemini_api_key="test-key", ai_generation_timeout_seconds=60),
    )
    monkeypatch.setattr(text_generation.genai, "Client", FakeClient)

    provider = text_generation.GeminiTextGenerationProvider()
    result = provider.generate_text("Test prompt")

    assert result == "Generated tutor response"


def test_gemini_provider_error_mappings(monkeypatch) -> None:
    class ErrorModels:
        def __init__(self, exc: Exception) -> None:
            self.exc = exc

        def generate_content(self, **kwargs):
            raise self.exc

    class ErrorClient:
        def __init__(self, exc: Exception) -> None:
            self.models = ErrorModels(exc)

    monkeypatch.setattr(
        text_generation,
        "settings",
        SimpleNamespace(gemini_api_key="test-key", ai_generation_timeout_seconds=60),
    )

    # 429 rate limit
    monkeypatch.setattr(
        text_generation.genai,
        "Client",
        lambda **kw: ErrorClient(genai_errors.APIError(429, "Too Many Requests")),
    )
    provider = GeminiTextGenerationProvider()
    with pytest.raises(TextGenerationRateLimitError):
        provider.generate_text("test")


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
    assert payload["model"] == "llama3.1"
    assert payload["prompt"] == "Explain binary trees"
    assert payload["stream"] is False
    assert "format" not in payload


def test_ollama_provider_sends_configured_sampling_options(monkeypatch) -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=_ollama_envelope("Generated response"))

    provider = _ollama_provider(monkeypatch, handler)

    provider.generate_text("Explain binary trees")

    options = json.loads(captured[0].content)["options"]
    assert options["temperature"] == 0.2
    assert options["top_p"] == 0.9
    assert options["num_ctx"] == 8192
    assert options["num_predict"] == 4096
    assert options["repeat_penalty"] == 1.1


def test_ollama_provider_sends_sampling_options_on_json_requests(monkeypatch) -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=_ollama_envelope('{"title": "Test Guide"}'))

    provider = _ollama_provider(monkeypatch, handler)

    provider.generate_json("Build a study guide")

    payload = json.loads(captured[0].content)
    assert payload["format"] == "json"
    assert payload["options"]["temperature"] == 0.2
    assert payload["options"]["num_ctx"] == 8192


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
    assert metadata.model == "llama3.1"
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

    with pytest.raises(TextGenerationProviderError) as exc_info:
        provider.generate_text("Prompt")

    assert "internal ollama failure" not in str(exc_info.value)


def test_ollama_provider_rejects_malformed_envelope(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"foo": "bar"})

    provider = _ollama_provider(monkeypatch, handler)

    with pytest.raises(TextGenerationProviderError):
        provider.generate_text("Prompt")


def test_ollama_provider_rejects_non_json_envelope(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not an envelope")

    provider = _ollama_provider(monkeypatch, handler)

    with pytest.raises(TextGenerationProviderError):
        provider.generate_text("Prompt")


@pytest.mark.parametrize("response_text", ["", "      ", "\n\t "])
def test_ollama_provider_rejects_empty_response(monkeypatch, response_text) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_ollama_envelope(response_text))

    provider = _ollama_provider(monkeypatch, handler)

    with pytest.raises(TextGenerationEmptyResponseError, match="empty"):
        provider.generate_text("Prompt")


@pytest.mark.parametrize(
    "generated",
    ['{"title": "x",', "Sure! Here is your study guide:", "[1, 2, 3]"],
)
def test_ollama_provider_rejects_unusable_json_output(monkeypatch, generated) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_ollama_envelope(generated))

    provider = _ollama_provider(monkeypatch, handler)

    with pytest.raises(TextGenerationError) as exc_info:
        provider.generate_json("Prompt")

    assert exc_info.value.error_category == ErrorCategory.INVALID_STRUCTURE.value


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

    assert text_generation.configured_provider_identity() == ("ollama", "llama3.1")

    monkeypatch.setattr(
        text_generation,
        "settings",
        SimpleNamespace(
            ai_provider="gemini",
            gemini_api_key="key",
            openai_api_key=None,
            anthropic_api_key=None,
        ),
    )

    assert text_generation.configured_provider_identity() == (
        "gemini",
        GeminiTextGenerationProvider.MODEL,
    )


def test_gemini_error_mapping_continues(monkeypatch) -> None:
    class ErrorModels:
        def __init__(self, exc: Exception) -> None:
            self.exc = exc

        def generate_content(self, **kwargs):
            raise self.exc

    class ErrorClient:
        def __init__(self, exc: Exception) -> None:
            self.models = ErrorModels(exc)

    monkeypatch.setattr(
        text_generation,
        "settings",
        SimpleNamespace(gemini_api_key="test-key", ai_generation_timeout_seconds=60),
    )

    # 401 auth
    monkeypatch.setattr(
        text_generation.genai,
        "Client",
        lambda **kw: ErrorClient(genai_errors.APIError(401, "Invalid Key")),
    )
    provider = GeminiTextGenerationProvider()
    with pytest.raises(TextGenerationAuthError):
        provider.generate_text("test")

    # 503 unavailable
    monkeypatch.setattr(
        text_generation.genai,
        "Client",
        lambda **kw: ErrorClient(genai_errors.APIError(503, "Unavailable")),
    )
    provider = GeminiTextGenerationProvider()
    with pytest.raises(TextGenerationProviderError):
        provider.generate_text("test")

    # Timeout
    monkeypatch.setattr(
        text_generation.genai,
        "Client",
        lambda **kw: ErrorClient(httpx.ReadTimeout("Read timed out")),
    )
    provider = GeminiTextGenerationProvider()
    with pytest.raises(TextGenerationTimeoutError):
        provider.generate_text("test")


def test_transient_error_classification() -> None:
    assert is_transient_generation_error(TextGenerationTimeoutError())
    assert is_transient_generation_error(TimeoutError())
    assert is_transient_generation_error(httpx.ConnectError("fail"))
    assert is_transient_generation_error(genai_errors.APIError(429, "quota"))
    assert is_transient_generation_error(genai_errors.APIError(503, "service"))
    assert is_transient_generation_error(TextGenerationRateLimitError("rate limit"))

    # OpenAI and Anthropic errors
    import anthropic
    import openai

    req = httpx.Request("POST", "http://test")
    assert is_transient_generation_error(openai.APIConnectionError(request=req))
    assert is_transient_generation_error(anthropic.APIConnectionError(request=req))

    # Wrapped connection errors stay transient
    try:
        raise openai.APIConnectionError(request=req)
    except Exception as exc:
        conn_err = TextGenerationConnectionError("unreachable")
        conn_err.__cause__ = exc
        assert is_transient_generation_error(conn_err)

    try:
        raise anthropic.APIConnectionError(request=req)
    except Exception as exc:
        conn_err = TextGenerationConnectionError("unreachable")
        conn_err.__cause__ = exc
        assert is_transient_generation_error(conn_err)

    # 5xx status errors are transient
    res500 = httpx.Response(500, request=req)
    res503 = httpx.Response(503, request=req)
    assert is_transient_generation_error(openai.APIStatusError("err", response=res500, body=None))
    assert is_transient_generation_error(anthropic.APIStatusError("err", response=res503, body=None))

    # Wrapped 5xx provider errors stay transient
    try:
        raise openai.APIStatusError("err", response=res500, body=None)
    except Exception as exc:
        prov_err = TextGenerationProviderError("service unavailable")
        prov_err.__cause__ = exc
        assert is_transient_generation_error(prov_err)

    try:
        raise anthropic.APIStatusError("err", response=res503, body=None)
    except Exception as exc:
        prov_err = TextGenerationProviderError("service unavailable")
        prov_err.__cause__ = exc
        assert is_transient_generation_error(prov_err)

    # Non-transient errors:
    assert not is_transient_generation_error(TextGenerationAuthError())
    assert not is_transient_generation_error(TextGenerationEmptyResponseError())
    assert not is_transient_generation_error(GenerationConcurrencyError())
    assert not is_transient_generation_error(genai_errors.APIError(400, "bad"))
    assert not is_transient_generation_error(genai_errors.APIError(401, "unauth"))
    assert not is_transient_generation_error(genai_errors.APIError(404, "notfound"))

    res400 = httpx.Response(400, request=req)
    res401 = httpx.Response(401, request=req)
    assert not is_transient_generation_error(openai.APIStatusError("bad", response=res400, body=None))
    assert not is_transient_generation_error(anthropic.APIStatusError("unauth", response=res401, body=None))


def test_reliable_provider_recovers_from_transient_failures() -> None:
    stub = StubProvider(
        provider_name="gemini",
        model_name="gemini-2.5-flash",
        behaviors=[
            genai_errors.APIError(503, "temporary failure"),
            genai_errors.APIError(429, "rate limited"),
            "Success on 3rd attempt",
        ],
    )

    reliable = ReliableTextGenerationProvider(
        [stub],
        max_attempts=3,
        backoff_base_seconds=0.001,
        backoff_max_seconds=0.01,
        max_concurrency=5,
    )

    text, meta = reliable.generate_text_with_metadata("Explain quantum physics")

    assert text == "Success on 3rd attempt"
    assert stub.call_count == 3
    assert meta.provider == "gemini"
    assert meta.model == "gemini-2.5-flash"


def test_reliable_provider_does_not_retry_permanent_error() -> None:
    stub = StubProvider(
        behaviors=[
            TextGenerationAuthError("Invalid API key"),
            "Should not be reached",
        ]
    )

    reliable = ReliableTextGenerationProvider(
        [stub],
        max_attempts=3,
        backoff_base_seconds=0.001,
        backoff_max_seconds=0.01,
        max_concurrency=5,
    )

    with pytest.raises(TextGenerationAuthError):
        reliable.generate_text("Prompt")

    assert stub.call_count == 1


def test_reliable_provider_retry_exhaustion() -> None:
    stub = StubProvider(
        behaviors=[
            genai_errors.APIError(503, "Unavailable 1"),
            genai_errors.APIError(503, "Unavailable 2"),
            genai_errors.APIError(503, "Unavailable 3"),
        ]
    )

    reliable = ReliableTextGenerationProvider(
        [stub],
        max_attempts=3,
        backoff_base_seconds=0.001,
        backoff_max_seconds=0.01,
        max_concurrency=5,
    )

    with pytest.raises(TextGenerationError) as exc_info:
        reliable.generate_text("Prompt")

    assert stub.call_count == 3
    assert exc_info.value.error_category == ErrorCategory.PROVIDER_ERROR.value


def test_reliable_provider_fallback_to_secondary_provider() -> None:
    primary = StubProvider(
        provider_name="primary_gemini",
        model_name="gemini-2.5-flash",
        behaviors=[
            genai_errors.APIError(503, "Unavailable 1"),
            genai_errors.APIError(503, "Unavailable 2"),
        ],
    )
    fallback = StubProvider(
        provider_name="fallback_gemini",
        model_name="gemini-2.5-pro",
        behaviors=[
            "Fallback generation succeeded",
        ],
    )

    reliable = ReliableTextGenerationProvider(
        [primary, fallback],
        max_attempts=2,
        backoff_base_seconds=0.001,
        backoff_max_seconds=0.01,
        max_concurrency=5,
    )

    text, meta = reliable.generate_text_with_metadata("Summarize lecture")

    assert text == "Fallback generation succeeded"
    assert primary.call_count == 2
    assert fallback.call_count == 1
    # Regression check: metadata records the actual fallback provider & model used!
    assert meta.provider == "fallback_gemini"
    assert meta.model == "gemini-2.5-pro"


def test_reliable_provider_fallback_json_generation() -> None:
    primary = StubProvider(
        provider_name="primary_provider",
        behaviors=[TextGenerationAuthError("Key expired")],
    )
    fallback = StubProvider(
        provider_name="fallback_provider",
        model_name="fallback-model",
        behaviors=[{"title": "Fallback Quiz"}],
    )

    reliable = ReliableTextGenerationProvider(
        [primary, fallback],
        max_attempts=2,
        backoff_base_seconds=0.001,
        backoff_max_seconds=0.01,
        max_concurrency=5,
    )

    result, meta = reliable.generate_json_with_metadata("Generate quiz")

    assert result == {"title": "Fallback Quiz"}
    assert meta.provider == "fallback_provider"
    assert meta.model == "fallback-model"


def test_reliable_provider_all_providers_fail() -> None:
    primary = StubProvider(behaviors=[TextGenerationAuthError("Key 1 invalid")])
    fallback = StubProvider(behaviors=[TextGenerationAuthError("Key 2 invalid")])

    reliable = ReliableTextGenerationProvider(
        [primary, fallback],
        max_attempts=2,
        backoff_base_seconds=0.001,
        backoff_max_seconds=0.01,
        max_concurrency=5,
    )

    with pytest.raises(TextGenerationError) as exc_info:
        reliable.generate_text("Prompt")

    assert exc_info.value.error_category == ErrorCategory.AUTHENTICATION_ERROR.value
    assert primary.call_count == 1
    assert fallback.call_count == 1


def test_reliable_provider_concurrency_protection() -> None:
    semaphore = threading.BoundedSemaphore(1)

    class SlowProvider:
        PROVIDER_NAME = "slow"
        MODEL = "slow-model"

        def generate_text_with_metadata(self, prompt: str):
            time.sleep(0.1)
            return "done", GenerationMetadata("slow", "slow-model")

    reliable = ReliableTextGenerationProvider(
        [SlowProvider()],
        semaphore=semaphore,
        max_attempts=1,
    )

    def worker():
        reliable.generate_text("prompt 1")

    thread = threading.Thread(target=worker)
    thread.start()

    time.sleep(0.01)

    # While thread is running and holding semaphore (capacity 1), second call must fail with GenerationConcurrencyError
    with pytest.raises(GenerationConcurrencyError) as exc_info:
        reliable.generate_text("prompt 2")

    assert exc_info.value.error_category == ErrorCategory.RATE_LIMIT.value
    assert "busy" in str(exc_info.value)

    thread.join()


def test_fallback_providers_configuration_parsing(monkeypatch) -> None:
    class DummyGemini:
        PROVIDER_NAME = "gemini"
        MODEL = "gemini-2.5-flash"

    monkeypatch.setattr(
        text_generation,
        "settings",
        SimpleNamespace(
            ai_provider="gemini",
            ai_fallback_providers="gemini",
            gemini_api_key="test-key",
            openai_api_key=None,
            anthropic_api_key=None,
            ai_generation_timeout_seconds=60,
            ai_generation_max_attempts=3,
            ai_generation_backoff_base_seconds=0.01,
            ai_generation_backoff_max_seconds=0.1,
            ai_generation_max_concurrency=10,
            ai_generation_overall_timeout_seconds=110,
        ),
    )
    monkeypatch.setattr(text_generation, "GeminiTextGenerationProvider", DummyGemini)

    provider = get_text_generation_provider()
    assert isinstance(provider, ReliableTextGenerationProvider)


def test_reliable_provider_does_not_retry_invalid_json() -> None:
    class BadJsonProvider:
        PROVIDER_NAME = "bad_json"
        MODEL = "bad-model"

        def __init__(self):
            self.call_count = 0

        def generate_json_with_metadata(self, prompt: str):
            self.call_count += 1
            raise TextGenerationError(
                "Gemini returned invalid JSON.",
                error_category=ErrorCategory.INVALID_STRUCTURE,
            )

    bad_provider = BadJsonProvider()
    reliable = ReliableTextGenerationProvider(
        [bad_provider],
        max_attempts=3,
        backoff_base_seconds=0.001,
        backoff_max_seconds=0.01,
    )

    with pytest.raises(TextGenerationError) as exc_info:
        reliable.generate_json("Generate JSON")

    assert exc_info.value.error_category == ErrorCategory.INVALID_STRUCTURE.value
    # Must NOT blindly retry deterministic invalid JSON within the same provider
    assert bad_provider.call_count == 1


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

    with pytest.raises(TextGenerationProviderError, match="HTTP 302"):
        provider.generate_text("Prompt")


def test_every_implemented_provider_is_constructible(monkeypatch) -> None:
    monkeypatch.setattr(
        text_generation.genai,
        "Client",
        lambda **kwargs: SimpleNamespace(models=None),
    )

    for provider_name in IMPLEMENTED_AI_PROVIDERS:
        monkeypatch.setattr(
            text_generation,
            "settings",
            SimpleNamespace(
                ai_provider=provider_name,
                ai_fallback_providers="",
                gemini_api_key="test-key",
                openai_api_key="test-key",
                anthropic_api_key="test-key",
                ollama_base_url="http://ollama.test:11434",
                ollama_model="llama3.1",
                ai_generation_timeout_seconds=60,
                ai_generation_max_attempts=3,
                ai_generation_backoff_base_seconds=0.01,
                ai_generation_backoff_max_seconds=0.1,
                ai_generation_max_concurrency=10,
                ai_generation_overall_timeout_seconds=110,
                ollama_temperature=0.2,
                ollama_top_p=0.9,
                ollama_num_ctx=8192,
                ollama_num_predict=4096,
                ollama_repeat_penalty=1.1,
            ),
        )

        provider = text_generation.get_text_generation_provider()

        assert isinstance(provider, ReliableTextGenerationProvider)
        assert provider.providers, f"{provider_name} produced no provider instance"


def test_connection_error_keeps_provider_taxonomy_and_is_retryable() -> None:
    error = TextGenerationConnectionError()

    assert isinstance(error, TextGenerationProviderError)
    assert isinstance(error, TextGenerationError)
    assert error.error_category == ErrorCategory.PROVIDER_ERROR.value
    assert is_transient_generation_error(error)


def test_unreachable_ollama_is_retried_then_falls_back(monkeypatch) -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    unreachable = _ollama_provider(monkeypatch, refuse)
    healthy = StubProvider(provider_name="gemini", model_name="gemini-3.6-flash")

    reliable = ReliableTextGenerationProvider(
        [unreachable, healthy],
        max_attempts=2,
        backoff_base_seconds=0.001,
        backoff_max_seconds=0.01,
        max_concurrency=4,
        overall_timeout_seconds=110,
    )

    text, metadata = reliable.generate_text_with_metadata("Prompt")

    assert metadata.provider == "gemini"
    assert text == "Response for Prompt"


def test_openai_text_generation_provider_success() -> None:
    captured = {}

    class MockResponses:
        def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                output_text="OpenAI generated text",
                usage=SimpleNamespace(
                    input_tokens=15,
                    output_tokens=25,
                    total_tokens=40,
                ),
            )

    mock_client = SimpleNamespace(responses=MockResponses())
    provider = OpenAITextGenerationProvider(
        api_key="test-key",
        model="gpt-5.6-terra",
        client=mock_client,
    )

    assert provider.MODEL == "gpt-5.6-terra"
    assert provider.PROVIDER_NAME == "openai"

    text, metadata = provider.generate_text_with_metadata("Hello OpenAI")
    assert text == "OpenAI generated text"
    assert metadata.provider == "openai"
    assert metadata.model == "gpt-5.6-terra"
    assert metadata.prompt_tokens == 15
    assert metadata.completion_tokens == 25
    assert metadata.total_tokens == 40
    assert captured["model"] == "gpt-5.6-terra"
    assert captured["input"] == "Hello OpenAI"


def test_openai_text_generation_provider_json() -> None:
    captured = {}

    class MockResponses:
        def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                output_text='{"quiz": "sample", "questions": [1, 2, 3]}',
                usage=SimpleNamespace(
                    input_tokens=10,
                    output_tokens=20,
                    total_tokens=30,
                ),
            )

    mock_client = SimpleNamespace(responses=MockResponses())
    provider = OpenAITextGenerationProvider(
        api_key="test-key",
        model="gpt-5.6-terra",
        client=mock_client,
    )

    result, metadata = provider.generate_json_with_metadata("Generate quiz JSON")
    assert result == {"quiz": "sample", "questions": [1, 2, 3]}
    assert metadata.provider == "openai"
    assert "text" in captured
    assert captured["text"]["format"]["type"] == "json_schema"


def test_openai_text_generation_provider_errors() -> None:
    import openai

    req = httpx.Request("POST", "http://test")

    def make_provider_failing_with(exc):
        class FailingResponses:
            def create(self, **kwargs):
                raise exc

        return OpenAITextGenerationProvider(
            api_key="test-key",
            client=SimpleNamespace(responses=FailingResponses()),
        )

    # APITimeoutError -> TextGenerationTimeoutError
    p = make_provider_failing_with(openai.APITimeoutError(request=req))
    with pytest.raises(TextGenerationTimeoutError):
        p.generate_text("test")

    # RateLimitError -> TextGenerationRateLimitError
    res429 = httpx.Response(429, request=req)
    p = make_provider_failing_with(openai.RateLimitError("quota", response=res429, body=None))
    with pytest.raises(TextGenerationRateLimitError):
        p.generate_text("test")

    # APIConnectionError -> TextGenerationConnectionError
    p = make_provider_failing_with(openai.APIConnectionError(request=req))
    with pytest.raises(TextGenerationConnectionError) as exc_info:
        p.generate_text("test")
    assert is_transient_generation_error(exc_info.value)

    # APIStatusError 401 -> TextGenerationAuthError
    res401 = httpx.Response(401, request=req)
    p = make_provider_failing_with(openai.APIStatusError("unauth", response=res401, body=None))
    with pytest.raises(TextGenerationAuthError):
        p.generate_text("test")

    # APIStatusError 500 -> TextGenerationProviderError (transient)
    res500 = httpx.Response(500, request=req)
    p = make_provider_failing_with(openai.APIStatusError("server error", response=res500, body=None))
    with pytest.raises(TextGenerationProviderError) as exc_info:
        p.generate_text("test")
    assert is_transient_generation_error(exc_info.value)

    # Empty response
    class EmptyResponses:
        def create(self, **kwargs):
            return SimpleNamespace(output_text="", usage=None)

    p = OpenAITextGenerationProvider(
        api_key="test-key",
        client=SimpleNamespace(responses=EmptyResponses()),
    )
    with pytest.raises(TextGenerationEmptyResponseError):
        p.generate_text("test")


def test_claude_text_generation_provider_success() -> None:
    captured = {}

    class MockMessages:
        def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                content=[SimpleNamespace(type="text", text="Claude response text")],
                usage=SimpleNamespace(
                    input_tokens=18,
                    output_tokens=32,
                ),
            )

    mock_client = SimpleNamespace(messages=MockMessages())
    provider = ClaudeTextGenerationProvider(
        api_key="test-key",
        model="claude-sonnet-5",
        client=mock_client,
    )

    assert provider.MODEL == "claude-sonnet-5"
    assert provider.PROVIDER_NAME == "claude"

    text, metadata = provider.generate_text_with_metadata("Hello Claude")
    assert text == "Claude response text"
    assert metadata.provider == "claude"
    assert metadata.model == "claude-sonnet-5"
    assert metadata.prompt_tokens == 18
    assert metadata.completion_tokens == 32
    assert metadata.total_tokens == 50
    assert captured["model"] == "claude-sonnet-5"
    assert captured["messages"] == [{"role": "user", "content": "Hello Claude"}]


def test_claude_text_generation_provider_json() -> None:
    captured = {}

    class MockMessages:
        def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                content=[
                    SimpleNamespace(
                        type="text",
                        text='{"summary": "Study guide output", "points": [1, 2]}',
                    )
                ],
                usage=SimpleNamespace(
                    input_tokens=12,
                    output_tokens=22,
                ),
            )

    mock_client = SimpleNamespace(messages=MockMessages())
    provider = ClaudeTextGenerationProvider(
        api_key="test-key",
        model="claude-sonnet-5",
        client=mock_client,
    )

    result, metadata = provider.generate_json_with_metadata("Generate JSON guide")
    assert result == {"summary": "Study guide output", "points": [1, 2]}
    assert metadata.provider == "claude"
    assert "output_config" in captured
    assert captured["output_config"]["format"]["type"] == "json_schema"
    assert "name" not in captured["output_config"]["format"]


def test_claude_text_generation_provider_errors() -> None:
    import anthropic

    req = httpx.Request("POST", "http://test")

    def make_provider_failing_with(exc):
        class FailingMessages:
            def create(self, **kwargs):
                raise exc

        return ClaudeTextGenerationProvider(
            api_key="test-key",
            client=SimpleNamespace(messages=FailingMessages()),
        )

    # APITimeoutError -> TextGenerationTimeoutError
    p = make_provider_failing_with(anthropic.APITimeoutError(request=req))
    with pytest.raises(TextGenerationTimeoutError):
        p.generate_text("test")

    # RateLimitError -> TextGenerationRateLimitError
    res429 = httpx.Response(429, request=req)
    p = make_provider_failing_with(anthropic.RateLimitError("quota", response=res429, body=None))
    with pytest.raises(TextGenerationRateLimitError):
        p.generate_text("test")

    # APIConnectionError -> TextGenerationConnectionError
    p = make_provider_failing_with(anthropic.APIConnectionError(request=req))
    with pytest.raises(TextGenerationConnectionError) as exc_info:
        p.generate_text("test")
    assert is_transient_generation_error(exc_info.value)

    # APIStatusError 401 -> TextGenerationAuthError
    res401 = httpx.Response(401, request=req)
    p = make_provider_failing_with(anthropic.APIStatusError("unauth", response=res401, body=None))
    with pytest.raises(TextGenerationAuthError):
        p.generate_text("test")

    # APIStatusError 503 -> TextGenerationProviderError (transient)
    res503 = httpx.Response(503, request=req)
    p = make_provider_failing_with(anthropic.APIStatusError("service unavailable", response=res503, body=None))
    with pytest.raises(TextGenerationProviderError) as exc_info:
        p.generate_text("test")
    assert is_transient_generation_error(exc_info.value)

    # Empty response
    class EmptyMessages:
        def create(self, **kwargs):
            return SimpleNamespace(content=[], usage=None)

    p = ClaudeTextGenerationProvider(
        api_key="test-key",
        client=SimpleNamespace(messages=EmptyMessages()),
    )
    with pytest.raises(TextGenerationEmptyResponseError):
        p.generate_text("test")


def test_multi_provider_fallback_chain() -> None:
    # 1st provider (Gemini): connection error (transient, retries and fails)
    p1 = StubProvider(
        provider_name="gemini",
        model_name="gemini-3.6-flash",
        behaviors=[TextGenerationConnectionError("Gemini down"), TextGenerationConnectionError("Gemini down")],
    )
    # 2nd provider (OpenAI): 500 error (transient, retries and fails)
    p2 = StubProvider(
        provider_name="openai",
        model_name="gpt-5.6-terra",
        behaviors=[TextGenerationProviderError("OpenAI 500"), TextGenerationProviderError("OpenAI 500")],
    )
    # 3rd provider (Claude): succeeds
    p3 = StubProvider(
        provider_name="claude",
        model_name="claude-sonnet-5",
    )

    reliable = ReliableTextGenerationProvider(
        [p1, p2, p3],
        max_attempts=2,
        backoff_base_seconds=0.001,
        backoff_max_seconds=0.01,
    )

    text, metadata = reliable.generate_text_with_metadata("Explain machine learning")
    assert text == "Response for Explain machine learning"
    assert metadata.provider == "claude"
    assert metadata.model == "claude-sonnet-5"
    assert p1.call_count == 2
    assert p2.call_count == 2
    assert p3.call_count == 1
