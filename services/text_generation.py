import json
import time
from dataclasses import dataclass
from typing import Protocol

from google import genai
from google.genai import types

from backend.app.config import settings


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

        try:
            result = json.loads(response.text)
        except json.JSONDecodeError as exc:
            raise TextGenerationError("Gemini returned invalid JSON.") from exc

        if not isinstance(result, dict):
            raise TextGenerationError("Gemini response must be a JSON object.")

        metadata = self._extract_metadata(response, latency_ms)
        return result, metadata

    def generate_json(self, prompt: str) -> dict[str, object]:
        result, _ = self.generate_json_with_metadata(prompt)
        return result


def get_text_generation_provider() -> TextGenerationProvider:
    if settings.ai_provider == "gemini":
        return GeminiTextGenerationProvider()

    raise TextGenerationError(
        f"Text generation provider '{settings.ai_provider}' is not implemented."
    )
