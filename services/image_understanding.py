# services/image_understanding.py
"""Visual understanding providers and their configuration-driven factory.

Image understanding extracts semantic descriptions from detected PDF visual regions
(such as diagrams, tables, charts, and figures) so that their content can be indexed
and searched downstream alongside extracted text.
"""

import base64
import logging
from collections.abc import Callable
from dataclasses import dataclass
from time import perf_counter

import httpx
from google import genai
from google.genai import errors as genai_errors, types

from backend.app.config import (
    AI_PROVIDER_GEMINI,
    AI_PROVIDER_OLLAMA,
    IMAGE_PROVIDER_NONE,
    settings,
)
from schemas.ai_usage import ErrorCategory, GenerationType
from schemas.prompt_context import PromptContext
from schemas.prompt_template import PromptTemplateError
from utils.ai_diagnostics import ai_failure_fields
from services.ollama import resolve_ollama_base_url
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
_SHOW_PATH = "/api/show"
_VISION_CAPABILITY: dict[tuple[str, str], bool] = {}
_CAPABILITY_TIMEOUT_SECONDS = 10.0
_shared_http_client: httpx.Client | None = None


@dataclass(frozen=True, slots=True)
class ImageUnderstandingUsage:
    provider: str
    model: str
    success: bool
    error_category: ErrorCategory | None
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None
    latency_ms: int


ImageUsageCallback = Callable[[ImageUnderstandingUsage], None]


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


def _token_count(value: object) -> int | None:
    return value if isinstance(value, int) and value >= 0 else None


def _latency_ms(started_at: float) -> int:
    return max(0, round((perf_counter() - started_at) * 1000))


