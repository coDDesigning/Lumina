import json
import logging
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from backend.app.config import MODE_HOSTED, MODE_SELF_HOSTED, settings
from backend.app.models import AiUsageLog
from backend.app.operational_events import (
    OperationalEventHandler,
    sanitize_operational_payload,
)
from routes import admin as admin_route
from services.admin_logs import (
    InvalidLogCursorError,
    LogFilters,
    LogReadService,
)


def _emit(
    handler: OperationalEventHandler,
    event: str,
    *,
    level: int = logging.INFO,
    **extra: object,
) -> str:
    record = logging.LogRecord(
        "lumina.test",
        level,
        __file__,
        1,
        "SECRET-CONTENT must not enter the operational store",
        (),
        None,
    )
    record.event = event
    for key, value in extra.items():
        setattr(record, key, value)
    handler.emit(record)
    return f"operational:{record._lumina_event_id}"


def _local_settings(path: Path):
    return replace(
        settings,
        deployment_mode=MODE_SELF_HOSTED,
        operational_log_path=str(path),
    )


def _hosted_settings():
    return replace(
        settings,
        deployment_mode=MODE_HOSTED,
        operational_log_persistence_enabled=False,
        operational_log_cloudwatch_group="/ecs/lumina-production",
        operational_log_cloudwatch_region="us-east-1",
    )


def _window() -> LogFilters:
    now = datetime.now(timezone.utc)
    return LogFilters(start=now - timedelta(minutes=5), end=now + timedelta(minutes=5))


def test_operational_sink_stores_only_reviewed_fields(
    tmp_path: Path, db_session
) -> None:
    path = tmp_path / "operational.db"
    handler = OperationalEventHandler(
        str(path),
        service="api",
        environment="test",
        retention_days=30,
        max_records=10_000,
    )
    event_id = _emit(
        handler,
        "http_request_failed",
        level=logging.ERROR,
        operation_id="api:operation-1",
        http_method="POST",
        http_path="/api/courses/{course_id}/quiz",
        http_status=500,
        error_code="database_unavailable",
        application_version="SECRET VERSION",
        ai_response_keys=["valid_key", "SECRET FIELD"],
        ai_validation_errors=["items.0.name: string_type", "SECRET VALIDATION"],
        unreviewed_content="SECRET-FIELD",
    )

    service = LogReadService(db_session, app_settings=_local_settings(path))
    detail = service.detail(event_id).record

    assert detail.operation_id == "api:operation-1"
    assert detail.http_path == "/api/courses/{course_id}/quiz"
    assert detail.error_code == "database_unavailable"
    assert detail.error_signature is not None
    assert "SECRET" not in detail.model_dump_json()
    assert detail.details["ai_response_keys"] == ["valid_key", "*"]
    assert detail.details["ai_validation_errors"] == [
        "items.0.name: string_type",
        "*",
    ]


def test_cursor_is_bound_to_the_fixed_filter_window(tmp_path: Path, db_session) -> None:
    path = tmp_path / "operational.db"
    handler = OperationalEventHandler(
        str(path),
        service="api",
        environment="test",
        retention_days=30,
        max_records=10_000,
    )
    _emit(handler, "http_request_completed", operation_id="api:first")
    _emit(handler, "http_request_completed", operation_id="api:second")
    service = LogReadService(db_session, app_settings=_local_settings(path))
    filters = replace(_window(), sources=("operational",))

    first = service.list(filters, limit=1)
    assert len(first.records) == 1
    assert first.next_cursor is not None
    second = service.list(filters, limit=1, cursor=first.next_cursor)
    assert len(second.records) == 1
    assert second.records[0].id != first.records[0].id

    changed = replace(filters, levels=("ERROR",))
    with pytest.raises(InvalidLogCursorError):
        service.list(changed, limit=1, cursor=first.next_cursor)


