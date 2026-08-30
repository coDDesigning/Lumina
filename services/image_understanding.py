# services/image_understanding.py
"""Visual understanding providers and their configuration-driven factory.

Image understanding extracts semantic descriptions from detected PDF visual regions
(such as diagrams, tables, charts, and figures) so that their content can be indexed
and searched downstream alongside extracted text.
"""

import base64
import logging

import httpx
from google import genai
from google.genai import errors as genai_errors, types

from backend.app.config import (
    AI_PROVIDER_GEMINI,
    AI_PROVIDER_OLLAMA,
    IMAGE_PROVIDER_NONE,
    settings,
)
from schemas.prompt_context import PromptContext
from schemas.prompt_template import PromptTemplateError
from services.document_pipeline import (
    DisabledImageUnderstandingProvider,
    ImageUnderstandingProvider,
    TemporaryVisualServiceError,
    VisualAnalysisError,
    VisualDescription,
    VisualType,
    _MAX_VISUAL_DESCRIPTION_CHARACTERS,
)

from services.prompt_loader import PromptLoader

logger = logging.getLogger(__name__)

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_shared_http_client: httpx.Client | None = None


def _get_shared_http_client() -> httpx.Client:
    global _shared_http_client
    if _shared_http_client is None:
        _shared_http_client = httpx.Client()
    return _shared_http_client


def _validate_image_bytes(visual_png: bytes, max_bytes: int) -> None:
    """Ensure visual input is bounded and represents valid PNG image data."""
    if not isinstance(visual_png, bytes) or not visual_png:
        raise VisualAnalysisError("Visual content is empty or invalid bytes.")
    if len(visual_png) > max_bytes:
        raise VisualAnalysisError(
            f"Visual content exceeds maximum allowed size of {max_bytes} bytes."
        )
    if not visual_png.startswith(_PNG_SIGNATURE):
        raise VisualAnalysisError("Visual content does not have a valid PNG signature.")


def _clean_description_text(raw_text: str | None) -> str | None:
    if not isinstance(raw_text, str):
        return None
    cleaned = raw_text.replace("\x00", "").strip()
    if not cleaned:
        return None
    if len(cleaned) > _MAX_VISUAL_DESCRIPTION_CHARACTERS:
        cleaned = cleaned[:_MAX_VISUAL_DESCRIPTION_CHARACTERS].rstrip()
    return cleaned or None


_IMAGE_DESCRIPTION_TEMPLATE = "image_description"


def _render_image_description_prompt(
    prompt_context: PromptContext, suggested_type: VisualType
) -> str:
    try:
        return PromptLoader.render(
            _IMAGE_DESCRIPTION_TEMPLATE,
            {
                **prompt_context.as_variables(),
                "SUGGESTED_TYPE": suggested_type.value,
            },
        )
    except PromptTemplateError as exc:
        raise VisualAnalysisError(
            "The image description prompt template could not be rendered."
        ) from exc


class GeminiImageUnderstandingProvider:
    """Cloud-hosted visual analysis using Google Gemini multimodal models."""

    PROVIDER_NAME = AI_PROVIDER_GEMINI
    enabled = True

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        timeout_seconds: int | None = None,
        max_bytes: int | None = None,
        client: object | None = None,
        prompt_context: PromptContext | None = None,
    ) -> None:
        key = api_key or settings.gemini_api_key
        if client is None and not key:
            raise VisualAnalysisError(
                "GEMINI_API_KEY is not configured for visual understanding."
            )
        self._model = model or settings.gemini_image_model
        self._timeout_seconds = (
            timeout_seconds
            if timeout_seconds is not None
            else settings.image_understanding_timeout_seconds
        )
        self._max_bytes = (
            max_bytes
            if max_bytes is not None
            else settings.image_understanding_max_bytes
        )
        self._prompt_context = prompt_context or PromptContext()
        if client is not None:
            self._client = client
        else:
            http_opts = types.HttpOptions(timeout=int(self._timeout_seconds * 1000))
            self._client = genai.Client(api_key=key, http_options=http_opts)

    def _handle_client_error(self, exc: Exception) -> None:
        if isinstance(exc, (TemporaryVisualServiceError, VisualAnalysisError)):
            raise exc
        if isinstance(exc, (TimeoutError, httpx.TimeoutException)):
            raise TemporaryVisualServiceError(
                "Gemini visual understanding request timed out."
            ) from exc
        if isinstance(exc, genai_errors.APIError):
            code = getattr(exc, "code", None)
            if code == 429:
                raise TemporaryVisualServiceError(
                    "Gemini visual rate limit exceeded."
                ) from exc
            if code in {500, 502, 503, 504}:
                raise TemporaryVisualServiceError(
                    "Gemini visual service temporarily unavailable."
                ) from exc
            if code in {400, 401, 403, 404}:
                raise VisualAnalysisError(
                    f"Gemini visual analysis failed: {getattr(exc, 'message', str(exc))}"
                ) from exc
            raise TemporaryVisualServiceError(
                "Gemini visual understanding request failed."
            ) from exc
        if isinstance(exc, genai_errors.ServerError):
            raise TemporaryVisualServiceError("Gemini visual server error.") from exc
        if isinstance(exc, (httpx.NetworkError, httpx.ConnectError)):
            raise TemporaryVisualServiceError(
                "Gemini visual connection error."
            ) from exc
        raise VisualAnalysisError(f"Gemini visual understanding error: {exc}") from exc

    def describe_visual(
        self,
        visual_png: bytes,
        *,
        page_number: int,
        visual_index: int,
        suggested_type: VisualType,
    ) -> VisualDescription | None:
        _validate_image_bytes(visual_png, self._max_bytes)
        prompt = _render_image_description_prompt(self._prompt_context, suggested_type)
        try:
            part = types.Part.from_bytes(data=visual_png, mime_type="image/png")
            response = self._client.models.generate_content(
                model=self._model,
                contents=[prompt, part],
            )
        except Exception as exc:
            self._handle_client_error(exc)
            return None

        if not response or not getattr(response, "text", None):
            return None

        description = _clean_description_text(response.text)
        if not description:
            return None

        return VisualDescription(
            visual_type=suggested_type,
            description=description,
        )


