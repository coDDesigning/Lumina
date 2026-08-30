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
    AI_PROVIDER_CLAUDE,
    AI_PROVIDER_GEMINI,
    AI_PROVIDER_OPENAI,
    AI_PROVIDER_OLLAMA,
    DEFAULT_CLAUDE_MODEL,
    DEFAULT_GEMINI_MODEL,
    DEFAULT_OPENAI_MODEL,
    settings,
)
from schemas.ai_usage import ErrorCategory
from utils.exceptions import BadRequestException

logger = logging.getLogger(__name__)
_shared_http_client: httpx.Client | None = None
_shared_http_client_lock = threading.Lock()


def _get_shared_http_client() -> httpx.Client:
    global _shared_http_client

    with _shared_http_client_lock:
        if _shared_http_client is None:
            _shared_http_client = httpx.Client()
        return _shared_http_client


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


class ProviderRegistry:
    """Central registry for text generation provider metadata and construction."""

    _registry: dict[str, dict] = {}

    @classmethod
    def register(
        cls,
        name: str,
        *,
        constructor: type,
        default_model: str,
        requires_key: bool,
        vendor: str,
        description: str,
        is_local: bool,
    ) -> None:
        cls._registry[name] = {
            "constructor": constructor,
            "default_model": default_model,
            "requires_key": requires_key,
            "vendor": vendor,
            "description": description,
            "is_local": is_local,
        }

    @classmethod
    def get(cls, name: str) -> dict | None:
        return cls._registry.get(name.lower())

    @classmethod
    def all_implemented(cls) -> list[str]:
        return list(cls._registry.keys())

    @classmethod
    def get_constructor(cls, name: str) -> type | None:
        entry = cls.get(name)
        return entry["constructor"] if entry else None

    @classmethod
    def get_default_model(cls, name: str) -> str | None:
        entry = cls.get(name)
        return entry["default_model"] if entry else None

    @classmethod
    def requires_key(cls, name: str) -> bool:
        entry = cls.get(name)
        return entry["requires_key"] if entry else False

    @classmethod
    def get_vendor(cls, name: str) -> str | None:
        entry = cls.get(name)
        return entry["vendor"] if entry else None

    @classmethod
    def get_description(cls, name: str) -> str | None:
        entry = cls.get(name)
        return entry["description"] if entry else None

    @classmethod
    def is_local(cls, name: str) -> bool:
        entry = cls.get(name)
        return entry["is_local"] if entry else False


def _model_catalog_entry(
    model_id: str, user: object | None = None
) -> dict[str, object] | None:
    for model in get_available_models(user=user):
        if model["id"] == model_id:
            return model
    if ":" in model_id:
        provider, model_name = model_id.split(":", 1)
        catalog_dict = getattr(settings, "ai_model_catalog", None) or {}
        for entry in catalog_dict.get(provider, []):
            if str(entry.get("model")) == model_name:
                vendor = ProviderRegistry.get_vendor(provider) or provider.title()
                is_local = ProviderRegistry.is_local(provider)
                is_json = bool(entry.get("json_mode", True))
                return {
                    "id": model_id,
                    "provider": provider,
                    "model": model_name,
                    "display_name": f"{vendor} ({model_name})",
                    "is_default": False,
                    "cost_hint": (
                        "Local execution · Unmetered"
                        if is_local
                        else "Metered (1-2 credits)"
                    ),
                    "capabilities": [
                        "study_guide",
                        "quiz",
                        "flashcard",
                        "ai_tutor",
                        "course_qa",
                        "prompt_generator",
                    ],
                    "description": f"{vendor} ({model_name})",
                    "is_local": is_local,
                    "supports_json": is_json,
                    "json_mode": is_json,
                    "context_window": int(entry.get("context_window", 8192)),
                    "vision": bool(entry.get("vision", False)),
                }
    return None


class UnavailableModelError(ValueError):
    """Requested AI model is not available in the deployment catalog."""


class IncompatibleModelError(ValueError):
    """Requested AI model does not support the required capability."""


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


class PersonalKeyAuthError(TextGenerationAuthError):
    """Personal/BYOK API key authentication failure (disables silent fallback)."""

    def __init__(
        self,
        message: str = "Your personal API key is invalid or expired.",
        provider: str | None = None,
    ) -> None:
        super().__init__(message)
        self.provider = provider


