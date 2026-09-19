from __future__ import annotations

import ast
import json
import logging
import threading
from pathlib import Path

import pytest

from backend.app.observability import JsonFormatter
from backend.app.operational_events import EVENT_DESCRIPTIONS
from workers import document_processor

_ROOT = Path(__file__).resolve().parents[1]
_PACKAGES = ("backend", "routes", "services", "tasks", "utils", "workers")
_LEVELS = {"info", "warning", "error", "exception", "critical"}


def _project_files() -> list[Path]:
    files = [_ROOT / "main.py"]
    for package in _PACKAGES:
        files.extend(sorted((_ROOT / package).rglob("*.py")))
    return files


def _logger_calls(tree: ast.AST):
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in _LEVELS
            and isinstance(node.func.value, ast.Name)
            and "log" in node.func.value.id.lower()
        ):
            yield node


def _event_of(call: ast.Call) -> str | None:
    for keyword in call.keywords:
        if keyword.arg != "extra" or not isinstance(keyword.value, ast.Dict):
            continue
        for key, value in zip(keyword.value.keys, keyword.value.values):
            if (
                isinstance(key, ast.Constant)
                and key.value == "event"
                and isinstance(value, ast.Constant)
                and isinstance(value.value, str)
            ):
                return value.value
    return None


def _violations() -> list[str]:
    problems: list[str] = []
    for path in _project_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for call in _logger_calls(tree):
            location = f"{path.relative_to(_ROOT).as_posix()}:{call.lineno}"
            event = _event_of(call)
            if event is None:
                problems.append(f"{location} has no literal extra['event']")
            elif event not in EVENT_DESCRIPTIONS:
                problems.append(f"{location} logs undescribed event {event!r}")
    return problems


def test_every_log_call_names_a_described_event() -> None:
    problems = _violations()
    assert not problems, "\n".join(problems)


class _FormattingHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.INFO)
        self.setFormatter(JsonFormatter(service="worker", environment="test"))
        self.payloads: list[dict] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.payloads.append(json.loads(self.format(record)))


def test_maintenance_failures_carry_their_task_correlation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(**_kwargs) -> None:
        raise RuntimeError("purge backend unavailable")

    for name in ("run_account_purge", "run_purge", "run_document_purge"):
        monkeypatch.setattr(document_processor, name, fail)
    monkeypatch.setattr(
        document_processor, "release_expired_generation_locks", lambda _session: 0
    )

    class _Session:
        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def commit(self) -> None:
            return None

    schedule = document_processor._MaintenanceSchedule()
    schedule.next_recovery = float("inf")
    schedule.purge_interval = 3600.0
    schedule.next_purge = 0.0
    schedule.next_backfill = float("inf")
    schedule.next_ai_usage_cleanup = float("inf")
    schedule.next_visual_description_sweep = float("inf")

    handler = _FormattingHandler()
    worker_logger = logging.getLogger("workers.document_processor")
    worker_logger.addHandler(handler)
    try:
        document_processor._maintenance_cycle(
            schedule,
            session_factory=_Session,
            storage=object(),
            stop=threading.Event(),
        )
    finally:
        worker_logger.removeHandler(handler)

    failures = [p for p in handler.payloads if p["event"] == "maintenance_task_failed"]
    assert {p["maintenance_task"] for p in failures} == {
        "account_purge",
        "course_purge",
        "document_purge",
    }
    for payload in failures:
        assert payload["operation_id"] == f"maintenance:{payload['maintenance_task']}"
        assert payload["exception_message"] == "RuntimeError: purge backend unavailable"
