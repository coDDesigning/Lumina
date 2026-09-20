"""Privacy-safe structured logs and CloudWatch Embedded Metric Format."""

from __future__ import annotations

import json
import logging
import re
import time
import traceback
from contextvars import ContextVar, Token
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_MAX_STACK_FRAMES = 12
_MAX_EXCEPTION_MESSAGE = 500

_REQUEST_ID: ContextVar[str | None] = ContextVar("request_id", default=None)
_OPERATION_CONTEXT: ContextVar[dict[str, Any]] = ContextVar(
    "operation_context", default={}
)
_REQUEST_ID_PATTERN = re.compile(r"[A-Za-z0-9._-]{1,64}")
_SECRET_PATTERN = re.compile(
    r"""(?ix)
    (?P<key>["']?(?:authorization|api[ _-]?key|password|secret|token)(?:\s+provided)?["']?\s*[:=]\s*["']?)
    (?:bearer\s+)?[^\s,;\"'{}()]+(?P<quote>["']?)
    |
    \b(?P<bearer>bearer)\s+[^\s,;\"'{}()]+
    """
)
_SQL_STATEMENT_PATTERN = re.compile(
    r"\[SQL:(?:(?!\n\[|\n\(Background on this error at:).)*(?<!\[REDACTED)\]",
    re.DOTALL,
)
_SQL_PARAMETERS_PATTERN = re.compile(
    r"\[parameters:(?:(?!\n\[|\n\(Background on this error at:).)*(?<!\[REDACTED)\]",
    re.DOTALL,
)
_URL_QUERY_PATTERN = re.compile(r"""https?://[^\s"'<>{}()?]+\?[^\s"'<>()]*""")
_EMAIL_PATTERN = re.compile(
    r"[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,255}\.[A-Za-z]{2,24}"
)
_VALIDATION_INPUT_PATTERN = re.compile(
    r"(\[type=\w+, input(?:_value)?=).*?(, input_type=\w+\])"
)
_MAX_REDACT_INPUT_LENGTH = 4000
_TRUNCATION_MARKER = "[REDACTED]"
_MAX_TRUNCATION_SETTLE_PASSES = 4
_ALLOWED_FIELDS = (
    "action",
    "application_version",
    "ai_response_bytes",
    "ai_response_excerpt",
    "ai_response_keys",
    "ai_response_sha256",
    "ai_response_type",
    "ai_validation_errors",
    "attempt_number",
    "auth_state",
    "course_id",
    "client_fingerprint",
    "document_id",
    "duration_ms",
    "error_category",
    "error_code",
    "error_class",
    "exception_chain",
    "exception_type",
    "failed_stage",
    "generation_type",
    "http_method",
    "http_path",
    "http_status",
    "item_count",
    "job_id",
    "job_status",
    "job_type",
    "lock_holder",
    "maintenance_task",
    "model",
    "operation_id",
    "owner_id",
    "page_number",
    "parent_operation_id",
    "pricing_version",
    "profile_document_id",
    "prompt_tokens",
    "provider",
    "rate_limit_control",
    "rate_limit_feature",
    "reason",
    "related_request_id",
    "response_bytes",
    "retry_after_seconds",
    "runbook",
    "stage",
    "success",
    "stack",
    "user_id",
    "visual_index",
    "worker_id",
)


def _safe_cut(text: str, limit: int) -> str:
    cut = max(0, min(limit, len(text)))
    marker_len = len(_TRUNCATION_MARKER)
    search_start = max(0, cut - marker_len + 1)
    for start in range(search_start, cut):
        if (
            text[start : start + marker_len] == _TRUNCATION_MARKER
            and start + marker_len > cut
        ):
            return text[:start]
    return text[:cut]