def _vendor_display_name(provider_name: str) -> str:
    clean = provider_name.strip().lower()
    mapping = {
        "openai": "OpenAI",
        "gemini": "Google Gemini",
        "claude": "Anthropic Claude",
        "anthropic": "Anthropic Claude",
        "ollama": "Ollama",
    }
    return mapping.get(clean, clean.title())


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
    if isinstance(exc, TextGenerationConnectionError):
        return True

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

    try:
        import openai

        openai_errors = tuple(
            getattr(openai, attr)
            for attr in (
                "APIConnectionError",
                "APITimeoutError",
                "RateLimitError",
                "InternalServerError",
            )
            if hasattr(openai, attr)
        )
        if openai_errors and isinstance(exc, openai_errors):
            return True
        status_err = getattr(openai, "APIStatusError", None)
        if status_err and isinstance(exc, status_err):
            code = getattr(exc, "status_code", None)
            return code in {429, 500, 502, 503, 504}
    except (ImportError, AttributeError):
        pass

    try:
        import anthropic

        anthropic_errors = tuple(
            getattr(anthropic, attr)
            for attr in (
                "APIConnectionError",
                "APITimeoutError",
                "RateLimitError",
                "InternalServerError",
            )
            if hasattr(anthropic, attr)
        )
        if anthropic_errors and isinstance(exc, anthropic_errors):
            return True
        status_err = getattr(anthropic, "APIStatusError", None)
        if status_err and isinstance(exc, status_err):
            code = getattr(exc, "status_code", None)
            return code in {429, 500, 502, 503, 504}
    except (ImportError, AttributeError):
        pass

    status_code = getattr(exc, "status_code", getattr(exc, "code", None))
    if isinstance(status_code, int) and status_code in {429, 500, 502, 503, 504}:
        return True

    exc_name = type(exc).__name__.lower()
    if "connection" in exc_name or "timeout" in exc_name:
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
    MODEL = DEFAULT_GEMINI_MODEL
    PROVIDER_NAME = "gemini"

    def __init__(
        self,
        api_key: str | None = None,
        timeout_seconds: int | None = None,
        model: str | None = None,
    ) -> None:
        key = api_key or settings.gemini_api_key
        if not key:
            raise TextGenerationAuthError("GEMINI_API_KEY is not configured.")

        self._model = model or self.MODEL

        timeout_sec = (
            timeout_seconds
            if timeout_seconds is not None
            else settings.ai_generation_timeout_seconds
        )
        http_opts = types.HttpOptions(timeout=int(timeout_sec * 1000))
        self._client = genai.Client(api_key=key, http_options=http_opts)
        self._model = model or self.MODEL

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
            model=self._model,
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
                model=self._model,
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
                model=self._model,
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


