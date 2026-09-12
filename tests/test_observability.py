import json
import logging
import multiprocessing
import os
import re
import sys
from pathlib import Path
from uuid import uuid4

import pytest

from backend.app.readiness import ReadinessError
from backend.app.observability import (
    JsonFormatter,
    bind_operation_context,
    emit_emf_metrics,
    normalize_request_id,
    reset_operation_context,
)
from services.processing_jobs import ClaimedJob


def _spawn_child_that_logs_an_exception(stderr_path: str, canary: str) -> None:
    """Module-level so a "spawn" context can import it in the fresh interpreter.

    Mirrors ``workers.document_processor._extraction_process``: a spawn child
    re-applies ``configure_logging`` and binds the correlation id, then logs an
    exception. Its stderr must be JSON, redacted, and correlated.
    """
    from backend.app.observability import bind_request_id, configure_logging

    sink = open(stderr_path, "w", encoding="utf-8")  # noqa: SIM115
    os.dup2(sink.fileno(), 2)
    sys.stderr = sink

    configure_logging(service="worker", environment="production")
    bind_request_id("corr-1")
    try:
        raise ValueError(canary)
    except ValueError:
        logging.getLogger("lumina.pipeline").exception("chunking failed")
    logging.getLogger("lumina.pipeline").info("breadcrumb below WARNING")
    sink.flush()
    sink.close()


def test_json_formatter_is_single_line_and_redacts_secrets() -> None:
    formatter = JsonFormatter(service="api", environment="production")
    record = logging.LogRecord(
        "lumina.test",
        logging.INFO,
        __file__,
        1,
        "request token=private-value completed",
        (),
        None,
    )
    record.event = "test_event"
    record.http_status = 200

    rendered = formatter.format(record)
    payload = json.loads(rendered)

    assert "\n" not in rendered
    assert payload["event"] == "test_event"
    assert payload["http_status"] == 200
    assert payload["service"] == "api"
    assert "private-value" not in rendered
    assert "[REDACTED]" in rendered


def test_request_id_is_preserved_only_when_header_safe() -> None:
    assert normalize_request_id("request-123") == "request-123"
    generated = normalize_request_id("unsafe request id")
    assert generated != "unsafe request id"
    assert len(generated) == 32


def test_request_middleware_returns_correlation_header(api_context) -> None:
    # A probe rather than "/": the root is the interface shell now, and whether
    # it exists depends on whether a build was baked into this deployment.
    response = api_context.client.get(
        "/health/live", headers={"X-Request-ID": "client-42"}
    )

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "client-42"


def test_emf_event_has_cloudwatch_schema() -> None:
    records: list[logging.LogRecord] = []

    class CapturingHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    logger = logging.getLogger("lumina.metrics")
    handler = CapturingHandler()
    logger.addHandler(handler)
    try:
        emit_emf_metrics(
            {"QueuedJobs": 3, "OldestQueuedAgeSeconds": 12.5},
            dimensions={"Service": "worker", "Environment": "production"},
            units={"OldestQueuedAgeSeconds": "Seconds"},
        )
    finally:
        logger.removeHandler(handler)

    record = records[-1]
    emf = record.emf
    assert record.event == "cloudwatch_emf"
    assert emf["QueuedJobs"] == 3
    assert emf["Service"] == "worker"
    definitions = emf["_aws"]["CloudWatchMetrics"][0]["Metrics"]
    assert {item["Name"] for item in definitions} == {
        "QueuedJobs",
        "OldestQueuedAgeSeconds",
    }


def test_worker_logging_includes_correlation_and_job_id() -> None:
    from backend.app.observability import bind_request_id, reset_request_id

    formatter = JsonFormatter(service="worker", environment="production")
    token = bind_request_id("corr-trace-999")
    try:
        record = logging.LogRecord(
            "lumina.worker",
            logging.INFO,
            __file__,
            1,
            "Job completed successfully",
            (),
            None,
        )
        record.job_id = 42
        record.worker_id = "worker-node-1"
        rendered = formatter.format(record)
        payload = json.loads(rendered)

        assert payload["request_id"] == "corr-trace-999"
        assert payload["job_id"] == 42
        assert payload["worker_id"] == "worker-node-1"
        assert payload["service"] == "worker"
    finally:
        reset_request_id(token)


