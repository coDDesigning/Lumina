"""Safe log fields describing an AI response that failed.

Everything here is built to be attached to a log record through ``extra=`` and
to survive ``JsonFormatter``'s allowlist. The rules this module exists to keep:

- A model-authored value is never emitted. Only sizes, a digest, top-level key
  names, and pydantic ``loc``/``type`` pairs leave this module.
- Key names and ``loc`` components are model output too. Each one is emitted
  verbatim only when it looks like a schema field name; anything else is
  masked, so a key built out of course material cannot reach a log.
- Pydantic's ``input`` is never materialised, because it holds the rejected
  content itself.
- The raw response text is emitted only when
  ``AI_LOG_RAW_RESPONSE_ON_FAILURE`` is on, truncated and redacted.

This module must not import from ``services``: ``services.ai_usage_logger``
imports it, and ``utils.ai_errors`` already depends on ``services``. Attributes
carried by provider exceptions are read duck-typed for the same reason.
"""

import hashlib
import json
import re
from typing import Any, Mapping

from pydantic import ValidationError

from backend.app.config import settings
from backend.app.observability import redact

MAX_EXCERPT_CHARACTERS = 2000
MAX_RESPONSE_KEYS = 20
MAX_VALIDATION_ERRORS = 20
MAX_CHAIN_DEPTH = 10

_SAFE_NAME = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
_MASK = "*"


def _safe_name(value: object) -> str:
    text = str(value)
    return text if _SAFE_NAME.match(text) else _MASK


def _serialize(response: object) -> str | None:
    if response is None:
        return None
    if isinstance(response, str):
        return response
    try:
        return json.dumps(response, ensure_ascii=True, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return repr(response)


def _response_type(response: object) -> str:
    if response is None:
        return "null"
    if isinstance(response, Mapping):
        return "dict"
    if isinstance(response, str):
        return "str"
    if isinstance(response, (list, tuple)):
        return "list"
    return type(response).__name__


def _exception_chain(exc: BaseException | None) -> list[BaseException]:
    chain: list[BaseException] = []
    seen: set[int] = set()
    current = exc
    while (
        current is not None and id(current) not in seen and len(chain) < MAX_CHAIN_DEPTH
    ):
        seen.add(id(current))
        chain.append(current)
        current = current.__cause__ or current.__context__
    return chain


def _raw_response_from(exc: BaseException | None) -> str | None:
    for error in _exception_chain(exc):
        raw = getattr(error, "raw_response", None)
        if isinstance(raw, str) and raw:
            return raw
    return None


def resolve_response(
    response: object = None,
    exc: BaseException | None = None,
    raw_text: str | None = None,
) -> object:
    if response is not None:
        return response
    if raw_text is not None:
        return raw_text
    return _raw_response_from(exc)


def describe_response(response: object) -> dict[str, object]:
    if response is None:
        return {}

    serialized = _serialize(response)
    if serialized is None:
        return {}

    encoded = serialized.encode("utf-8", errors="replace")
    fields: dict[str, object] = {
        "ai_response_bytes": len(encoded),
        "ai_response_type": _response_type(response),
        "ai_response_sha256": hashlib.sha256(encoded).hexdigest()[:16],
    }

    if isinstance(response, Mapping):
        keys = [_safe_name(key) for key in list(response)[:MAX_RESPONSE_KEYS]]
        fields["ai_response_keys"] = keys

    return fields


def describe_validation_error(exc: BaseException | None) -> list[str]:
    for error in _exception_chain(exc):
        if isinstance(error, ValidationError):
            return _describe_pydantic_error(error)
    for error in _exception_chain(exc):
        if isinstance(error, ValueError):
            message = redact(str(error))[:MAX_EXCERPT_CHARACTERS]
            return [message] if message else []
    return []


def _describe_pydantic_error(exc: ValidationError) -> list[str]:
    described: list[str] = []
    for error in exc.errors(
        include_url=False, include_context=False, include_input=False
    )[:MAX_VALIDATION_ERRORS]:
        location = ".".join(
            str(part) if isinstance(part, int) else _safe_name(part)
            for part in error.get("loc", ())
        )
        described.append(f"{location or _MASK}: {error.get('type', 'unknown')}")
    return described


def raw_excerpt(response: object) -> str | None:
    if not settings.ai_log_raw_response_on_failure:
        return None
    serialized = _serialize(response)
    if not serialized:
        return None
    return redact(serialized[:MAX_EXCERPT_CHARACTERS])


def ai_failure_fields(
    *,
    generation_type: object = None,
    provider: str | None = None,
    model: str | None = None,
    error_category: object = None,
    response: object = None,
    exc: BaseException | None = None,
    raw_text: str | None = None,
) -> dict[str, Any]:
    resolved = resolve_response(response=response, exc=exc, raw_text=raw_text)

    fields: dict[str, Any] = {"event": "ai_generation_failed"}
    if generation_type is not None:
        fields["generation_type"] = getattr(generation_type, "value", generation_type)
    if provider:
        fields["provider"] = provider
    if model:
        fields["model"] = model
    if error_category is not None:
        fields["error_category"] = getattr(error_category, "value", error_category)

    fields.update(describe_response(resolved))

    validation_errors = describe_validation_error(exc)
    if validation_errors:
        fields["ai_validation_errors"] = validation_errors

    excerpt = raw_excerpt(resolved)
    if excerpt is not None:
        fields["ai_response_excerpt"] = excerpt

    if exc is not None:
        fields["exception_type"] = type(exc).__name__

    return fields
