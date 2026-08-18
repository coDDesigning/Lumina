import threading
import time
from types import SimpleNamespace

from google.genai import errors as genai_errors
import httpx
import pytest

from schemas.ai_usage import ErrorCategory
import services.text_generation as text_generation
from services.text_generation import (
    GeminiTextGenerationProvider,
    GenerationConcurrencyError,
    GenerationMetadata,
    ReliableTextGenerationProvider,
    TextGenerationAuthError,
    TextGenerationEmptyResponseError,
    TextGenerationError,
    TextGenerationProviderError,
    TextGenerationRateLimitError,
    TextGenerationTimeoutError,
    get_text_generation_provider,
    is_transient_generation_error,
)


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
            ai_generation_timeout_seconds=60,
            ai_generation_max_attempts=3,
            ai_generation_backoff_base_seconds=0.01,
            ai_generation_backoff_max_seconds=0.1,
            ai_generation_max_concurrency=10,
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
            ai_provider="ollama",
            ai_fallback_providers="",
            gemini_api_key=None,
            ai_generation_timeout_seconds=60,
            ai_generation_max_attempts=3,
            ai_generation_backoff_base_seconds=0.01,
            ai_generation_backoff_max_seconds=0.1,
            ai_generation_max_concurrency=10,
        ),
    )

    with pytest.raises(TextGenerationError) as exc_info:
        text_generation.get_text_generation_provider()
    assert "not implemented" in str(exc_info.value)


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

    # Non-transient errors:
    assert not is_transient_generation_error(TextGenerationAuthError())
    assert not is_transient_generation_error(TextGenerationEmptyResponseError())
    assert not is_transient_generation_error(GenerationConcurrencyError())
    assert not is_transient_generation_error(genai_errors.APIError(400, "bad"))
    assert not is_transient_generation_error(genai_errors.APIError(401, "unauth"))
    assert not is_transient_generation_error(genai_errors.APIError(404, "notfound"))


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
            ai_generation_timeout_seconds=60,
            ai_generation_max_attempts=3,
            ai_generation_backoff_base_seconds=0.01,
            ai_generation_backoff_max_seconds=0.1,
            ai_generation_max_concurrency=10,
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