def test_trace_follows_parent_and_child_operations(tmp_path: Path, db_session) -> None:
    path = tmp_path / "operational.db"
    handler = OperationalEventHandler(
        str(path),
        service="api",
        environment="test",
        retention_days=30,
        max_records=10_000,
    )
    anchor = _emit(handler, "http_request_completed", operation_id="api:parent")
    _emit(
        handler,
        "generation_job_enqueued",
        operation_id="api:parent",
        job_id=7,
        job_type="quiz",
        job_status="queued",
    )
    _emit(
        handler,
        "generation_job_claimed",
        operation_id="generation_job:quiz:7",
        parent_operation_id="api:parent",
        job_id=7,
        job_type="quiz",
        job_status="running",
    )
    _emit(
        handler,
        "generation_job_completed",
        operation_id="generation_job:quiz:7",
        parent_operation_id="api:parent",
        job_id=7,
        job_type="quiz",
        job_status="succeeded",
    )

    trace = LogReadService(db_session, app_settings=_local_settings(path)).trace(anchor)

    assert trace.correlation_status == "correlated"
    assert [record.event for record in trace.records] == [
        "http_request_completed",
        "generation_job_enqueued",
        "generation_job_claimed",
        "generation_job_completed",
    ]


def test_partial_source_makes_aggregate_counts_unavailable(
    tmp_path: Path, db_session
) -> None:
    service = LogReadService(
        db_session,
        app_settings=_local_settings(tmp_path / "missing-operational.db"),
    )
    summary = service.summary(replace(_window(), sources=("operational",)))

    assert summary.partial is True
    assert summary.counts.events is None
    assert summary.error_groups == []


def test_hosted_source_reads_only_the_configured_group_and_sanitizes_events(
    db_session,
) -> None:
    now = datetime.now(timezone.utc)
    payload = {
        "event_id": "event-1",
        "timestamp": now.isoformat(),
        "level": "ERROR",
        "service": "api",
        "environment": "production",
        "logger": "main",
        "event": "http_request_failed",
        "message": "SECRET-CONTENT",
        "operation_id": "api:operation-1",
        "unreviewed_content": "SECRET-FIELD",
    }

    class LogsClient:
        def __init__(self) -> None:
            self.requests: list[dict[str, object]] = []

        def filter_log_events(self, **request):
            self.requests.append(request)
            return {
                "events": [
                    {"message": json.dumps(payload)},
                    {"message": "not-json"},
                ]
            }

    client = LogsClient()
    service = LogReadService(
        db_session,
        app_settings=_hosted_settings(),
        cloudwatch_client=client,
    )

    result = service.list(
        LogFilters(
            start=now - timedelta(minutes=1),
            end=now + timedelta(minutes=1),
        )
    )

    assert client.requests[0]["logGroupName"] == "/ecs/lumina-production"
    assert len(result.records) == 1
    assert result.records[0].operation_id == "api:operation-1"
    assert "SECRET" not in result.records[0].model_dump_json()
    assert result.source_health[0].status == "available"
    assert result.omitted_records == 1


def test_admin_log_api_reads_ai_telemetry_and_requires_admin(authz_api) -> None:
    now = datetime.now(timezone.utc)
    with authz_api.session_factory() as session:
        row = AiUsageLog(
            user_id=authz_api.user_a_id,
            course_id=authz_api.a_course_id,
            generation_type="quiz",
            provider="gemini",
            model="gemini-2.5-flash",
            success=False,
            error_category="provider_error",
            operation_id="generation_job:quiz:8",
            job_id=8,
            job_type="quiz",
            attempt_number=2,
            created_at=now,
        )
        session.add(row)
        session.commit()
        row_id = row.id

    params = {
        "source": "ai_telemetry",
        "start": (now - timedelta(minutes=1)).isoformat(),
        "end": (now + timedelta(minutes=1)).isoformat(),
    }
    denied = authz_api.client.get(
        "/api/admin/logs", params=params, headers=authz_api.authorization_a
    )
    response = authz_api.client.get(
        "/api/admin/logs", params=params, headers=authz_api.authorization_admin
    )

    assert denied.status_code == 403
    assert response.status_code == 200
    record = response.json()["data"]["records"][0]
    assert record["id"] == f"ai_telemetry:{row_id}"
    assert record["error_code"] == "provider_error"
    assert record["operation_id"] == "generation_job:quiz:8"
    assert record["attempt_number"] == 2