def _error_category(exc: Exception) -> ErrorCategory:
    if isinstance(exc, (TimeoutError, httpx.TimeoutException)):
        return ErrorCategory.TIMEOUT
    if isinstance(exc, genai_errors.APIError):
        code = getattr(exc, "code", None)
        if code == 429:
            return ErrorCategory.RATE_LIMIT
        if code in {401, 403}:
            return ErrorCategory.AUTHENTICATION_ERROR
    return ErrorCategory.PROVIDER_ERROR


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
    MODEL = "gemini-2.5-flash"
    enabled = True

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        timeout_seconds: int | None = None,
        max_bytes: int | None = None,
        client: object | None = None,
        prompt_context: PromptContext | None = None,
        usage_callback: ImageUsageCallback | None = None,
    ) -> None:
        key = api_key or settings.gemini_api_key
        if client is None and not key:
            raise VisualAnalysisError(
                "GEMINI_API_KEY is not configured for visual understanding."
            )
        self._model = model or self.MODEL
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
        self._usage_callback = usage_callback
        if client is not None:
            self._client = client
        else:
            http_opts = types.HttpOptions(timeout=int(self._timeout_seconds * 1000))
            self._client = genai.Client(api_key=key, http_options=http_opts)

    def _report_usage(
        self,
        *,
        started_at: float,
        success: bool,
        response: object | None = None,
        error_category: ErrorCategory | None = None,
    ) -> None:
        if self._usage_callback is None:
            return
        usage = getattr(response, "usage_metadata", None)
        prompt_tokens = _token_count(getattr(usage, "prompt_token_count", None))
        completion_tokens = _token_count(getattr(usage, "candidates_token_count", None))
        total_tokens = _token_count(getattr(usage, "total_token_count", None))
        try:
            self._usage_callback(
                ImageUnderstandingUsage(
                    provider=self.PROVIDER_NAME,
                    model=self._model,
                    success=success,
                    error_category=error_category,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=total_tokens,
                    latency_ms=_latency_ms(started_at),
                )
            )
        except Exception:
            logger.warning(
                "Failed to report image understanding usage",
                extra={"event": "image_understanding_usage_report_failed"},
            )

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
        started_at = perf_counter()
        try:
            part = types.Part.from_bytes(data=visual_png, mime_type="image/png")
            response = self._client.models.generate_content(
                model=self._model,
                contents=[prompt, part],
            )
        except Exception as exc:
            self._report_usage(
                started_at=started_at,
                success=False,
                error_category=_error_category(exc),
            )
            self._handle_client_error(exc)
            return None

        self._report_usage(started_at=started_at, success=True, response=response)

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
    MODEL = "llama3.2-vision"
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
        usage_callback: ImageUsageCallback | None = None,
    ) -> None:
        self._base_url = resolve_ollama_base_url(base_url or settings.ollama_base_url)
        self._model = model or self.MODEL
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
        self._usage_callback = usage_callback
        self._options = {
            "temperature": settings.ollama_temperature,
            "top_p": settings.ollama_top_p,
            "num_ctx": settings.ollama_num_ctx,
            "num_predict": settings.ollama_num_predict,
            "repeat_penalty": settings.ollama_repeat_penalty,
        }

    def _report_usage(
        self,
        *,
        started_at: float,
        success: bool,
        envelope: dict | None = None,
        error_category: ErrorCategory | None = None,
    ) -> None:
        if self._usage_callback is None:
            return
        prompt_tokens = _token_count(
            envelope.get("prompt_eval_count") if envelope else None
        )
        completion_tokens = _token_count(
            envelope.get("eval_count") if envelope else None
        )
        total_tokens = (
            prompt_tokens + completion_tokens
            if prompt_tokens is not None and completion_tokens is not None
            else None
        )
        try:
            self._usage_callback(
                ImageUnderstandingUsage(
                    provider=self.PROVIDER_NAME,
                    model=self._model,
                    success=success,
                    error_category=error_category,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=total_tokens,
                    latency_ms=_latency_ms(started_at),
                )
            )
        except Exception:
            logger.warning(
                "Failed to report image understanding usage",
                extra={"event": "image_understanding_usage_report_failed"},
            )

    def _log_unusable_response(self, raw_text: str, exc: BaseException | None) -> None:
        logger.warning(
            "AI generation failed",
            extra=ai_failure_fields(
                generation_type=GenerationType.IMAGE_UNDERSTANDING,
                provider=self.PROVIDER_NAME,
                model=self._model,
                error_category=ErrorCategory.INVALID_STRUCTURE,
                raw_text=raw_text,
                exc=exc,
            ),
        )

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
            "options": dict(self._options),
        }

        started_at = perf_counter()
        try:
            response = self._client.post(
                f"{self._base_url}{self.GENERATE_PATH}",
                json=payload,
                timeout=self._timeout_seconds,
            )
        except httpx.TimeoutException as exc:
            self._report_usage(
                started_at=started_at,
                success=False,
                error_category=ErrorCategory.TIMEOUT,
            )
            raise TemporaryVisualServiceError(
                "Ollama image understanding timed out."
            ) from exc
        except (httpx.TransportError, httpx.NetworkError, httpx.ConnectError) as exc:
            self._report_usage(
                started_at=started_at,
                success=False,
                error_category=ErrorCategory.PROVIDER_ERROR,
            )
            raise TemporaryVisualServiceError(
                "Ollama visual service could not be reached."
            ) from exc

        if response.status_code == 429:
            self._report_usage(
                started_at=started_at,
                success=False,
                error_category=ErrorCategory.RATE_LIMIT,
            )
            raise TemporaryVisualServiceError("Ollama rate limit exceeded.")
        if response.status_code in {500, 502, 503, 504}:
            self._report_usage(
                started_at=started_at,
                success=False,
                error_category=ErrorCategory.PROVIDER_ERROR,
            )
            raise TemporaryVisualServiceError(
                f"Ollama visual service returned HTTP {response.status_code}."
            )
        if not response.is_success:
            self._report_usage(
                started_at=started_at,
                success=False,
                error_category=(
                    ErrorCategory.AUTHENTICATION_ERROR
                    if response.status_code in {401, 403}
                    else ErrorCategory.PROVIDER_ERROR
                ),
            )
            raise VisualAnalysisError(f"Ollama returned HTTP {response.status_code}.")

        try:
            envelope = response.json()
        except ValueError as exc:
            self._report_usage(
                started_at=started_at,
                success=False,
                error_category=ErrorCategory.INVALID_STRUCTURE,
            )
            self._log_unusable_response(response.text, exc)
            raise VisualAnalysisError(
                "Ollama returned an invalid JSON response."
            ) from exc

        if not isinstance(envelope, dict):
            self._report_usage(
                started_at=started_at,
                success=False,
                error_category=ErrorCategory.INVALID_STRUCTURE,
            )
            self._log_unusable_response(response.text, None)
            raise VisualAnalysisError(
                "Ollama returned an unexpected response structure."
            )

        self._report_usage(started_at=started_at, success=True, envelope=envelope)
        raw_response = envelope.get("response")
        description = _clean_description_text(raw_response)
        if not description:
            return None

        return VisualDescription(
            visual_type=suggested_type,
            description=description,
        )