def test_operation_context_is_inherited_and_record_fields_take_precedence() -> None:
    formatter = JsonFormatter(service="worker", environment="production")
    token = bind_operation_context(
        operation_id="generation_job:quiz:42",
        parent_operation_id="api:parent",
        job_id=42,
        attempt_number=2,
    )
    try:
        record = logging.LogRecord(
            "lumina.worker",
            logging.INFO,
            __file__,
            1,
            "Job completed successfully",
            (),
            None,
        )
        record.attempt_number = 3
        payload = json.loads(formatter.format(record))
    finally:
        reset_operation_context(token)

    assert payload["operation_id"] == "generation_job:quiz:42"
    assert payload["parent_operation_id"] == "api:parent"
    assert payload["job_id"] == 42
    assert payload["attempt_number"] == 3
    assert re.fullmatch(r"[a-f0-9]{32}", payload["event_id"])


def test_maintenance_logging_uses_structured_json() -> None:
    formatter = JsonFormatter(service="maintenance", environment="production")
    record = logging.LogRecord(
        "lumina.maintenance",
        logging.INFO,
        __file__,
        1,
        "Course purge finished: examined=1 purged=1 failed=0",
        (),
        None,
    )
    rendered = formatter.format(record)
    payload = json.loads(rendered)

    assert payload["service"] == "maintenance"
    assert payload["level"] == "INFO"
    assert "Course purge finished" in payload["message"]


def test_ai_provider_emf_metrics_schema() -> None:
    records: list[logging.LogRecord] = []

    class CapturingHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    logger = logging.getLogger("lumina.metrics")
    handler = CapturingHandler()
    logger.addHandler(handler)
    try:
        emit_emf_metrics(
            {"ProviderCalls": 1, "ProviderLatencyMs": 142.5, "ProviderErrors": 1},
            dimensions={
                "Service": "api",
                "Environment": "production",
                "Provider": "gemini",
            },
            units={"ProviderLatencyMs": "Milliseconds"},
            namespace="Lumina/AI",
        )
    finally:
        logger.removeHandler(handler)

    record = records[-1]
    emf = record.emf
    assert record.event == "cloudwatch_emf"
    assert emf["_aws"]["CloudWatchMetrics"][0]["Namespace"] == "Lumina/AI"
    assert emf["Provider"] == "gemini"
    assert emf["ProviderCalls"] == 1
    assert emf["ProviderLatencyMs"] == 142.5
    assert emf["ProviderErrors"] == 1
    definitions = emf["_aws"]["CloudWatchMetrics"][0]["Metrics"]
    units_by_name = {item["Name"]: item["Unit"] for item in definitions}
    assert units_by_name["ProviderLatencyMs"] == "Milliseconds"
    assert units_by_name["ProviderCalls"] == "Count"


def test_stage_failure_emf_metrics_schema() -> None:
    records: list[logging.LogRecord] = []

    class CapturingHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    logger = logging.getLogger("lumina.metrics")
    handler = CapturingHandler()
    logger.addHandler(handler)
    try:
        emit_emf_metrics(
            {"StageFailed": 1},
            dimensions={
                "Service": "worker",
                "Environment": "production",
                "Stage": "extracting_text",
            },
        )
    finally:
        logger.removeHandler(handler)

    record = records[-1]
    emf = record.emf
    assert record.event == "cloudwatch_emf"
    assert emf["_aws"]["CloudWatchMetrics"][0]["Namespace"] == "Lumina/Worker"
    assert emf["Stage"] == "extracting_text"
    assert emf["StageFailed"] == 1


def test_spawn_child_logs_are_json_redacted_and_correlated(tmp_path: Path) -> None:
    """P2-025: a spawn extraction child must not fall back to logging.lastResort.

    Every line it emits has to be one JSON object, carry the bound request_id,
    and never leak a traceback or raw exception text.
    """
    stderr_path = tmp_path / "child-stderr.log"
    context = multiprocessing.get_context("spawn")
    process = context.Process(
        target=_spawn_child_that_logs_an_exception,
        args=(str(stderr_path), "SECRET-CANARY"),
    )
    process.start()
    process.join(timeout=30)
    assert process.exitcode == 0

    lines = [
        line for line in stderr_path.read_text(encoding="utf-8").splitlines() if line
    ]
    assert lines, "child emitted nothing"
    for line in lines:
        payload = json.loads(line)  # every line is one JSON object
        assert payload["request_id"] == "corr-1"
        assert payload["service"] == "worker"

    body = "\n".join(lines)
    assert "Traceback" not in body
    assert "SECRET-CANARY" not in body
    # The INFO breadcrumb survives because the child's root level is INFO.
    assert any(json.loads(line)["level"] == "INFO" for line in lines)
    assert any(json.loads(line).get("exception_type") == "ValueError" for line in lines)
    # SCRUM-206: the child emits frames, and they are still canary-free.
    assert any("stack" in json.loads(line) for line in lines)