def redact(value: str) -> str:
    def _replace_secret(match: re.Match[str]) -> str:
        if match.group("bearer"):
            return f"{match.group('bearer')} [REDACTED]"
        quote = match.group("quote") or ""
        return f"{match.group('key')}[REDACTED]{quote}"

    def _replace_url_query(match: re.Match[str]) -> str:
        url = match.group(0)
        return f"{url[: url.index('?') + 1]}[REDACTED]"

    def _apply_patterns(text: str) -> str:
        text = _URL_QUERY_PATTERN.sub(_replace_url_query, text)
        text = _SECRET_PATTERN.sub(_replace_secret, text)
        text = _SQL_STATEMENT_PATTERN.sub("[SQL: [REDACTED]]", text)
        text = _SQL_PARAMETERS_PATTERN.sub("[parameters: [REDACTED]]", text)
        text = _EMAIL_PATTERN.sub("[REDACTED]", text)
        text = _VALIDATION_INPUT_PATTERN.sub(r"\1[REDACTED]\2", text)
        return text

    truncated = len(value) > _MAX_REDACT_INPUT_LENGTH
    if truncated:
        value = value[:_MAX_REDACT_INPUT_LENGTH]
    value = _apply_patterns(value)

    if truncated or len(value) > _MAX_REDACT_INPUT_LENGTH:
        budget = _MAX_REDACT_INPUT_LENGTH - len(_TRUNCATION_MARKER) - 1
        value = f"{_safe_cut(value, budget)} {_TRUNCATION_MARKER}"
        for _ in range(_MAX_TRUNCATION_SETTLE_PASSES):
            settled = _apply_patterns(value)
            if len(settled) > _MAX_REDACT_INPUT_LENGTH:
                settled = f"{_safe_cut(settled, budget)} {_TRUNCATION_MARKER}"
            if settled == value:
                break
            value = settled
    return value


def _exception_type_chain(exc: BaseException | None) -> list[str]:
    """Name every exception behind a failure without recording any content.

    A wrapped error reports only the outermost type, which is rarely the one
    that explains the failure. Type names carry no request content, so the
    chain stays safe to log.
    """
    names: list[str] = []
    seen: set[int] = set()
    current = exc
    while current is not None and id(current) not in seen and len(names) < 10:
        seen.add(id(current))
        names.append(type(current).__name__)
        current = current.__cause__ or current.__context__
    return names


def _exception_message(exc: BaseException | None) -> str | None:
    parts: list[str] = []
    seen: set[int] = set()
    current = exc
    while current is not None and id(current) not in seen and len(parts) < 10:
        seen.add(id(current))
        text = str(current).strip()
        parts.append(
            f"{type(current).__name__}: {text}" if text else type(current).__name__
        )
        current = current.__cause__ or current.__context__
    if not parts:
        return None
    return redact(" <- ".join(parts))[:_MAX_EXCEPTION_MESSAGE]


def _innermost_exception(exc: BaseException) -> BaseException:
    seen: set[int] = set()
    current = exc
    while len(seen) < 10:
        seen.add(id(current))
        nxt = current.__cause__ or current.__context__
        if nxt is None or id(nxt) in seen:
            return current
        current = nxt
    return current


def _stack_frames(exc: BaseException) -> list[str]:
    frames: list[str] = []
    for frame in traceback.extract_tb(_innermost_exception(exc).__traceback__):
        try:
            location = Path(frame.filename).resolve().relative_to(_PROJECT_ROOT)
            name = location.as_posix()
        except (ValueError, OSError):
            name = "<external>"
        frames.append(redact(f"{name}:{frame.lineno} in {frame.name}"))
    return frames[-_MAX_STACK_FRAMES:]