class OllamaTextGenerationProvider:
    PROVIDER_NAME = "ollama"
    GENERATE_PATH = "/api/generate"

    def __init__(
        self,
        client: httpx.Client | None = None,
        timeout_seconds: int | None = None,
        model: str | None = None,
        base_url: str | None = None,
    ) -> None:
        from services.embeddings import resolve_ollama_base_url

        self._base_url = resolve_ollama_base_url(base_url or settings.ollama_base_url)
        self._model = model or settings.ollama_model
        self._timeout_seconds = (
            timeout_seconds
            if timeout_seconds is not None
            else settings.ai_generation_timeout_seconds
        )
        self._options = {
            "temperature": settings.ollama_temperature,
            "top_p": settings.ollama_top_p,
            "num_ctx": settings.ollama_num_ctx,
            "num_predict": settings.ollama_num_predict,
            "repeat_penalty": settings.ollama_repeat_penalty,
        }
        self._client = client or _get_shared_http_client()

    def _request(self, prompt: str, *, as_json: bool) -> tuple[str, dict[str, object]]:
        payload: dict[str, object] = {
            "model": self._model,
            "prompt": prompt,
            "stream": False,
            "options": dict(self._options),
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


class OpenAITextGenerationProvider:
    MODEL = DEFAULT_OPENAI_MODEL
    PROVIDER_NAME = "openai"

    def __init__(
        self,
        api_key: str | None = None,
        timeout_seconds: int | None = None,
        model: str | None = None,
        client: object | None = None,
    ) -> None:
        key = api_key or settings.openai_api_key
        if not key and client is None:
            raise TextGenerationAuthError("OPENAI_API_KEY is not configured.")

        self._model = model or self.MODEL
        self._timeout_seconds = (
            timeout_seconds
            if timeout_seconds is not None
            else settings.ai_generation_timeout_seconds
        )
        if client is not None:
            self._client = client
        else:
            try:
                from openai import OpenAI

                self._client = OpenAI(api_key=key, timeout=self._timeout_seconds)
            except (ImportError, AttributeError):
                self._client = None

    def _extract_metadata(
        self, response: object, latency_ms: int
    ) -> GenerationMetadata:
        prompt_tokens = None
        completion_tokens = None
        total_tokens = None

        usage = getattr(response, "usage", None)
        if usage is not None:
            prompt_tokens = getattr(usage, "input_tokens", None)
            completion_tokens = getattr(usage, "output_tokens", None)
            total_tokens = getattr(usage, "total_tokens", None)

        return GenerationMetadata(
            provider=self.PROVIDER_NAME,
            model=self._model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            latency_ms=latency_ms,
        )

    def _handle_client_error(self, exc: Exception) -> None:
        if isinstance(exc, TextGenerationError):
            raise exc
        try:
            from openai import (
                APITimeoutError,
                RateLimitError as OpenAIRateLimitError,
                APIStatusError,
                APIConnectionError,
            )

            if isinstance(exc, APITimeoutError):
                raise TextGenerationTimeoutError("OpenAI request timed out.") from exc
            if isinstance(exc, OpenAIRateLimitError):
                raise TextGenerationRateLimitError("OpenAI rate limit exceeded.") from exc
            if isinstance(exc, APIStatusError):
                if exc.status_code in {401, 403}:
                    raise TextGenerationAuthError("OpenAI authentication failed.") from exc
                if exc.status_code in {429}:
                    raise TextGenerationRateLimitError(
                        "OpenAI rate limit exceeded."
                    ) from exc
                if exc.status_code in {500, 502, 503, 504}:
                    raise TextGenerationProviderError(
                        "OpenAI service unavailable."
                    ) from exc
                raise TextGenerationProviderError("OpenAI text generation failed.") from exc
            if isinstance(exc, APIConnectionError):
                raise TextGenerationConnectionError("OpenAI could not be reached.") from exc
        except (ImportError, ModuleNotFoundError, AttributeError):
            pass

        status_code = getattr(exc, "status_code", getattr(exc, "code", None))
        if status_code in {401, 403}:
            raise TextGenerationAuthError("OpenAI authentication failed.") from exc
        if status_code == 429:
            raise TextGenerationRateLimitError("OpenAI rate limit exceeded.") from exc
        if status_code in {500, 502, 503, 504}:
            raise TextGenerationProviderError("OpenAI service unavailable.") from exc
        name = type(exc).__name__.lower()
        if "timeout" in name:
            raise TextGenerationTimeoutError("OpenAI request timed out.") from exc
        if "ratelimit" in name:
            raise TextGenerationRateLimitError("OpenAI rate limit exceeded.") from exc
        if "connection" in name:
            raise TextGenerationConnectionError("OpenAI could not be reached.") from exc
        raise TextGenerationProviderError("OpenAI text generation failed.") from exc

    def generate_text_with_metadata(
        self, prompt: str
    ) -> tuple[str, GenerationMetadata]:
        start_time = time.perf_counter()
        try:
            response = self._client.responses.create(
                model=self._model,
                input=prompt,
            )
        except Exception as exc:
            self._handle_client_error(exc)

        latency_ms = int((time.perf_counter() - start_time) * 1000)

        if not response or not response.output_text:
            raise TextGenerationEmptyResponseError("OpenAI returned an empty response.")

        metadata = self._extract_metadata(response, latency_ms)
        return response.output_text, metadata

    def generate_text(self, prompt: str) -> str:
        text, _ = self.generate_text_with_metadata(prompt)
        return text

    def generate_json_with_metadata(
        self, prompt: str
    ) -> tuple[dict[str, object], GenerationMetadata]:
        start_time = time.perf_counter()
        # Use a permissive generic object schema for structured output
        schema = {
            "type": "object",
            "additionalProperties": True,
        }
        try:
            response = self._client.responses.create(
                model=self._model,
                input=prompt,
                text={
                    "format": {
                        "type": "json_schema",
                        "name": "response",
                        "schema": schema,
                        "strict": False,
                    }
                },
            )
        except Exception as exc:
            self._handle_client_error(exc)

        latency_ms = int((time.perf_counter() - start_time) * 1000)

        if not response or not response.output_text:
            raise TextGenerationEmptyResponseError("OpenAI returned an empty response.")

        result = _parse_json_object(response.output_text, "OpenAI")
        metadata = self._extract_metadata(response, latency_ms)
        return result, metadata

    def generate_json(self, prompt: str) -> dict[str, object]:
        result, _ = self.generate_json_with_metadata(prompt)
        return result


class ClaudeTextGenerationProvider:
    MODEL = DEFAULT_CLAUDE_MODEL
    PROVIDER_NAME = "claude"

    def __init__(
        self,
        api_key: str | None = None,
        timeout_seconds: int | None = None,
        model: str | None = None,
        client: object | None = None,
    ) -> None:
        key = api_key or settings.anthropic_api_key
        if not key and client is None:
            raise TextGenerationAuthError("ANTHROPIC_API_KEY is not configured.")

        self._model = model or self.MODEL
        self._timeout_seconds = (
            timeout_seconds
            if timeout_seconds is not None
            else settings.ai_generation_timeout_seconds
        )
        if client is not None:
            self._client = client
        else:
            try:
                from anthropic import Anthropic

                self._client = Anthropic(api_key=key, timeout=self._timeout_seconds)
            except (ImportError, ModuleNotFoundError, AttributeError):
                self._client = None

    def _extract_metadata(
        self, response: object, latency_ms: int
    ) -> GenerationMetadata:
        prompt_tokens = None
        completion_tokens = None
        total_tokens = None

        usage = getattr(response, "usage", None)
        if usage is not None:
            prompt_tokens = getattr(usage, "input_tokens", None)
            completion_tokens = getattr(usage, "output_tokens", None)
            total_tokens = (
                prompt_tokens + completion_tokens
                if prompt_tokens is not None and completion_tokens is not None
                else None
            )

        return GenerationMetadata(
            provider=self.PROVIDER_NAME,
            model=self._model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            latency_ms=latency_ms,
        )

    def _handle_client_error(self, exc: Exception) -> None:
        if isinstance(exc, TextGenerationError):
            raise exc
        try:
            from anthropic import (
                APITimeoutError,
                RateLimitError as AnthropicRateLimitError,
                APIStatusError,
                APIConnectionError,
            )

            if isinstance(exc, APITimeoutError):
                raise TextGenerationTimeoutError("Claude request timed out.") from exc
            if isinstance(exc, AnthropicRateLimitError):
                raise TextGenerationRateLimitError("Claude rate limit exceeded.") from exc
            if isinstance(exc, APIStatusError):
                if exc.status_code in {401, 403}:
                    raise TextGenerationAuthError("Claude authentication failed.") from exc
                if exc.status_code in {429}:
                    raise TextGenerationRateLimitError(
                        "Claude rate limit exceeded."
                    ) from exc
                if exc.status_code in {500, 502, 503, 504}:
                    raise TextGenerationProviderError(
                        "Claude service unavailable."
                    ) from exc
                raise TextGenerationProviderError("Claude text generation failed.") from exc
            if isinstance(exc, APIConnectionError):
                raise TextGenerationConnectionError("Claude could not be reached.") from exc
        except (ImportError, ModuleNotFoundError, AttributeError):
            pass

        status_code = getattr(exc, "status_code", getattr(exc, "code", None))
        if status_code in {401, 403}:
            raise TextGenerationAuthError("Claude authentication failed.") from exc
        if status_code == 429:
            raise TextGenerationRateLimitError("Claude rate limit exceeded.") from exc
        if status_code in {500, 502, 503, 504}:
            raise TextGenerationProviderError("Claude service unavailable.") from exc
        name = type(exc).__name__.lower()
        if "timeout" in name:
            raise TextGenerationTimeoutError("Claude request timed out.") from exc
        if "ratelimit" in name:
            raise TextGenerationRateLimitError("Claude rate limit exceeded.") from exc
        if "connection" in name:
            raise TextGenerationConnectionError("Claude could not be reached.") from exc
        raise TextGenerationProviderError("Claude text generation failed.") from exc

    def generate_text_with_metadata(
        self, prompt: str
    ) -> tuple[str, GenerationMetadata]:
        start_time = time.perf_counter()
        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=4096,
                messages=[{"role": "user", "content": prompt}],
            )
        except Exception as exc:
            self._handle_client_error(exc)

        latency_ms = int((time.perf_counter() - start_time) * 1000)

        if not response or not response.content:
            raise TextGenerationEmptyResponseError("Claude returned an empty response.")

        # Extract text from content blocks
        text_parts = [block.text for block in response.content if block.type == "text"]
        if not text_parts:
            raise TextGenerationEmptyResponseError("Claude returned an empty response.")
        text = "\n".join(text_parts)

        metadata = self._extract_metadata(response, latency_ms)
        return text, metadata

    def generate_text(self, prompt: str) -> str:
        text, _ = self.generate_text_with_metadata(prompt)
        return text

    def generate_json_with_metadata(
        self, prompt: str
    ) -> tuple[dict[str, object], GenerationMetadata]:
        start_time = time.perf_counter()
        # Use a permissive generic object schema for structured output
        schema = {
            "type": "object",
            "additionalProperties": True,
        }
        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=4096,
                messages=[{"role": "user", "content": prompt}],
                output_config={
                    "format": {
                        "type": "json_schema",
                        "schema": schema,
                    }
                },
            )
        except Exception as exc:
            self._handle_client_error(exc)

        latency_ms = int((time.perf_counter() - start_time) * 1000)

        if not response or not response.content:
            raise TextGenerationEmptyResponseError("Claude returned an empty response.")

        text_parts = [block.text for block in response.content if block.type == "text"]
        if not text_parts:
            raise TextGenerationEmptyResponseError("Claude returned an empty response.")
        text = "\n".join(text_parts)

        result = _parse_json_object(text, "Claude")
        metadata = self._extract_metadata(response, latency_ms)
        return result, metadata

    def generate_json(self, prompt: str) -> dict[str, object]:
        result, _ = self.generate_json_with_metadata(prompt)
        return result


