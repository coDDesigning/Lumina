import json
import logging
import threading
import time
from dataclasses import dataclass
from typing import Protocol

import httpx
from google import genai
from google.genai import errors as genai_errors, types
from tenacity import (
    RetryError,
    Retrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from backend.app.config import (
    AI_PROVIDER_GEMINI,
    AI_PROVIDER_OLLAMA,
    settings,
)
from schemas.ai_usage import ErrorCategory

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GenerationMetadata:
    """Operational telemetry metadata for an AI model call."""

    provider: str
    model: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    latency_ms: int | None = None


class TextGenerationProvider(Protocol):
    def generate_text(self, prompt: str) -> str: ...

    def generate_json(self, prompt: str) -> dict[str, object]: ...

    def generate_text_with_metadata(
        self, prompt: str
    ) -> tuple[str, GenerationMetadata]: ...

    def generate_json_with_metadata(
        self, prompt: str
    ) -> tuple[dict[str, object], GenerationMetadata]: ...


class TextGenerationError(RuntimeError):
    """The configured text generation provider failed."""

    def __init__(
        self,
        message: str,
        *,
        error_category: str | ErrorCategory = ErrorCategory.PROVIDER_ERROR,
    ) -> None:
        super().__init__(message)
        self.error_category = (
            error_category.value
            if isinstance(error_category, ErrorCategory)
            else str(error_category)
        )


class TextGenerationTimeoutError(TextGenerationError):
    """Text generation request timed out."""

    def __init__(self, message: str = "Text generation timed out.") -> None:
        super().__init__(message, error_category=ErrorCategory.TIMEOUT)


class TextGenerationRateLimitError(TextGenerationError):
    """Text generation rate limit exceeded."""

    def __init__(self, message: str = "Text generation rate limit exceeded.") -> None:
        super().__init__(message, error_category=ErrorCategory.RATE_LIMIT)


class GenerationConcurrencyError(TextGenerationRateLimitError):
    """Generation capacity reached maximum concurrent requests."""

    def __init__(
        self,
        message: str = "The generation service is currently busy. Please try again in a few moments.",
    ) -> None:
        super().__init__(message)


class TextGenerationAuthError(TextGenerationError):
    """Text generation authentication/authorization failure."""

    def __init__(self, message: str = "Text generation authentication failed.") -> None:
        super().__init__(message, error_category=ErrorCategory.AUTHENTICATION_ERROR)


class TextGenerationEmptyResponseError(TextGenerationError):
    """Provider returned an empty response."""

    def __init__(
        self, message: str = "Text generation returned an empty response."
    ) -> None:
        super().__init__(message, error_category=ErrorCategory.EMPTY_RESPONSE)


class TextGenerationProviderError(TextGenerationError):
    """Generic or upstream provider error."""

    def __init__(self, message: str = "Text generation provider failed.") -> None:
        super().__init__(message, error_category=ErrorCategory.PROVIDER_ERROR)


def is_transient_generation_error(exc: Exception) -> bool:
    """Classify whether an exception is transient and safe to retry."""
    if isinstance(
        exc,
        (
            TextGenerationTimeoutError,
            TimeoutError,
            httpx.TimeoutException,
            httpx.NetworkError,
            httpx.ConnectError,
            httpx.ReadTimeout,
        ),
    ):
        return True

    if isinstance(exc, genai_errors.APIError):
        code = getattr(exc, "code", None)
        return code in {429, 500, 502, 503, 504}

    if isinstance(exc, genai_errors.ServerError):
        return True

    if isinstance(exc, TextGenerationRateLimitError) and not isinstance(
        exc, GenerationConcurrencyError
    ):
        return True

    if isinstance(exc, TextGenerationProviderError):
        cause = exc.__cause__
        if cause is not None:
            return is_transient_generation_error(cause)
        return True

    return False


class TextGenerationConnectionError(TextGenerationProviderError):
    """The configured text generation provider could not be reached.

    Subclasses the generic provider error so it keeps the PROVIDER_ERROR
    telemetry category and stays retryable, while remaining distinctly
    catchable by the API layer, which answers 503 rather than 500 when the
    model server is simply not there.
    """

    def __init__(
        self, message: str = "The text generation provider is unreachable."
    ) -> None:
        super().__init__(message)


def _strip_markdown_fence(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped

    without_opening = stripped[3:]
    newline_index = without_opening.find("\n")
    if newline_index == -1:
        return stripped

    first_line = without_opening[:newline_index].strip()
    if first_line and not first_line.isalnum():
        return stripped

    body = without_opening[newline_index + 1 :]
    closing_index = body.rfind("```")
    if closing_index == -1:
        return stripped

    return body[:closing_index].strip()


def _parse_json_object(text: str, provider_label: str) -> dict[str, object]:
    try:
        result = json.loads(_strip_markdown_fence(text))
    except json.JSONDecodeError as exc:
        raise TextGenerationError(
            f"{provider_label} returned invalid JSON.",
            error_category=ErrorCategory.INVALID_STRUCTURE,
        ) from exc

    if not isinstance(result, dict):
        raise TextGenerationError(
            f"{provider_label} response must be a JSON object.",
            error_category=ErrorCategory.INVALID_STRUCTURE,
        )

    return result


class GeminiTextGenerationProvider:
    MODEL = "gemini-3.6-flash"
    PROVIDER_NAME = "gemini"

    def __init__(
        self,
        api_key: str | None = None,
        timeout_seconds: int | None = None,
    ) -> None:
        key = api_key or settings.gemini_api_key
        if not key:
            raise TextGenerationAuthError("GEMINI_API_KEY is not configured.")

        timeout_sec = (
            timeout_seconds
            if timeout_seconds is not None
            else settings.ai_generation_timeout_seconds
        )
        http_opts = types.HttpOptions(timeout=int(timeout_sec * 1000))
        self._client = genai.Client(api_key=key, http_options=http_opts)

    def _extract_metadata(
        self, response: object, latency_ms: int
    ) -> GenerationMetadata:
        prompt_tokens = None
        completion_tokens = None
        total_tokens = None

        usage_metadata = getattr(response, "usage_metadata", None)
        if usage_metadata is not None:
            prompt_tokens = getattr(usage_metadata, "prompt_token_count", None)
            completion_tokens = getattr(usage_metadata, "candidates_token_count", None)
            total_tokens = getattr(usage_metadata, "total_token_count", None)

        return GenerationMetadata(
            provider=self.PROVIDER_NAME,
            model=self.MODEL,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            latency_ms=latency_ms,
        )

    def _handle_client_error(self, exc: Exception) -> None:
        if isinstance(exc, TextGenerationError):
            raise exc
        if isinstance(exc, (TimeoutError, httpx.TimeoutException)):
            raise TextGenerationTimeoutError("Gemini request timed out.") from exc
        if isinstance(exc, genai_errors.APIError):
            if exc.code == 429:
                raise TextGenerationRateLimitError(
                    "Gemini rate limit exceeded."
                ) from exc
            if exc.code in {401, 403}:
                raise TextGenerationAuthError("Gemini authentication failed.") from exc
            if exc.code in {500, 502, 503, 504}:
                raise TextGenerationProviderError(
                    "Gemini service unavailable."
                ) from exc
            raise TextGenerationProviderError("Gemini text generation failed.") from exc
        if isinstance(exc, genai_errors.ServerError):
            raise TextGenerationProviderError("Gemini server error.") from exc
        if isinstance(exc, (httpx.NetworkError, httpx.ConnectError)):
            raise TextGenerationProviderError("Gemini connection error.") from exc
        raise TextGenerationProviderError("Gemini text generation failed.") from exc

    def generate_text_with_metadata(
        self, prompt: str
    ) -> tuple[str, GenerationMetadata]:
        start_time = time.perf_counter()
        try:
            response = self._client.models.generate_content(
                model=self.MODEL,
                contents=prompt,
            )
        except Exception as exc:
            self._handle_client_error(exc)

        latency_ms = int((time.perf_counter() - start_time) * 1000)

        if not response or not response.text:
            raise TextGenerationEmptyResponseError("Gemini returned an empty response.")

        metadata = self._extract_metadata(response, latency_ms)
        return response.text, metadata

    def generate_text(self, prompt: str) -> str:
        text, _ = self.generate_text_with_metadata(prompt)
        return text

    def generate_json_with_metadata(
        self, prompt: str
    ) -> tuple[dict[str, object], GenerationMetadata]:
        start_time = time.perf_counter()
        try:
            response = self._client.models.generate_content(
                model=self.MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                ),
            )
        except Exception as exc:
            self._handle_client_error(exc)

        latency_ms = int((time.perf_counter() - start_time) * 1000)

        if not response or not response.text:
            raise TextGenerationEmptyResponseError("Gemini returned an empty response.")

        result = _parse_json_object(response.text, "Gemini")

        metadata = self._extract_metadata(response, latency_ms)
        return result, metadata

    def generate_json(self, prompt: str) -> dict[str, object]:
        result, _ = self.generate_json_with_metadata(prompt)
        return result


_shared_http_client: httpx.Client | None = None


def _get_shared_http_client() -> httpx.Client:
    """Return the process-wide HTTP client used for self-hosted providers.

    A provider instance is built per request, so creating a client per
    provider would leak one connection pool per generation. httpx clients
    are safe to share across threads.
    """
    global _shared_http_client
    if _shared_http_client is None:
        _shared_http_client = httpx.Client()
    return _shared_http_client


class OllamaTextGenerationProvider:
    PROVIDER_NAME = "ollama"
    GENERATE_PATH = "/api/generate"

    def __init__(
        self,
        client: httpx.Client | None = None,
        timeout_seconds: int | None = None,
    ) -> None:
        self._base_url = settings.ollama_base_url
        self._model = settings.ollama_model
        self._timeout_seconds = (
            timeout_seconds
            if timeout_seconds is not None
            else settings.ai_generation_timeout_seconds
        )
        self._client = client or _get_shared_http_client()

    def _request(self, prompt: str, *, as_json: bool) -> tuple[str, dict[str, object]]:
        payload: dict[str, object] = {
            "model": self._model,
            "prompt": prompt,
            "stream": False,
        }
        if as_json:
            payload["format"] = "json"

        try:
            response = self._client.post(
                f"{self._base_url}{self.GENERATE_PATH}",
                json=payload,
                timeout=self._timeout_seconds,
            )
        except httpx.TimeoutException as exc:
            raise TextGenerationTimeoutError(
                "Ollama did not respond within the configured timeout."
            ) from exc
        except httpx.TransportError as exc:
            raise TextGenerationConnectionError(
                "Ollama could not be reached at the configured base URL."
            ) from exc

        if response.status_code == 429:
            raise TextGenerationRateLimitError("Ollama rate limit exceeded.")

        if not response.is_success:
            raise TextGenerationProviderError(
                f"Ollama returned HTTP {response.status_code}."
            )

        try:
            envelope = response.json()
        except ValueError as exc:
            raise TextGenerationProviderError(
                "Ollama returned a response that is not valid JSON."
            ) from exc

        if not isinstance(envelope, dict):
            raise TextGenerationProviderError(
                "Ollama returned an unexpected response structure."
            )

        generated = envelope.get("response")
        if not isinstance(generated, str):
            raise TextGenerationProviderError(
                "Ollama returned an unexpected response structure."
            )

        if not generated.strip():
            raise TextGenerationEmptyResponseError("Ollama returned an empty response.")

        return generated, envelope

    def _extract_metadata(
        self, envelope: dict[str, object], latency_ms: int
    ) -> GenerationMetadata:
        prompt_tokens = envelope.get("prompt_eval_count")
        completion_tokens = envelope.get("eval_count")
        if not isinstance(prompt_tokens, int):
            prompt_tokens = None
        if not isinstance(completion_tokens, int):
            completion_tokens = None

        total_tokens = None
        if prompt_tokens is not None or completion_tokens is not None:
            total_tokens = (prompt_tokens or 0) + (completion_tokens or 0)

        return GenerationMetadata(
            provider=self.PROVIDER_NAME,
            model=self._model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            latency_ms=latency_ms,
        )

    def generate_text_with_metadata(
        self, prompt: str
    ) -> tuple[str, GenerationMetadata]:
        start_time = time.perf_counter()
        generated, envelope = self._request(prompt, as_json=False)
        latency_ms = int((time.perf_counter() - start_time) * 1000)

        return generated, self._extract_metadata(envelope, latency_ms)

    def generate_text(self, prompt: str) -> str:
        text, _ = self.generate_text_with_metadata(prompt)
        return text

    def generate_json_with_metadata(
        self, prompt: str
    ) -> tuple[dict[str, object], GenerationMetadata]:
        start_time = time.perf_counter()
        generated, envelope = self._request(prompt, as_json=True)
        latency_ms = int((time.perf_counter() - start_time) * 1000)

        result = _parse_json_object(generated, "Ollama")
        return result, self._extract_metadata(envelope, latency_ms)

    def generate_json(self, prompt: str) -> dict[str, object]:
        result, _ = self.generate_json_with_metadata(prompt)
        return result


def configured_provider_identity() -> tuple[str, str]:
    """Report the provider name and model the application is configured to use."""
    if settings.ai_provider == AI_PROVIDER_OLLAMA:
        return AI_PROVIDER_OLLAMA, settings.ollama_model

    return AI_PROVIDER_GEMINI, GeminiTextGenerationProvider.MODEL


def model_identifier(metadata: GenerationMetadata | None) -> str:
    if metadata is not None:
        return f"{metadata.provider}:{metadata.model}"

    provider, model = configured_provider_identity()
    return f"{provider}:{model}"


_shared_generation_semaphore: threading.BoundedSemaphore | None = None
_shared_generation_semaphore_lock = threading.Lock()


def get_shared_generation_semaphore(
    max_concurrency: int | None = None,
) -> threading.BoundedSemaphore:
    global _shared_generation_semaphore
    limit = (
        max_concurrency
        if max_concurrency is not None
        else settings.ai_generation_max_concurrency
    )
    with _shared_generation_semaphore_lock:
        if _shared_generation_semaphore is None:
            _shared_generation_semaphore = threading.BoundedSemaphore(limit)
        return _shared_generation_semaphore


def reset_shared_generation_semaphore(
    max_concurrency: int | None = None,
) -> threading.BoundedSemaphore:
    global _shared_generation_semaphore
    limit = (
        max_concurrency
        if max_concurrency is not None
        else settings.ai_generation_max_concurrency
    )
    with _shared_generation_semaphore_lock:
        _shared_generation_semaphore = threading.BoundedSemaphore(limit)
        return _shared_generation_semaphore


class ReliableTextGenerationProvider:
    """Shared resilience layer managing timeouts, retries, backoff, concurrency, and fallback."""

    def __init__(
        self,
        providers: list[TextGenerationProvider],
        *,
        max_attempts: int | None = None,
        backoff_base_seconds: float | None = None,
        backoff_max_seconds: float | None = None,
        max_concurrency: int | None = None,
        semaphore: threading.BoundedSemaphore | None = None,
    ) -> None:
        if not providers:
            raise TextGenerationError(
                "At least one text generation provider must be configured."
            )
        self.providers = list(providers)
        self.max_attempts = (
            max_attempts
            if max_attempts is not None
            else settings.ai_generation_max_attempts
        )
        self.backoff_base_seconds = (
            backoff_base_seconds
            if backoff_base_seconds is not None
            else settings.ai_generation_backoff_base_seconds
        )
        self.backoff_max_seconds = (
            backoff_max_seconds
            if backoff_max_seconds is not None
            else settings.ai_generation_backoff_max_seconds
        )
        if semaphore is not None:
            self._semaphore = semaphore
        elif max_concurrency is not None:
            self._semaphore = threading.BoundedSemaphore(max_concurrency)
        else:
            self._semaphore = get_shared_generation_semaphore()

    def _execute_with_resilience(
        self,
        method_name: str,
        prompt: str,
    ) -> tuple[str | dict[str, object], GenerationMetadata]:
        acquired = self._semaphore.acquire(blocking=False)
        if not acquired:
            raise GenerationConcurrencyError()

        try:
            last_exception: Exception | None = None

            for provider in self.providers:
                retryer = Retrying(
                    stop=stop_after_attempt(self.max_attempts),
                    wait=wait_exponential(
                        multiplier=self.backoff_base_seconds,
                        max=self.backoff_max_seconds,
                    ),
                    retry=retry_if_exception(is_transient_generation_error),
                    reraise=True,
                )

                try:
                    for attempt in retryer:
                        with attempt:
                            if hasattr(provider, method_name):
                                return getattr(provider, method_name)(prompt)
                            if method_name == "generate_text_with_metadata":
                                text = provider.generate_text(prompt)
                                meta = GenerationMetadata(
                                    provider=getattr(
                                        provider, "PROVIDER_NAME", "unknown"
                                    ),
                                    model=getattr(provider, "MODEL", "unknown"),
                                )
                                return text, meta
                            if method_name == "generate_json_with_metadata":
                                data = provider.generate_json(prompt)
                                meta = GenerationMetadata(
                                    provider=getattr(
                                        provider, "PROVIDER_NAME", "unknown"
                                    ),
                                    model=getattr(provider, "MODEL", "unknown"),
                                )
                                return data, meta
                            return getattr(provider, method_name)(prompt)
                except RetryError as exc:
                    last_exception = exc.last_attempt.exception() or exc
                    logger.warning(
                        "Provider %s exhausted %d attempts: %s",
                        getattr(provider, "PROVIDER_NAME", type(provider).__name__),
                        self.max_attempts,
                        last_exception,
                    )
                except Exception as exc:
                    last_exception = exc
                    logger.warning(
                        "Provider %s failed with %s: %s",
                        getattr(provider, "PROVIDER_NAME", type(provider).__name__),
                        type(exc).__name__,
                        exc,
                    )

            if isinstance(last_exception, TextGenerationError):
                raise last_exception
            raise TextGenerationError(
                "All configured AI providers failed.",
                error_category=getattr(
                    last_exception, "error_category", ErrorCategory.PROVIDER_ERROR
                ),
            ) from last_exception
        finally:
            self._semaphore.release()

    def generate_text_with_metadata(
        self, prompt: str
    ) -> tuple[str, GenerationMetadata]:
        result, metadata = self._execute_with_resilience(
            "generate_text_with_metadata", prompt
        )
        return str(result), metadata

    def generate_text(self, prompt: str) -> str:
        text, _ = self.generate_text_with_metadata(prompt)
        return text

    def generate_json_with_metadata(
        self, prompt: str
    ) -> tuple[dict[str, object], GenerationMetadata]:
        result, metadata = self._execute_with_resilience(
            "generate_json_with_metadata", prompt
        )
        if isinstance(result, dict):
            return result, metadata
        raise TextGenerationError(
            "Expected dict response from JSON generation.",
            error_category=ErrorCategory.INVALID_STRUCTURE,
        )

    def generate_json(self, prompt: str) -> dict[str, object]:
        result, _ = self.generate_json_with_metadata(prompt)
        return result


def get_available_models() -> list[dict[str, object]]:
    """Return the catalog of models actually available under the current deployment."""
    primary_name = settings.ai_provider
    provider_names = [primary_name]

    if settings.ai_fallback_providers:
        for fallback_token in (
            item.strip().lower()
            for item in settings.ai_fallback_providers.split(",")
            if item.strip()
        ):
            if fallback_token not in provider_names and fallback_token in (
                AI_PROVIDER_GEMINI,
                AI_PROVIDER_OLLAMA,
            ):
                provider_names.append(fallback_token)

    models: list[dict[str, object]] = []
    for prov in provider_names:
        if prov == AI_PROVIDER_GEMINI:
            model_name = GeminiTextGenerationProvider.MODEL
            models.append(
                {
                    "id": f"{prov}:{model_name}",
                    "provider": prov,
                    "model": model_name,
                    "display_name": f"Gemini ({model_name})",
                    "is_default": prov == primary_name,
                }
            )
        elif prov == AI_PROVIDER_OLLAMA:
            model_name = settings.ollama_model
            models.append(
                {
                    "id": f"{prov}:{model_name}",
                    "provider": prov,
                    "model": model_name,
                    "display_name": f"Ollama ({model_name})",
                    "is_default": prov == primary_name,
                }
            )
    return models


def resolve_effective_model(
    request_model: str | None = None,
    user_preferred_model: str | None = None,
) -> str:
    """Resolve the effective model using precedence rule:
    1. Explicit request override (if valid in catalog)
    2. User preferred model (if valid in catalog)
    3. Deployment default
    """
    catalog = get_available_models()
    valid_ids = {m["id"] for m in catalog}

    if request_model and request_model in valid_ids:
        return request_model
    if user_preferred_model and user_preferred_model in valid_ids:
        return user_preferred_model

    for m in catalog:
        if m.get("is_default"):
            return str(m["id"])

    if catalog:
        return str(catalog[0]["id"])

    prov, model = configured_provider_identity()
    return f"{prov}:{model}"


def _instantiate_provider(provider_name: str) -> TextGenerationProvider:
    clean_name = provider_name.strip().lower()
    if clean_name == AI_PROVIDER_GEMINI:
        return GeminiTextGenerationProvider()
    if clean_name == AI_PROVIDER_OLLAMA:
        return OllamaTextGenerationProvider()
    raise TextGenerationError(
        f"Text generation provider '{clean_name}' is not implemented.",
        error_category=ErrorCategory.PROVIDER_ERROR,
    )


def get_text_generation_provider(
    effective_model: str | None = None,
) -> TextGenerationProvider:
    primary_name = settings.ai_provider
    provider_names = [primary_name]

    if settings.ai_fallback_providers:
        for fallback_token in (
            item.strip().lower()
            for item in settings.ai_fallback_providers.split(",")
            if item.strip()
        ):
            if fallback_token not in provider_names:
                provider_names.append(fallback_token)

    if effective_model:
        preferred_provider = effective_model.split(":", 1)[0].strip().lower()
        if preferred_provider in provider_names:
            provider_names.remove(preferred_provider)
            provider_names.insert(0, preferred_provider)

    providers: list[TextGenerationProvider] = []
    for name in provider_names:
        providers.append(_instantiate_provider(name))

    return ReliableTextGenerationProvider(
        providers,
        semaphore=get_shared_generation_semaphore(),
    )