class JsonFormatter(logging.Formatter):
    """Render one JSON object per record with secrets redacted.

    Records logged with an exception carry its redacted, truncated message
    chain, and at WARNING or above also `stack`: project relative frames.
    """

    def __init__(self, *, service: str, environment: str) -> None:
        super().__init__()
        self.service = service
        self.environment = environment

    def format(self, record: logging.LogRecord) -> str:
        timestamp = getattr(record, "_lumina_timestamp", None)
        if timestamp is None:
            timestamp = datetime.now(timezone.utc).isoformat()
            record._lumina_timestamp = timestamp
        event_id = getattr(record, "_lumina_event_id", None)
        if event_id is None:
            event_id = uuid4().hex
            record._lumina_event_id = event_id
        payload: dict[str, Any] = {
            "event_id": event_id,
            "timestamp": timestamp,
            "level": record.levelname,
            "service": self.service,
            "environment": self.environment,
            "logger": record.name,
            "event": getattr(record, "event", "application_log"),
            "message": redact(record.getMessage()),
        }
        request_id = getattr(record, "request_id", None) or _REQUEST_ID.get()
        if request_id is not None:
            payload["request_id"] = request_id
        for field, value in _OPERATION_CONTEXT.get().items():
            if field in _ALLOWED_FIELDS and value is not None:
                payload[field] = value
        for field in _ALLOWED_FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value
        if record.exc_info and "exception_type" not in payload:
            payload["exception_type"] = record.exc_info[0].__name__
            chain = _exception_type_chain(record.exc_info[1])
            if len(chain) > 1:
                payload["exception_chain"] = chain
        if record.exc_info and "exception_message" not in payload:
            message = _exception_message(record.exc_info[1])
            if message:
                payload["exception_message"] = message
        if (
            record.levelno >= logging.ERROR
            and record.exc_info
            and record.exc_info[1] is not None
            and "stack" not in payload
        ):
            frames = _stack_frames(record.exc_info[1])
            if frames:
                payload["stack"] = frames
        emf = getattr(record, "emf", None)
        if isinstance(emf, dict):
            payload.update(emf)
        return json.dumps(payload, ensure_ascii=True, separators=(",", ":"))


def configure_logging(
    *,
    service: str,
    environment: str,
    persistence_path: str | None = None,
    retention_days: int = 30,
    max_records: int = 500_000,
) -> None:
    """Apply the shared formatter without removing test or platform handlers."""
    formatter = JsonFormatter(service=service, environment=environment)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if not root.handlers:
        root.addHandler(logging.StreamHandler())
    for handler in root.handlers:
        handler.setFormatter(formatter)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        for handler in logging.getLogger(name).handlers:
            handler.setFormatter(formatter)
    logging.getLogger("uvicorn.access").disabled = True
    logging.getLogger("alembic").setLevel(logging.WARNING)
    if persistence_path and not any(
        getattr(handler, "_lumina_operational_handler", False)
        for handler in root.handlers
    ):
        from backend.app.operational_events import OperationalEventHandler

        root.addHandler(
            OperationalEventHandler(
                persistence_path,
                service=service,
                environment=environment,
                retention_days=retention_days,
                max_records=max_records,
            )
        )


def normalize_request_id(value: str | None) -> str:
    candidate = (value or "").strip()
    return candidate if _REQUEST_ID_PATTERN.fullmatch(candidate) else uuid4().hex


def get_request_id() -> str | None:
    return _REQUEST_ID.get()


def bind_request_id(value: str | None) -> Token[str | None]:
    return _REQUEST_ID.set(value)


def reset_request_id(token: Token[str | None]) -> None:
    _REQUEST_ID.reset(token)


def get_operation_context() -> Mapping[str, Any]:
    return _OPERATION_CONTEXT.get()


def bind_operation_context(**values: Any) -> Token[dict[str, Any]]:
    context = dict(_OPERATION_CONTEXT.get())
    context.update({key: value for key, value in values.items() if value is not None})
    return _OPERATION_CONTEXT.set(context)


def reset_operation_context(token: Token[dict[str, Any]]) -> None:
    _OPERATION_CONTEXT.reset(token)


def emit_emf_metrics(
    metrics: Mapping[str, float | int],
    *,
    dimensions: Mapping[str, str],
    units: Mapping[str, str] | None = None,
    namespace: str = "Lumina/Worker",
) -> None:
    """Emit a CloudWatch EMF event; stdout remains the only transport."""
    if not metrics:
        return
    units = units or {}
    definitions = [
        {"Name": name, "Unit": units.get(name, "Count")} for name in sorted(metrics)
    ]
    emf: dict[str, Any] = {
        "_aws": {
            "Timestamp": int(time.time() * 1000),
            "CloudWatchMetrics": [
                {
                    "Namespace": namespace,
                    "Dimensions": [sorted(dimensions)],
                    "Metrics": definitions,
                }
            ],
        },
        **dimensions,
        **metrics,
    }
    logging.getLogger("lumina.metrics").info(
        "CloudWatch metric",
        extra={"event": "cloudwatch_emf", "emf": emf},
    )
