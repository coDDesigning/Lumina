import logging
import time
import urllib.parse

import httpx

from backend.app.config import AI_PROVIDER_OLLAMA, settings
from backend.app.models import User
from schemas.ai_model import ModelTestErrorCode, ModelTestResult
from services.image_understanding import ollama_model_supports_vision
from services.ollama import resolve_ollama_base_url_with_fallback
from services.text_generation import (
    TextGenerationAuthError,
    TextGenerationConnectionError,
    TextGenerationError,
    TextGenerationRateLimitError,
    TextGenerationTimeoutError,
    UnavailableModelError,
    _model_catalog_entry,
    get_single_text_generation_provider,
    resolve_effective_model,
)

logger = logging.getLogger(__name__)

MODEL_CHECK_TIMEOUT_SECONDS = 10.0
OLLAMA_TAGS_TIMEOUT_SECONDS = 5.0
_TAGS_PATH = "/api/tags"

_shared_http_client: httpx.Client | None = None


def _get_shared_http_client() -> httpx.Client:
    global _shared_http_client
    if _shared_http_client is None:
        _shared_http_client = httpx.Client()
    return _shared_http_client


_SUCCESS_MESSAGE = "The provider confirmed this model is available to your account."
_UNREACHABLE_MESSAGE = "The model provider could not be reached. Try again in a moment."
_TIMEOUT_MESSAGE = "The model provider did not respond in time. Try again."
_AUTH_MESSAGE = (
    "Authentication with the model provider failed. Check the configured API key."
)
_RATE_LIMITED_MESSAGE = (
    "The model provider is rate-limited right now. Try again shortly."
)
_BAD_RESPONSE_MESSAGE = "The model provider returned an unusable response."
_UNAVAILABLE_MESSAGE = "The requested model is not available."
_MODEL_NOT_FOUND_CLOUD_MESSAGE = (
    "This provider does not offer this model to this account."
)

_ERROR_MESSAGES: dict[str, str] = {
    "timeout": _TIMEOUT_MESSAGE,
    "auth": _AUTH_MESSAGE,
    "rate_limited": _RATE_LIMITED_MESSAGE,
    "bad_response": _BAD_RESPONSE_MESSAGE,
    "unavailable": _UNAVAILABLE_MESSAGE,
}


def _split_model_id(model_id: str) -> tuple[str, str]:
    if ":" in model_id:
        provider, name = model_id.split(":", 1)
        return provider.strip().lower(), name
    return model_id.strip().lower(), model_id