class OllamaImageUnderstandingProvider:
    """Self-hosted visual analysis using Ollama multimodal/vision models."""

    PROVIDER_NAME = AI_PROVIDER_OLLAMA
    enabled = True
    GENERATE_PATH = "/api/generate"

    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        timeout_seconds: int | None = None,
        max_bytes: int | None = None,
        client: httpx.Client | None = None,
        prompt_context: PromptContext | None = None,
    ) -> None:
        from services.embeddings import resolve_ollama_base_url

        self._base_url = resolve_ollama_base_url(base_url or settings.ollama_base_url)
        self._model = model or settings.ollama_image_model
        self._timeout_seconds = (
            timeout_seconds
            if timeout_seconds is not None
            else settings.image_understanding_timeout_seconds
        )
        self._max_bytes = (
            max_bytes
            if max_bytes is not None
            else settings.image_understanding_max_bytes
        )
        self._prompt_context = prompt_context or PromptContext()
        self._client = client or _get_shared_http_client()

    def describe_visual(
        self,
        visual_png: bytes,
        *,
        page_number: int,
        visual_index: int,
        suggested_type: VisualType,
    ) -> VisualDescription | None:
        _validate_image_bytes(visual_png, self._max_bytes)
        b64_image = base64.b64encode(visual_png).decode("utf-8")
        prompt = _render_image_description_prompt(self._prompt_context, suggested_type)
        payload = {
            "model": self._model,
            "prompt": prompt,
            "images": [b64_image],
            "stream": False,
        }

        try:
            response = self._client.post(
                f"{self._base_url}{self.GENERATE_PATH}",
                json=payload,
                timeout=self._timeout_seconds,
            )
        except httpx.TimeoutException as exc:
            raise TemporaryVisualServiceError(
                "Ollama image understanding timed out."
            ) from exc
        except (httpx.TransportError, httpx.NetworkError, httpx.ConnectError) as exc:
            raise TemporaryVisualServiceError(
                "Ollama visual service could not be reached."
            ) from exc

        if response.status_code == 429:
            raise TemporaryVisualServiceError("Ollama rate limit exceeded.")
        if response.status_code in {500, 502, 503, 504}:
            raise TemporaryVisualServiceError(
                f"Ollama visual service returned HTTP {response.status_code}."
            )
        if not response.is_success:
            raise VisualAnalysisError(f"Ollama returned HTTP {response.status_code}.")

        try:
            envelope = response.json()
        except ValueError as exc:
            raise VisualAnalysisError(
                "Ollama returned an invalid JSON response."
            ) from exc

        if not isinstance(envelope, dict):
            raise VisualAnalysisError(
                "Ollama returned an unexpected response structure."
            )

        raw_response = envelope.get("response")
        description = _clean_description_text(raw_response)
        if not description:
            return None

        return VisualDescription(
            visual_type=suggested_type,
            description=description,
        )


def configured_image_understanding_identity() -> tuple[str, str | None]:
    """Report the provider and model attributed to visual descriptions."""
    if settings.image_provider == AI_PROVIDER_GEMINI:
        return AI_PROVIDER_GEMINI, settings.gemini_image_model
    if settings.image_provider == AI_PROVIDER_OLLAMA:
        return AI_PROVIDER_OLLAMA, settings.ollama_image_model
    return IMAGE_PROVIDER_NONE, None


def get_image_understanding_provider(
    *, prompt_context: PromptContext | None = None
) -> ImageUnderstandingProvider:
    """Construct the configured ImageUnderstandingProvider instance."""
    provider_name = settings.image_provider
    if provider_name in (IMAGE_PROVIDER_NONE, "disabled", ""):
        return DisabledImageUnderstandingProvider()
    if provider_name == AI_PROVIDER_GEMINI:
        return GeminiImageUnderstandingProvider(prompt_context=prompt_context)
    if provider_name == AI_PROVIDER_OLLAMA:
        return OllamaImageUnderstandingProvider(prompt_context=prompt_context)
    raise ValueError(
        f"Image understanding provider '{provider_name}' is not implemented."
    )
