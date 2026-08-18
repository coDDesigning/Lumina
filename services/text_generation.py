import json
import logging
import threading
import time
from dataclasses import dataclass
from typing import Protocol

from google import genai
from google.genai import errors as genai_errors, types
import httpx
from tenacity import (
    RetryError,
    Retrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from backend.app.config import settings
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


class GeminiTextGenerationProvider:
    MODEL = "gemini-2.5-flash"
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

        try:
            result = json.loads(response.text)
        except json.JSONDecodeError as exc:
            raise TextGenerationError(
                "Gemini returned invalid JSON.",
                error_category=ErrorCategory.INVALID_STRUCTURE,
            ) from exc

        if not isinstance(result, dict):
            raise TextGenerationError(
                "Gemini response must be a JSON object.",
                error_category=ErrorCategory.INVALID_STRUCTURE,
            )

        metadata = self._extract_metadata(response, latency_ms)
        return result, metadata

    def generate_json(self, prompt: str) -> dict[str, object]:
        result, _ = self.generate_json_with_metadata(prompt)
        return result


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


def _instantiate_provider(provider_name: str) -> TextGenerationProvider:
    clean_name = provider_name.strip().lower()
    if clean_name == "gemini":
        return GeminiTextGenerationProvider()
    raise TextGenerationError(
        f"Text generation provider '{clean_name}' is not implemented.",
        error_category=ErrorCategory.PROVIDER_ERROR,
    )


def get_text_generation_provider() -> TextGenerationProvider:
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

    providers: list[TextGenerationProvider] = []
    for name in provider_names:
        providers.append(_instantiate_provider(name))

    return ReliableTextGenerationProvider(
        providers,
        semaphore=get_shared_generation_semaphore(),
    )