def _redact_userinfo(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    if not parsed.hostname:
        return url
    netloc = parsed.hostname
    if parsed.port:
        netloc = f"{netloc}:{parsed.port}"
    return urllib.parse.urlunsplit(
        (parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment)
    )


def _error_message(
    code: ModelTestErrorCode,
    *,
    is_admin: bool,
    provider_name: str,
    base_url: str | None,
    model_name: str,
) -> str:
    if code == "unreachable":
        if is_admin and provider_name == AI_PROVIDER_OLLAMA and base_url:
            return (
                f"Ollama isn't reachable at {base_url}. Check that it's running "
                "and that OLLAMA_BASE_URL points at it."
            )
        return _UNREACHABLE_MESSAGE
    if code == "model_not_found":
        if provider_name == AI_PROVIDER_OLLAMA:
            return f"Run `ollama pull {model_name}` on the Ollama machine."
        return _MODEL_NOT_FOUND_CLOUD_MESSAGE
    return _ERROR_MESSAGES[code]


def _check_ollama_tags(base_url: str, model: str) -> ModelTestErrorCode | None:
    try:
        response = _get_shared_http_client().get(
            f"{base_url}{_TAGS_PATH}", timeout=OLLAMA_TAGS_TIMEOUT_SECONDS
        )
    except httpx.TimeoutException:
        return "timeout"
    except httpx.TransportError:
        return "unreachable"

    if response.status_code in (401, 403):
        return "auth"
    if response.status_code == 429:
        return "rate_limited"
    if not response.is_success:
        return "bad_response"

    try:
        payload = response.json()
    except ValueError:
        return "bad_response"

    if not isinstance(payload, dict):
        return "bad_response"

    models = payload.get("models")
    if not isinstance(models, list):
        return "bad_response"

    names = {
        entry.get("name")
        for entry in models
        if isinstance(entry, dict) and isinstance(entry.get("name"), str)
    }
    if model in names or f"{model}:latest" in names:
        return None
    return "model_not_found"


def _perform_check(
    *, user: User, is_admin: bool, model_id: str | None
) -> tuple[ModelTestResult, str | None, str | None]:
    attempted_id = model_id or user.preferred_model or ""
    attempted_provider, _ = _split_model_id(attempted_id)

    try:
        effective_model = resolve_effective_model(
            model_id, user.preferred_model, user=user
        )
    except UnavailableModelError:
        return (
            ModelTestResult(
                ok=False,
                model_id=attempted_id,
                provider=attempted_provider,
                latency_ms=None,
                error_code="unavailable",
                message=_UNAVAILABLE_MESSAGE,
            ),
            None,
            None,
        )

    entry = _model_catalog_entry(effective_model, user=user)
    if entry is None:
        provider_name, _ = _split_model_id(effective_model)
        return (
            ModelTestResult(
                ok=False,
                model_id=effective_model,
                provider=provider_name,
                latency_ms=None,
                error_code="unavailable",
                message=_UNAVAILABLE_MESSAGE,
            ),
            None,
            None,
        )

    provider_name = str(entry["provider"])
    model_name = str(entry["model"])

    ollama_base_url: str | None = None
    display_base_url: str | None = None
    base_url_fallback: bool | None = None
    if provider_name == AI_PROVIDER_OLLAMA:
        ollama_base_url, fell_back_to_default = resolve_ollama_base_url_with_fallback(
            settings.ollama_base_url
        )
        display_base_url = _redact_userinfo(ollama_base_url)
        if is_admin:
            base_url_fallback = fell_back_to_default

    def failure(code: ModelTestErrorCode) -> ModelTestResult:
        return ModelTestResult(
            ok=False,
            model_id=effective_model,
            provider=provider_name,
            latency_ms=None,
            error_code=code,
            message=_error_message(
                code,
                is_admin=is_admin,
                provider_name=provider_name,
                base_url=display_base_url,
                model_name=model_name,
            ),
            base_url=display_base_url if is_admin else None,
            base_url_fallback=base_url_fallback,
        )

    if provider_name == AI_PROVIDER_OLLAMA:
        started_at = time.perf_counter()
        tags_error = _check_ollama_tags(ollama_base_url, model_name)
        latency_ms = round((time.perf_counter() - started_at) * 1000)
        if tags_error is not None:
            return failure(tags_error), provider_name, model_name

        supports_vision: bool | None = None
        if bool(entry.get("vision")):
            supports_vision = ollama_model_supports_vision(ollama_base_url, model_name)

        return (
            ModelTestResult(
                ok=True,
                model_id=effective_model,
                provider=provider_name,
                latency_ms=latency_ms,
                error_code=None,
                message=_SUCCESS_MESSAGE,
                supports_vision=supports_vision,
                base_url=display_base_url if is_admin else None,
                base_url_fallback=base_url_fallback,
            ),
            provider_name,
            model_name,
        )

    try:
        provider = get_single_text_generation_provider(
            effective_model,
            user=user,
            timeout_seconds=MODEL_CHECK_TIMEOUT_SECONDS,
        )
        started_at = time.perf_counter()
        model_names = provider.list_model_names()
    except TextGenerationConnectionError:
        return failure("unreachable"), provider_name, model_name
    except TextGenerationTimeoutError:
        return failure("timeout"), provider_name, model_name
    except TextGenerationAuthError:
        return failure("auth"), provider_name, model_name
    except TextGenerationRateLimitError:
        return failure("rate_limited"), provider_name, model_name
    except TextGenerationError:
        return failure("bad_response"), provider_name, model_name

    latency_ms = round((time.perf_counter() - started_at) * 1000)

    if model_name not in model_names:
        return failure("model_not_found"), provider_name, model_name

    return (
        ModelTestResult(
            ok=True,
            model_id=effective_model,
            provider=provider_name,
            latency_ms=latency_ms,
            error_code=None,
            message=_SUCCESS_MESSAGE,
            supports_vision=None,
            base_url=display_base_url if is_admin else None,
            base_url_fallback=base_url_fallback,
        ),
        provider_name,
        model_name,
    )


def check_model(*, user: User, is_admin: bool, model_id: str | None) -> ModelTestResult:
    started_at = time.perf_counter()
    result, log_provider, log_model = _perform_check(
        user=user, is_admin=is_admin, model_id=model_id
    )
    duration_ms = round((time.perf_counter() - started_at) * 1000)
    logger.info(
        "AI model check completed",
        extra={
            "event": "ai_model_check",
            "provider": log_provider,
            "model": log_model,
            "success": result.ok,
            "duration_ms": duration_ms,
            "error_code": result.error_code,
        },
    )
    return result
