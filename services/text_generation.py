import json
import time
from dataclasses import dataclass
from typing import Protocol

import httpx
from google import genai
from google.genai import types

from backend.app.config import (
    AI_PROVIDER_GEMINI,
    AI_PROVIDER_OLLAMA,
    settings,
)


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


class TextGenerationConnectionError(TextGenerationError):
    """The configured text generation provider could not be reached."""


class TextGenerationTimeoutError(TextGenerationError):
    """The configured text generation provider did not answer in time."""


class TextGenerationResponseError(TextGenerationError):
    """The configured text generation provider returned an unusable response."""


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
        raise TextGenerationResponseError(
            f"{provider_label} returned invalid JSON."
        ) from exc

    if not isinstance(result, dict):
        raise TextGenerationResponseError(
            f"{provider_label} response must be a JSON object."
        )

    return result


class GeminiTextGenerationProvider:
    MODEL = "gemini-2.5-flash"
    PROVIDER_NAME = "gemini"

    def __init__(self) -> None:
        if not settings.gemini_api_key:
            raise TextGenerationError("GEMINI_API_KEY is not configured.")

        self._client = genai.Client(api_key=settings.gemini_api_key)

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
            raise TextGenerationError("Gemini text generation failed.") from exc

        latency_ms = int((time.perf_counter() - start_time) * 1000)

        if not response.text:
            raise TextGenerationError("Gemini returned an empty response.")

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
            raise TextGenerationError("Gemini text generation failed.") from exc

        latency_ms = int((time.perf_counter() - start_time) * 1000)

        if not response.text:
            raise TextGenerationError("Gemini returned an empty response.")

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

    def __init__(self, client: httpx.Client | None = None) -> None:
        self._base_url = settings.ollama_base_url
        self._model = settings.ollama_model
        self._timeout_seconds = settings.ollama_timeout_seconds
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

        if not response.is_success:
            raise TextGenerationResponseError(
                f"Ollama returned HTTP {response.status_code}."
            )

        try:
            envelope = response.json()
        except ValueError as exc:
            raise TextGenerationResponseError(
                "Ollama returned a response that is not valid JSON."
            ) from exc

        if not isinstance(envelope, dict):
            raise TextGenerationResponseError(
                "Ollama returned an unexpected response structure."
            )

        generated = envelope.get("response")
        if not isinstance(generated, str):
            raise TextGenerationResponseError(
                "Ollama returned an unexpected response structure."
            )

        if not generated.strip():
            raise TextGenerationResponseError("Ollama returned an empty response.")

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


def get_text_generation_provider() -> TextGenerationProvider:
    if settings.ai_provider == AI_PROVIDER_GEMINI:
        return GeminiTextGenerationProvider()

    if settings.ai_provider == AI_PROVIDER_OLLAMA:
        return OllamaTextGenerationProvider()

    raise TextGenerationError(
        f"Text generation provider '{settings.ai_provider}' is not implemented."
    )