# Register all providers
ProviderRegistry.register(
    AI_PROVIDER_GEMINI,
    constructor=GeminiTextGenerationProvider,
    default_model=DEFAULT_GEMINI_MODEL,
    requires_key=True,
    vendor="Google",
    description="Google Gemini · Fast, high-context instruction & JSON generation",
    is_local=False,
)
ProviderRegistry.register(
    AI_PROVIDER_OLLAMA,
    constructor=OllamaTextGenerationProvider,
    default_model=settings.ollama_model,
    requires_key=False,
    vendor="Ollama",
    description="Self-hosted local model via Ollama · Private execution",
    is_local=True,
)
ProviderRegistry.register(
    AI_PROVIDER_OPENAI,
    constructor=OpenAITextGenerationProvider,
    default_model=DEFAULT_OPENAI_MODEL,
    requires_key=True,
    vendor="OpenAI",
    description="OpenAI · High-quality instruction following & structured output",
    is_local=False,
)
ProviderRegistry.register(
    AI_PROVIDER_CLAUDE,
    constructor=ClaudeTextGenerationProvider,
    default_model=DEFAULT_CLAUDE_MODEL,
    requires_key=True,
    vendor="Anthropic",
    description="Anthropic Claude · Strong reasoning & reliable JSON generation",
    is_local=False,
)