def _ollama_model_supports_vision(base_url: str, model: str) -> bool | None:
    """Ask Ollama what the configured model can do. None means it would not say."""
    cached = _VISION_CAPABILITY.get((base_url, model))
    if cached is not None:
        return cached
    try:
        response = _get_shared_http_client().post(
            f"{base_url}{_SHOW_PATH}",
            json={"model": model},
            timeout=_CAPABILITY_TIMEOUT_SECONDS,
        )
        if not response.is_success:
            return None
        payload = response.json()
    except (httpx.HTTPError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    capabilities = payload.get("capabilities")
    if not isinstance(capabilities, list):
        return None
    supports = "vision" in capabilities
    _VISION_CAPABILITY[(base_url, model)] = supports
    if not supports:
        logger.warning(
            "The configured model does not support images, so visual analysis "
            "is disabled",
            extra={
                "event": "image_understanding_disabled",
                "provider": AI_PROVIDER_OLLAMA,
                "model": model,
            },
        )
    return supports


def _ollama_vision_is_available(model: str) -> bool:
    base_url = resolve_ollama_base_url(settings.ollama_base_url)
    return _ollama_model_supports_vision(base_url, model) is not False


def configured_image_understanding_identity() -> tuple[str, str | None]:
    """Report the provider and model attributed to visual descriptions."""
    if not settings.ai_vision_model:
        return IMAGE_PROVIDER_NONE, None
    provider_name, model_name = settings.ai_vision_model.split(":", 1)
    if provider_name == AI_PROVIDER_OLLAMA and not _ollama_vision_is_available(
        model_name
    ):
        return IMAGE_PROVIDER_NONE, None
    return provider_name, model_name


def get_image_understanding_provider(
    *,
    prompt_context: PromptContext | None = None,
    usage_callback: ImageUsageCallback | None = None,
) -> ImageUnderstandingProvider:
    """Construct the configured ImageUnderstandingProvider instance."""
    if not settings.ai_vision_model:
        return DisabledImageUnderstandingProvider()
    provider_name, model_name = settings.ai_vision_model.split(":", 1)
    if provider_name == AI_PROVIDER_GEMINI:
        return GeminiImageUnderstandingProvider(
            model=model_name,
            prompt_context=prompt_context,
            usage_callback=usage_callback,
        )
    if provider_name == AI_PROVIDER_OLLAMA:
        if not _ollama_vision_is_available(model_name):
            return DisabledImageUnderstandingProvider()
        return OllamaImageUnderstandingProvider(
            model=model_name,
            prompt_context=prompt_context,
            usage_callback=usage_callback,
        )
    raise ValueError(
        f"Image understanding provider '{provider_name}' is not implemented."
    )
