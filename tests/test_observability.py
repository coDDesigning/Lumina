import json
import logging

from backend.app.observability import (
    JsonFormatter,
    emit_emf_metrics,
    normalize_request_id,
)


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
    response = api_context.client.get("/", headers={"X-Request-ID": "client-42"})

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