def configured_provider_identity() -> tuple[str, str]:
    """Report the provider name and model the application is configured to use."""
    provider_name = settings.ai_provider
    default_model = ProviderRegistry.get_default_model(provider_name)
    if default_model:
        return provider_name, default_model

    # Fallback to old logic for backward compatibility
    if provider_name == AI_PROVIDER_OLLAMA:
        return AI_PROVIDER_OLLAMA, settings.ollama_model
    if provider_name == AI_PROVIDER_GEMINI:
        return AI_PROVIDER_GEMINI, GeminiTextGenerationProvider.MODEL
    if provider_name == AI_PROVIDER_OPENAI:
        return AI_PROVIDER_OPENAI, DEFAULT_OPENAI_MODEL
    if provider_name == AI_PROVIDER_CLAUDE:
        return AI_PROVIDER_CLAUDE, DEFAULT_CLAUDE_MODEL

    return provider_name, "unknown"


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
        overall_timeout_seconds: int | None = None,
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
        self.overall_timeout_seconds = (
            overall_timeout_seconds
            if overall_timeout_seconds is not None
            else settings.ai_generation_overall_timeout_seconds
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

        overall_deadline = time.monotonic() + self.overall_timeout_seconds
        try:
            last_exception: Exception | None = None
            attempt_count = 0
            provider_attempts = 0

            for provider_idx, provider in enumerate(self.providers):
                provider_name = getattr(
                    provider, "PROVIDER_NAME", type(provider).__name__
                )
                provider_attempts = 0

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
                        # Check overall deadline before each attempt
                        if time.monotonic() >= overall_deadline:
                            raise TextGenerationTimeoutError(
                                "Overall generation deadline exceeded."
                            )

                        with attempt:
                            if hasattr(provider, method_name):
                                result = getattr(provider, method_name)(prompt)
                                # Log attempt metrics (structured, no raw exception)
                                logger.info(
                                    "Provider attempt completed",
                                    extra={
                                        "event": "provider_attempt",
                                        "provider": provider_name,
                                        "attempt_number": provider_attempts + 1,
                                        "total_attempts": attempt_count
                                        + provider_attempts
                                        + 1,
                                        "fallback_index": provider_idx,
                                    },
                                )
                                return result
                            if method_name == "generate_text_with_metadata":
                                text = provider.generate_text(prompt)
                                meta = GenerationMetadata(
                                    provider=provider_name,
                                    model=getattr(provider, "MODEL", "unknown"),
                                )
                                return text, meta
                            if method_name == "generate_json_with_metadata":
                                data = provider.generate_json(prompt)
                                meta = GenerationMetadata(
                                    provider=provider_name,
                                    model=getattr(provider, "MODEL", "unknown"),
                                )
                                return data, meta
                            return getattr(provider, method_name)(prompt)
                        provider_attempts += 1
                        attempt_count += 1
                except RetryError as exc:
                    last_exception = exc.last_attempt.exception() or exc
                    logger.warning(
                        "Provider exhausted attempts",
                        extra={
                            "event": "provider_exhausted",
                            "provider": provider_name,
                            "attempts": provider_attempts,
                            "exception_type": type(last_exception).__name__,
                            "error_category": getattr(
                                last_exception, "error_category", "unknown"
                            ),
                        },
                    )
                except Exception as exc:
                    last_exception = exc
                    logger.warning(
                        "Provider failed",
                        extra={
                            "event": "provider_failed",
                            "provider": provider_name,
                            "attempts": provider_attempts,
                            "exception_type": type(exc).__name__,
                            "error_category": getattr(exc, "error_category", "unknown"),
                        },
                    )

                is_auth_error = isinstance(last_exception, TextGenerationAuthError)
                if not is_auth_error and last_exception is not None:
                    status_code = getattr(
                        last_exception, "status_code", getattr(last_exception, "code", None)
                    )
                    if status_code in {401, 403}:
                        is_auth_error = True

                if getattr(provider, "is_personal_key", False) and is_auth_error:
                    vendor = _vendor_display_name(provider_name)
                    raise PersonalKeyAuthError(
                        f"Your personal {vendor} API key is invalid or expired.",
                        provider=provider_name,
                    ) from last_exception

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


def get_available_models(user: object | None = None) -> list[dict[str, object]]:
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

    if user is not None:
        if getattr(user, "encrypted_gemini_api_key", None) or (
            isinstance(user, dict) and user.get("encrypted_gemini_api_key")
        ):
            if AI_PROVIDER_GEMINI not in provider_names:
                provider_names.append(AI_PROVIDER_GEMINI)
        if getattr(user, "encrypted_openai_api_key", None) or (
            isinstance(user, dict) and user.get("encrypted_openai_api_key")
        ):
            if AI_PROVIDER_OPENAI not in provider_names:
                provider_names.append(AI_PROVIDER_OPENAI)
        if getattr(user, "encrypted_anthropic_api_key", None) or (
            isinstance(user, dict) and user.get("encrypted_anthropic_api_key")
        ):
            if AI_PROVIDER_CLAUDE not in provider_names:
                provider_names.append(AI_PROVIDER_CLAUDE)

    standard_capabilities = [
        "study_guide",
        "quiz",
        "flashcard",
        "ai_tutor",
        "course_qa",
        "prompt_generator",
    ]

    models: list[dict[str, object]] = []

    catalog_dict = getattr(settings, "ai_model_catalog", None) or {}
    for provider in provider_names:
        provider_models = catalog_dict.get(provider, [])
        vendor = ProviderRegistry.get_vendor(provider) or provider.title()
        description_template = ProviderRegistry.get_description(provider)
        is_local = ProviderRegistry.is_local(provider)

        for index, entry in enumerate(provider_models):
            model_name = str(entry["model"])
            is_json = bool(entry.get("json_mode", True))
            context_win = int(entry.get("context_window", 8192))
            has_vision = bool(entry.get("vision", False))

            cost_hint = (
                "Local execution · Unmetered" if is_local else "Metered (1-2 credits)"
            )
            description = (
                description_template.format(model_name=model_name)
                if description_template and "{" in description_template
                else (description_template or f"{vendor} ({model_name})")
            )

            models.append(
                {
                    "id": f"{provider}:{model_name}",
                    "provider": provider,
                    "model": model_name,
                    "display_name": f"{vendor} ({model_name})",
                    "is_default": provider == primary_name and index == 0,
                    "cost_hint": cost_hint,
                    "capabilities": list(standard_capabilities),
                    "description": description,
                    "is_local": is_local,
                    "supports_json": is_json,
                    "json_mode": is_json,
                    "context_window": context_win,
                    "vision": has_vision,
                }
            )

    return models


def resolve_effective_model(
    request_model: str | None = None,
    user_preferred_model: str | None = None,
    required_capability: str | None = None,
    user: object | None = None,
) -> str:
    """Resolve the effective model using precedence rule:
    1. Explicit request override
    2. User preferred model
    3. Deployment default

    Optionally validates that the resolved model supports ``required_capability``.
    """
    catalog = get_available_models(user=user)
    catalog_by_id = {m["id"]: m for m in catalog}
    valid_ids = set(catalog_by_id.keys())

    if request_model:
        if request_model not in valid_ids:
            full_entry = _model_catalog_entry(request_model, user=user)
            if full_entry is None:
                raise UnavailableModelError("Requested AI model is not available.")
            catalog_by_id[request_model] = full_entry
        if required_capability:
            caps = catalog_by_id[request_model].get("capabilities") or []
            if required_capability not in caps:
                raise BadRequestException(
                    f"Model '{request_model}' does not support '{required_capability}' task."
                )
        return request_model

    resolved_id: str | None = None

    if user_preferred_model:
        if user_preferred_model in catalog_by_id:
            if not required_capability or required_capability in (
                catalog_by_id[user_preferred_model].get("capabilities") or []
            ):
                resolved_id = user_preferred_model
        else:
            full_entry = _model_catalog_entry(user_preferred_model, user=user)
            if full_entry is not None:
                if not required_capability or required_capability in (
                    full_entry.get("capabilities") or []
                ):
                    resolved_id = user_preferred_model

    if not resolved_id:
        for m in catalog:
            if m.get("is_default"):
                if not required_capability or required_capability in (
                    m.get("capabilities") or []
                ):
                    resolved_id = str(m["id"])
                    break

    if not resolved_id and catalog:
        for m in catalog:
            if not required_capability or required_capability in (
                m.get("capabilities") or []
            ):
                resolved_id = str(m["id"])
                break

    if not resolved_id:
        if required_capability and catalog:
            raise BadRequestException(
                f"No available model supports '{required_capability}' task."
            )
        prov, model = configured_provider_identity()
        resolved_id = f"{prov}:{model}"

    return resolved_id


def resolve_user_api_key(
    provider_name: str,
    user: object | None = None,
    user_api_keys: dict[str, str | None] | None = None,
    explicit_key: str | None = None,
) -> str | None:
    """Resolve the API key for a provider, prioritizing explicit/user BYOK key before fallback."""
    if explicit_key:
        return explicit_key

    clean_name = provider_name.strip().lower()

    if user_api_keys:
        if clean_name in user_api_keys and user_api_keys[clean_name]:
            return user_api_keys[clean_name]
        if clean_name == "claude" and user_api_keys.get("anthropic"):
            return user_api_keys["anthropic"]
        if clean_name == "anthropic" and user_api_keys.get("claude"):
            return user_api_keys["claude"]

    if user is not None:
        try:
            from utils.crypto import decrypt_value

            if clean_name == "gemini":
                enc = getattr(user, "encrypted_gemini_api_key", None)
                if enc:
                    decrypted = decrypt_value(enc)
                    if decrypted:
                        return decrypted
            elif clean_name == "openai":
                enc = getattr(user, "encrypted_openai_api_key", None)
                if enc:
                    decrypted = decrypt_value(enc)
                    if decrypted:
                        return decrypted
            elif clean_name in {"claude", "anthropic"}:
                enc = getattr(user, "encrypted_anthropic_api_key", None)
                if enc:
                    decrypted = decrypt_value(enc)
                    if decrypted:
                        return decrypted
        except Exception as exc:
            logger.warning(
                "Failed to decrypt BYOK API key for provider '%s': %s. Falling back to default.",
                clean_name,
                exc,
            )

    return None


def _instantiate_provider(
    provider_name: str,
    model_name: str | None = None,
    api_key: str | None = None,
) -> TextGenerationProvider:
    clean_name = provider_name.strip().lower()
    constructor = ProviderRegistry.get_constructor(clean_name)
    if constructor is None:
        raise TextGenerationError(
            f"Text generation provider '{clean_name}' is not implemented.",
            error_category=ErrorCategory.PROVIDER_ERROR,
        )

    default_model = ProviderRegistry.get_default_model(clean_name)
    model = model_name or default_model
    if api_key and ProviderRegistry.requires_key(clean_name):
        return constructor(model=model, api_key=api_key)
    return constructor(model=model)


def get_text_generation_provider(
    effective_model: str | None = None,
    *,
    user: object | None = None,
    user_api_keys: dict[str, str | None] | None = None,
    api_key: str | None = None,
    require_json_mode: bool = False,
    overall_timeout_seconds: int | None = None,
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

    # Validate effective model's JSON mode requirement
    if effective_model and require_json_mode:
        model_entry = _model_catalog_entry(effective_model, user=user)

        if model_entry is None:
            raise UnavailableModelError("Requested AI model is not available.")

        if not model_entry["json_mode"]:
            raise IncompatibleModelError(
                "Requested AI model does not support JSON mode."
            )

    # Validate fallback models also support required capabilities
    if require_json_mode:
        catalog = get_available_models(user=user)
        # Determine selected_provider early for validation
        selected_provider_for_validation: str | None = None
        if effective_model:
            selected_provider_for_validation, _ = effective_model.split(":", 1)
            selected_provider_for_validation = (
                selected_provider_for_validation.strip().lower()
            )

        for name in provider_names:
            # Check if this provider has at least one JSON-capable model in catalog
            provider_models = [m for m in catalog if m["provider"] == name]
            json_capable = any(m.get("json_mode", True) for m in provider_models)
            if not json_capable and name != selected_provider_for_validation:
                raise IncompatibleModelError(
                    f"Fallback provider '{name}' has no JSON-capable models in catalog."
                )

    selected_provider: str | None = None
    selected_model: str | None = None

    if effective_model:
        selected_provider, selected_model = effective_model.split(":", 1)
        selected_provider = selected_provider.strip().lower()

        if selected_provider in provider_names:
            provider_names.remove(selected_provider)
        provider_names.insert(0, selected_provider)

    providers: list[TextGenerationProvider] = []

    for name in provider_names:
        model_override = selected_model if name == selected_provider else None
        resolved_key = resolve_user_api_key(
            name,
            user=user,
            user_api_keys=user_api_keys,
            explicit_key=api_key if name == selected_provider else None,
        )
        is_personal = bool(resolved_key)
        try:
            provider_inst = _instantiate_provider(
                name, model_override, api_key=resolved_key
            )
        except TypeError:
            provider_inst = _instantiate_provider(name, model_override)
        except TextGenerationAuthError as exc:
            if is_personal:
                vendor = _vendor_display_name(name)
                raise PersonalKeyAuthError(
                    f"Your personal {vendor} API key is invalid or expired.",
                    provider=name,
                ) from exc
            raise
        setattr(provider_inst, "is_personal_key", is_personal)
        setattr(provider_inst, "provider_name", name)
        providers.append(provider_inst)

    return ReliableTextGenerationProvider(
        providers,
        semaphore=get_shared_generation_semaphore(),
        max_concurrency=settings.ai_generation_max_concurrency,
        overall_timeout_seconds=overall_timeout_seconds,
    )