def test_extraction_process_configures_logging_before_binding_the_request_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P2-025: the fix itself - _extraction_process installs the JSON formatter
    first, otherwise binding the request id renders nothing."""
    from workers import document_processor

    calls: list[str] = []

    def record_configure(**kwargs) -> None:
        calls.append(f"configure_logging:{kwargs.get('service')}")

    def record_bind(value):
        calls.append(f"bind_request_id:{value}")

    def boom(*args, **kwargs):
        raise RuntimeError("extraction blew up")

    monkeypatch.setattr(document_processor, "WORKER_SHUTDOWN_SIGNALS", set())
    monkeypatch.setattr(document_processor, "configure_logging", record_configure)
    monkeypatch.setattr(document_processor, "bind_request_id", record_bind)
    monkeypatch.setattr(document_processor, "extract_document", boom)

    class FakeConnection:
        def send(self, _message) -> None:
            pass

        def recv(self):
            return ("continue",)

        def close(self) -> None:
            pass

    job = ClaimedJob(
        id=1,
        document_id=uuid4(),
        course_id=1,
        claim_token=str(uuid4()),
        attempt_count=1,
        max_attempts=3,
        storage_provider="test",
        storage_key="document.txt",
        file_hash="0" * 64,
        file_type="txt",
        file_size=16,
        correlation_id="corr-99",
    )

    document_processor._extraction_process(FakeConnection(), object(), job)

    assert calls[0] == "configure_logging:worker"
    assert "bind_request_id:corr-99" in calls
    assert calls.index("configure_logging:worker") < calls.index(
        "bind_request_id:corr-99"
    )


def test_course_purge_aged_tombstone_emf_metrics_schema() -> None:
    records: list[logging.LogRecord] = []

    class CapturingHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    logger = logging.getLogger("lumina.metrics")
    handler = CapturingHandler()
    logger.addHandler(handler)
    try:
        emit_emf_metrics(
            {
                "CoursesExamined": 2,
                "CoursesPurged": 1,
                "CoursesFailed": 1,
                "AgedTombstones": 1,
                "OldestTombstoneAgeSeconds": 7200.0,
            },
            dimensions={"Service": "course_purge", "Environment": "production"},
            units={"OldestTombstoneAgeSeconds": "Seconds"},
        )
    finally:
        logger.removeHandler(handler)

    record = records[-1]
    emf = record.emf
    assert record.event == "cloudwatch_emf"
    assert emf["Service"] == "course_purge"
    assert emf["AgedTombstones"] == 1
    assert emf["OldestTombstoneAgeSeconds"] == 7200.0


def test_configure_logging_disables_uvicorn_access_logger() -> None:
    from backend.app.observability import configure_logging

    configure_logging(service="api", environment="production")
    assert logging.getLogger("uvicorn.access").disabled is True


@pytest.mark.parametrize(
    ("message", "sensitive_snippets", "preserved_snippets"),
    [
        (
            '{"Authorization": "Bearer eyJsecret123"}',
            ["eyJsecret123"],
            ['{"Authorization": "[REDACTED]"}'],
        ),
        (
            "headers={'authorization': 'Bearer sk-live-123'}",
            ["sk-live-123"],
            ["headers={'authorization': '[REDACTED]'}"],
        ),
        (
            "a bare Bearer eyJsecretToken",
            ["eyJsecretToken"],
            ["a bare Bearer [REDACTED]"],
        ),
        (
            "Invalid API key provided: sk-ant-secret",
            ["sk-ant-secret"],
            ["[REDACTED]"],
        ),
        (
            "status: ok, count: 5, user_id: 42",
            [],
            ["status: ok, count: 5, user_id: 42"],
        ),
    ],
)
def test_json_formatter_redacts_various_secret_shapes_and_preserves_plain_text(
    message: str, sensitive_snippets: list[str], preserved_snippets: list[str]
) -> None:
    formatter = JsonFormatter(service="api", environment="production")
    record = logging.LogRecord(
        "lumina.test",
        logging.INFO,
        __file__,
        1,
        message,
        (),
        None,
    )
    rendered = formatter.format(record)
    payload = json.loads(rendered)
    for sensitive in sensitive_snippets:
        assert sensitive not in payload["message"]
        assert sensitive not in rendered
    for preserved in preserved_snippets:
        assert preserved in payload["message"]


def _record_with_exception(exc: BaseException, level: int = logging.ERROR, **extra):
    record = logging.LogRecord(
        "lumina.test",
        level,
        __file__,
        1,
        "generation failed",
        (),
        (type(exc), exc, exc.__traceback__),
    )
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def _raise_here(canary: str) -> None:
    raise ValueError(canary)


def _formatted(record) -> dict:
    formatter = JsonFormatter(service="api", environment="production")
    return json.loads(formatter.format(record))


def test_stack_frames_are_project_relative_and_carry_no_message() -> None:
    """SCRUM-206: an ERROR names where it came from without quoting the failure."""
    try:
        _raise_here("SECRET-CANARY")
    except ValueError as exc:
        payload = _formatted(_record_with_exception(exc))

    frames = payload["stack"]
    assert frames
    assert all(re.fullmatch(r"[\w./<>-]+:\d+ in \S+", frame) for frame in frames)
    assert any(frame.startswith("tests/test_observability.py:") for frame in frames)
    assert "SECRET-CANARY" not in json.dumps(payload)
    assert "Traceback" not in json.dumps(payload)


def test_stack_frames_never_carry_an_absolute_path() -> None:
    """SCRUM-206: a home directory or image layout must not reach a log line."""
    try:
        json.loads("{definitely not json")
    except ValueError as exc:
        payload = _formatted(_record_with_exception(exc))

    for frame in payload["stack"]:
        assert not frame.startswith("/")
        assert ":\\" not in frame
        assert "Users" not in frame


def test_a_warning_with_an_exception_carries_no_stack() -> None:
    """SCRUM-206: frames are for ERROR and above, not for expected refusals."""
    try:
        _raise_here("nope")
    except ValueError as exc:
        payload = _formatted(_record_with_exception(exc, level=logging.WARNING))

    assert "stack" not in payload
    assert payload["exception_type"] == "ValueError"


def test_the_stack_survives_an_explicit_exception_type_extra() -> None:
    """SCRUM-206: main.py sets exception_type itself; the frames must still appear.

    Computing the stack inside the ``exception_type not in payload`` branch
    would drop it for the one middleware that catches every unhandled 5xx.
    """
    try:
        _raise_here("boom")
    except ValueError as exc:
        payload = _formatted(
            _record_with_exception(exc, exception_type="ValueError", http_status=500)
        )

    assert payload["exception_type"] == "ValueError"
    assert payload["stack"]


def test_the_stack_is_taken_from_the_innermost_cause() -> None:
    """SCRUM-206: a wrapper raised `from` its cause explains nothing on its own."""
    try:
        try:
            _raise_here("inner")
        except ValueError as inner:
            raise RuntimeError("wrapped") from inner
    except RuntimeError as outer:
        payload = _formatted(_record_with_exception(outer))

    assert any("in _raise_here" in frame for frame in payload["stack"])


def test_the_stack_is_capped() -> None:
    """SCRUM-206: a deep recursion must not turn one log line into a kilobyte."""

    def recurse(depth: int) -> None:
        if depth == 0:
            raise ValueError("deep")
        recurse(depth - 1)

    try:
        recurse(40)
    except ValueError as exc:
        payload = _formatted(_record_with_exception(exc))

    assert len(payload["stack"]) == 12


def _records(caplog) -> list[logging.LogRecord]:
    return [record for record in caplog.records if getattr(record, "event", "")]


def _http_records(caplog) -> list[logging.LogRecord]:
    return [
        record
        for record in caplog.records
        if getattr(record, "event", "").startswith("http_")
    ]


def test_a_successful_read_is_not_logged_but_is_still_correlated(
    api_context, caplog
) -> None:
    with caplog.at_level(logging.INFO):
        response = api_context.client.get(
            "/health/live", headers={"X-Request-ID": "quiet-1"}
        )

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "quiet-1"
    assert _http_records(caplog) == []


def test_health_probe_is_logged_when_it_fails(api_context, caplog, monkeypatch) -> None:
    def unready(*_args, **_kwargs):
        raise ReadinessError("storage is unavailable")

    monkeypatch.setattr("main.check_readiness", unready)

    with caplog.at_level(logging.INFO):
        response = api_context.client.get("/health/ready")

    assert response.status_code == 503
    records = _http_records(caplog)
    assert [record.event for record in records] == ["http_request_failed"]
    assert records[0].http_path == "/health/ready"


def test_a_successful_mutation_is_logged_with_its_actor(upload_api, caplog) -> None:
    with caplog.at_level(logging.INFO):
        response = upload_api.client.post(
            f"/api/courses/{upload_api.course_id}/documents",
            headers=upload_api.authorization,
            files={"document": ("notes.txt", b"Some notes", "text/plain")},
        )

    assert response.status_code == 201, response.text
    records = _http_records(caplog)
    assert [record.event for record in records] == ["http_request_completed"]
    record = records[0]
    assert record.http_method == "POST"
    assert record.http_path == "/api/courses/{course_id}/documents"
    assert record.user_id == upload_api.user_id
    assert record.auth_state == "authenticated"
    assert record.course_id == upload_api.course_id
    assert isinstance(record.course_id, int)


def test_a_delete_names_the_document_it_acted_on(upload_api, caplog) -> None:
    uploaded = upload_api.client.post(
        f"/api/courses/{upload_api.course_id}/documents",
        headers=upload_api.authorization,
        files={"document": ("gone.txt", b"Disposable", "text/plain")},
    )
    assert uploaded.status_code == 201, uploaded.text
    document_id = uploaded.json()["document"]["id"]

    caplog.clear()
    with caplog.at_level(logging.INFO):
        response = upload_api.client.delete(
            f"/api/courses/{upload_api.course_id}/documents/{document_id}?force=true",
            headers=upload_api.authorization,
        )

    assert response.status_code == 204, response.text
    records = _http_records(caplog)
    assert len(records) == 1
    assert records[0].document_id == document_id
    assert records[0].course_id == upload_api.course_id


def test_an_unauthenticated_request_names_why_it_was_denied(
    api_context, caplog
) -> None:
    with caplog.at_level(logging.INFO):
        response = api_context.client.get("/api/courses")

    assert response.status_code == 401
    records = _http_records(caplog)
    assert [record.event for record in records] == ["http_authorization_denied"]
    assert records[0].error_code == "unauthenticated"
    assert records[0].auth_state == "anonymous"


def test_a_rejected_token_is_told_apart_from_a_missing_one(api_context, caplog) -> None:
    with caplog.at_level(logging.INFO):
        response = api_context.client.get(
            "/api/courses", headers={"Authorization": "Bearer not-a-real-token"}
        )

    assert response.status_code == 401
    records = _http_records(caplog)
    assert [record.event for record in records] == ["http_authorization_denied"]
    assert records[0].error_code == "invalid_credentials"
    assert records[0].auth_state == "rejected"


def test_an_admin_denial_is_told_apart_from_a_ban(authz_api, caplog) -> None:
    with caplog.at_level(logging.INFO):
        response = authz_api.client.get(
            "/api/admin/users", headers=authz_api.authorization_a
        )

    assert response.status_code == 403
    records = _http_records(caplog)
    assert [record.event for record in records] == ["http_authorization_denied"]
    assert records[0].error_code == "admin_required"
    assert records[0].auth_state == "authenticated"
    assert records[0].user_id == authz_api.user_a_id


def test_an_unknown_api_path_is_told_apart_from_a_missing_record(
    api_context, caplog
) -> None:
    with caplog.at_level(logging.INFO):
        unknown = api_context.client.get("/api/does-not-exist")

    assert unknown.status_code == 404
    records = _http_records(caplog)
    assert [record.event for record in records] == ["http_not_found"]
    assert records[0].http_path == "/api/unmatched"
    assert records[0].error_code == "not_found"


def test_a_missing_course_names_the_course_not_found_code(
    upload_api, caplog
) -> None:
    with caplog.at_level(logging.INFO):
        response = upload_api.client.get(
            "/api/courses/98765/documents", headers=upload_api.authorization
        )

    assert response.status_code == 404
    records = _http_records(caplog)
    assert [record.event for record in records] == ["http_not_found"]
    assert records[0].error_code == "course_not_found"
    assert records[0].http_path == "/api/courses/{course_id}/documents"


def test_a_slow_read_is_logged_even_though_reads_are_not(
    upload_api, caplog, monkeypatch
) -> None:
    monkeypatch.setattr("main.SLOW_REQUEST_THRESHOLD_MS", 0.0)

    with caplog.at_level(logging.INFO):
        response = upload_api.client.get(
            f"/api/courses/{upload_api.course_id}/documents",
            headers=upload_api.authorization,
        )

    assert response.status_code == 200
    assert [record.event for record in _http_records(caplog)] == ["http_request_slow"]


def test_a_query_string_never_reaches_a_request_record(upload_api, caplog) -> None:
    with caplog.at_level(logging.INFO):
        response = upload_api.client.get(
            f"/api/courses/{upload_api.course_id}/documents?token=secret-value",
            headers=upload_api.authorization,
        )

    assert response.status_code == 200
    rendered = json.dumps(
        [
            {
                key: str(value)
                for key, value in record.__dict__.items()
                if not key.startswith("_")
            }
            for record in _records(caplog)
        ]
    )
    assert "secret-value" not in rendered
    for record in _http_records(caplog):
        assert "?" not in record.http_path
