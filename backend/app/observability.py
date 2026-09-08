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
    "job_id",
    "job_status",
    "job_type",
    "model",
    "operation_id",
    "owner_id",
    "parent_operation_id",
    "pricing_version",
    "prompt_tokens",
    "provider",
    "rate_limit_control",
    "rate_limit_feature",
    "related_request_id",
    "retry_after_seconds",
    "runbook",
    "stage",
    "success",
    "stack",
    "user_id",
    "worker_id",
)


def redact(value: str) -> str:
    def _replace(match: re.Match[str]) -> str:
        if match.group("bearer"):
            return f"{match.group('bearer')} [REDACTED]"
        quote = match.group("quote") or ""
        return f"{match.group('key')}[REDACTED]{quote}"

    return _SECRET_PATTERN.sub(_replace, value)


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
    """Render one JSON object per record without request content.

    Records logged at ERROR with an exception also carry `stack`: project
    relative frames only, never a traceback rendering or an exception message.
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
