"""Privacy-safe structured logs and CloudWatch Embedded Metric Format."""

from __future__ import annotations

import json
import logging
import re
import time
from contextvars import ContextVar, Token
from datetime import datetime, timezone
from typing import Any, Mapping
from uuid import uuid4

_REQUEST_ID: ContextVar[str | None] = ContextVar("request_id", default=None)
_REQUEST_ID_PATTERN = re.compile(r"[A-Za-z0-9._-]{1,64}")
_SECRET_PATTERN = re.compile(
    r"(?i)(authorization|api[_-]?key|password|secret|token)\s*[:=]\s*[^\s,;]+"
)
_ALLOWED_FIELDS = (
    "duration_ms",
    "error_code",
    "exception_type",
    "http_method",
    "http_path",
    "http_status",
    "job_id",
    "worker_id",
)


def _redact(value: str) -> str:
    return _SECRET_PATTERN.sub(lambda match: f"{match.group(1)}=[REDACTED]", value)


class JsonFormatter(logging.Formatter):
    """Render one JSON object per record without traceback or request content."""

    def __init__(self, *, service: str, environment: str) -> None:
        super().__init__()
        self.service = service
        self.environment = environment

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "service": self.service,
            "environment": self.environment,
            "logger": record.name,
            "event": getattr(record, "event", "application_log"),
            "message": _redact(record.getMessage()),
        }
        request_id = getattr(record, "request_id", None) or _REQUEST_ID.get()
        if request_id is not None:
            payload["request_id"] = request_id
        for field in _ALLOWED_FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value
        if record.exc_info and "exception_type" not in payload:
            payload["exception_type"] = record.exc_info[0].__name__
        emf = getattr(record, "emf", None)
        if isinstance(emf, dict):
            payload.update(emf)
        return json.dumps(payload, ensure_ascii=True, separators=(",", ":"))


def configure_logging(*, service: str, environment: str) -> None:
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


def normalize_request_id(value: str | None) -> str:
    candidate = (value or "").strip()
    return candidate if _REQUEST_ID_PATTERN.fullmatch(candidate) else uuid4().hex


def bind_request_id(value: str) -> Token[str | None]:
    return _REQUEST_ID.set(value)


def reset_request_id(token: Token[str | None]) -> None:
    _REQUEST_ID.reset(token)


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