def test_admin_log_export_carries_bounds_and_only_sanitized_records(
    authz_api, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "operational.db"
    handler = OperationalEventHandler(
        str(path),
        service="api",
        environment="test",
        retention_days=30,
        max_records=10_000,
    )
    _emit(
        handler,
        "http_request_failed",
        level=logging.ERROR,
        operation_id="api:operation-1",
    )
    app_settings = _local_settings(path)
    monkeypatch.setattr(
        admin_route,
        "_log_service",
        lambda db: LogReadService(db, app_settings=app_settings),
    )
    now = datetime.now(timezone.utc)

    response = authz_api.client.get(
        "/api/admin/logs/export",
        params={
            "source": "operational",
            "start": (now - timedelta(minutes=1)).isoformat(),
            "end": (now + timedelta(minutes=1)).isoformat(),
            "format": "jsonl",
        },
        headers=authz_api.authorization_admin,
    )

    assert response.status_code == 200
    assert response.headers["X-Export-Record-Limit"] == "10000"
    assert response.headers["X-Export-Truncated"] == "false"
    lines = response.text.splitlines()
    assert json.loads(lines[0])["type"] == "export_metadata"
    assert json.loads(lines[1])["operation_id"] == "api:operation-1"
    assert "SECRET" not in response.text


def test_csv_export_cells_cannot_become_spreadsheet_formulas() -> None:
    assert admin_route._csv_cell("=cmd") == "'=cmd"
    assert admin_route._csv_cell("+SUM(A1)") == "'+SUM(A1)"


def test_ai_only_query_rejects_a_filter_it_cannot_apply(authz_api) -> None:
    now = datetime.now(timezone.utc)
    response = authz_api.client.get(
        "/api/admin/logs",
        params={
            "source": "ai_telemetry",
            "exception_type": "TimeoutError",
            "start": (now - timedelta(minutes=1)).isoformat(),
            "end": (now + timedelta(minutes=1)).isoformat(),
        },
        headers=authz_api.authorization_admin,
    )

    assert response.status_code == 422
    assert response.headers["X-Error-Code"] == "unsupported_log_filter"


def test_client_error_reports_are_authenticated_and_deduplicated(
    authz_api, caplog: pytest.LogCaptureFixture
) -> None:
    payload = {
        "route_template": "/courses/{course_id}",
        "application_version": "build-123",
        "error_class": "ChunkLoadError",
        "api_request_id": "request-123",
        "fingerprint": "0123456789abcdef",
    }

    denied = authz_api.client.post("/api/client-errors", json=payload)
    unsafe_route = authz_api.client.post(
        "/api/client-errors",
        json={**payload, "route_template": "/private-note"},
        headers=authz_api.authorization_a,
    )
    unsafe_class = authz_api.client.post(
        "/api/client-errors",
        json={**payload, "error_class": "PrivateCourseTitle"},
        headers=authz_api.authorization_a,
    )
    with caplog.at_level(logging.WARNING, logger="routes.client_error"):
        accepted = authz_api.client.post(
            "/api/client-errors", json=payload, headers=authz_api.authorization_a
        )
        duplicate = authz_api.client.post(
            "/api/client-errors", json=payload, headers=authz_api.authorization_a
        )

    assert denied.status_code == 401
    assert unsafe_route.status_code == 422
    assert unsafe_class.status_code == 422
    assert accepted.status_code == 200
    assert accepted.json()["data"] == {"accepted": True}
    assert duplicate.status_code == 200
    assert duplicate.json()["data"] == {"accepted": False}
    assert (
        sum(
            record.event == "client_error_reported"
            for record in caplog.records
            if hasattr(record, "event")
        )
        == 1
    )


def test_payload_sanitizer_rejects_messages_and_unsafe_identifiers() -> None:
    payload = sanitize_operational_payload(
        {
            "event_id": "event-1",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": "ERROR",
            "service": "api",
            "environment": "test",
            "logger": "lumina.test",
            "event": "http_request_failed",
            "message": "SECRET-CONTENT",
            "error_code": "unsafe error with content",
        }
    )

    assert payload is not None
    assert "message" not in payload
    assert "error_code" not in payload
    assert "SECRET" not in str(payload)
