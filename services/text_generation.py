import json
from typing import Protocol

from google import genai
from google.genai import types

from backend.app.config import settings


class TextGenerationProvider(Protocol):
    def generate_json(self, prompt: str) -> dict[str, object]: ...


class TextGenerationError(RuntimeError):
    """The configured text generation provider failed."""


class GeminiTextGenerationProvider:
    MODEL = "gemini-2.5-flash"

    def __init__(self) -> None:
        if not settings.gemini_api_key:
            raise TextGenerationError("GEMINI_API_KEY is not configured.")

        self._client = genai.Client(api_key=settings.gemini_api_key)

    def generate_json(self, prompt: str) -> dict[str, object]:
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

        if not response.text:
            raise TextGenerationError("Gemini returned an empty response.")

        try:
            result = json.loads(response.text)
        except json.JSONDecodeError as exc:
            raise TextGenerationError("Gemini returned invalid JSON.") from exc

        if not isinstance(result, dict):
            raise TextGenerationError("Gemini response must be a JSON object.")

        return result


def get_text_generation_provider() -> TextGenerationProvider:
    if settings.ai_provider == "gemini":
        return GeminiTextGenerationProvider()

    raise TextGenerationError(
        f"Text generation provider '{settings.ai_provider}' is not implemented."
    )
