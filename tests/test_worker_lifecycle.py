import json
import logging
import multiprocessing
import os
import signal
import threading
import time
from dataclasses import replace
from io import BytesIO
from uuid import uuid4

import pytest
from sqlalchemy.exc import OperationalError

from backend.app.config import WORKER_SHUTDOWN_MODE_ABORT, WORKER_SHUTDOWN_MODE_DRAIN
from services.processing_jobs import ClaimedJob
from workers import document_processor, generation_processor, shutdown, worker


@pytest.fixture(autouse=True)
def no_jobs_in_flight():
    for job in shutdown.in_flight():
        shutdown.unregister(job)
    yield
    for job in shutdown.in_flight():
        shutdown.unregister(job)


class _EventWatcher(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.INFO)
        self.records: list[logging.LogRecord] = []
        self._changed = threading.Condition()

    def emit(self, record: logging.LogRecord) -> None:
        with self._changed:
            self.records.append(record)
            self._changed.notify_all()

    def events(self, name: str) -> list[logging.LogRecord]:
        with self._changed:
            return [
                record
                for record in self.records
                if getattr(record, "event", None) == name
            ]

    def wait_for(self, name: str, count: int = 1, timeout: float = 5.0) -> bool:
        with self._changed:
            return self._changed.wait_for(
                lambda: (
                    sum(
                        1
                        for record in self.records
                        if getattr(record, "event", None) == name
                    )
                    >= count
                ),
                timeout,
            )


@pytest.fixture
def worker_events(caplog):
    caplog.set_level(logging.INFO)
    watcher = _EventWatcher()
    workers_logger = logging.getLogger("workers")
    workers_logger.addHandler(watcher)
    try:
        yield watcher
    finally:
        workers_logger.removeHandler(watcher)


def _never_release(_session, job_id, _claim_token):
    pytest.fail(f"job {job_id} must not be released")


def _in_flight_job(
    *,
    job_id: int,
    releaser=_never_release,
    kind: str = shutdown.KIND_COURSE_DOCUMENT,
    job_type: str = "course_document_processing",
    seconds_left: float = 120.0,
    course_id: int | None = 42,
    user_id: int | None = 7,
    document_id: str | None = "5f0c9a52-0d7e-4f5e-9a51-3c2b1d0e9f11",
) -> shutdown.InFlightJob:
    return shutdown.InFlightJob(
        kind=kind,
        job_id=job_id,
        job_type=job_type,
        claim_token=f"claim-{job_id}",
        attempt_number=1,
        worker_id="documents:slot-0",
        deadline=time.monotonic() + seconds_left,
        releaser=releaser,
        course_id=course_id,
        user_id=user_id,
        document_id=document_id,
    )


def _isolate_the_combined_worker(monkeypatch, *, mode: str) -> None:
    monkeypatch.setattr(document_processor, "check_worker_ready", lambda **_k: None)
    monkeypatch.setattr(generation_processor, "check_worker_ready", lambda **_k: None)
    monkeypatch.setattr(document_processor, "get_storage", ReadyStorage)
    monkeypatch.setattr(generation_processor, "get_storage", ReadyStorage)
    monkeypatch.setattr(
        document_processor,
        "settings",
        replace(
            document_processor.settings,
            worker_shutdown_mode=mode,
            course_purge_interval_seconds=0.0,
            embedding_backfill_interval_seconds=0.0,
            ai_usage_cleanup_interval_seconds=0.0,
            visual_description_sweep_interval_seconds=0.0,
        ),
    )
    monkeypatch.setattr(
        worker, "settings", replace(worker.settings, worker_shutdown_mode=mode)
    )


class FakeSession:
    def __enter__(self):
        return self

    def __exit__(self, _exception_type, _exception, _traceback):
        return False


def fake_session_factory() -> FakeSession:
    return FakeSession()


class ReadyStorage:
    provider = "ready-test"

    def check_ready(self) -> None:
        pass


class SignalingSlowStorage:
    provider = "signal-test"

    def __init__(self, ready) -> None:
        self.ready = ready

    def open(self, _key: str):
        self.ready.set()
        time.sleep(5)
        return BytesIO(b"eventual content")


def test_worker_drains_active_job_and_does_not_claim_another(
    monkeypatch,
    caplog,
) -> None:
    stop = threading.Event()
    job_started = threading.Event()
    allow_completion = threading.Event()
    worker_ids: list[str] = []
    session_factories = []
    completed_waits: list[bool] = []
    storage_instance = ReadyStorage()

    monkeypatch.setattr(document_processor, "check_worker_ready", lambda **_k: None)
    monkeypatch.setattr(document_processor, "recover_expired_jobs", lambda *_a, **_k: 0)

    def process_job(
        *, session_factory, storage, worker_id, shutdown_requested, claim_describe=False
    ):
        session_factories.append(session_factory)
        assert storage is storage_instance
        worker_ids.append(worker_id)
        job_started.set()
        completed_waits.append(allow_completion.wait(timeout=2))
        return True

    monkeypatch.setattr(document_processor, "process_next_job", process_job)
    caplog.set_level(logging.INFO)
    worker = threading.Thread(
        target=document_processor.run_worker,
        kwargs={
            "once": False,
            "worker_id": "deploy-worker",
            "stop_event": stop,
            "session_factory": fake_session_factory,
            "storage": storage_instance,
        },
        daemon=True,
    )
    worker.start()
    try:
        assert job_started.wait(timeout=2)
        stop.set()
        assert worker.is_alive()
    finally:
        stop.set()
        allow_completion.set()
        worker.join(timeout=2)

    assert not worker.is_alive()
    assert worker_ids == ["deploy-worker"]
    assert session_factories == [fake_session_factory]
    assert completed_waits == [True]
    assert "Document worker deploy-worker started" in caplog.messages
    assert (
        "Shutdown requested; document worker deploy-worker will not claim another job"
        in caplog.messages
    )
    assert "Document worker deploy-worker stopped" in caplog.messages


def test_shutdown_requested_during_recovery_prevents_claim(monkeypatch) -> None:
    stop = threading.Event()
    claims = 0

    def recover(_session, *, limit):
        assert limit == document_processor.RECOVERY_BATCH_SIZE
        stop.set()
        return document_processor.RECOVERY_BATCH_SIZE

    def process_job(**_kwargs):
        nonlocal claims
        claims += 1
        return False

    monkeypatch.setattr(document_processor, "check_worker_ready", lambda **_k: None)
    monkeypatch.setattr(document_processor, "recover_expired_jobs", recover)
    monkeypatch.setattr(document_processor, "process_next_job", process_job)

    document_processor.run_worker(
        once=False,
        stop_event=stop,
        session_factory=fake_session_factory,
        storage=ReadyStorage(),
    )

    assert claims == 0


def test_worker_uses_one_generated_identity_for_all_claims(monkeypatch) -> None:
    stop = threading.Event()
    worker_ids: list[str] = []

    storage_instance = ReadyStorage()
    monkeypatch.setattr(document_processor, "check_worker_ready", lambda **_k: None)
    monkeypatch.setattr(document_processor, "_default_worker_id", lambda: "stable-id")
    monkeypatch.setattr(document_processor, "recover_expired_jobs", lambda *_a, **_k: 0)

    def process_job(
        *, session_factory, storage, worker_id, shutdown_requested, claim_describe=False
    ):
        assert session_factory is fake_session_factory
        assert storage is storage_instance
        assert shutdown_requested() is False
        worker_ids.append(worker_id)
        if len(worker_ids) == 2:
            stop.set()
        return True

    monkeypatch.setattr(document_processor, "process_next_job", process_job)

    document_processor.run_worker(
        once=False,
        stop_event=stop,
        session_factory=fake_session_factory,
        storage=storage_instance,
    )

    assert worker_ids == ["stable-id", "stable-id"]


def test_worker_checks_readiness_before_recovery_and_claim(monkeypatch) -> None:
    events: list[str] = []
    storage_instance = ReadyStorage()

    def check_ready(*, session_factory, storage):
        assert session_factory is fake_session_factory
        assert storage is storage_instance
        events.append("ready")

    def recover(_session, *, limit):
        assert limit == document_processor.RECOVERY_BATCH_SIZE
        events.append("recover")
        return 0

    def process_job(**kwargs):
        assert kwargs["storage"] is storage_instance
        events.append("claim")
        return False

    monkeypatch.setattr(document_processor, "check_worker_ready", check_ready)
    monkeypatch.setattr(document_processor, "recover_expired_jobs", recover)
    monkeypatch.setattr(document_processor, "process_next_job", process_job)

    document_processor.run_worker(
        once=True,
        session_factory=fake_session_factory,
        storage=storage_instance,
    )

    assert events == ["ready", "recover", "claim"]


def test_worker_readiness_failure_prevents_recovery_and_claim(monkeypatch) -> None:
    def fail_readiness(**_kwargs):
        raise document_processor.ReadinessError("database is unavailable")

    monkeypatch.setattr(document_processor, "check_worker_ready", fail_readiness)
    monkeypatch.setattr(
        document_processor,
        "recover_expired_jobs",
        lambda *_args, **_kwargs: pytest.fail("recovery should not run"),
    )
    monkeypatch.setattr(
        document_processor,
        "process_next_job",
        lambda **_kwargs: pytest.fail("claim should not run"),
    )

    with pytest.raises(document_processor.ReadinessError):
        document_processor.run_worker(
            once=True,
            session_factory=fake_session_factory,
            storage=ReadyStorage(),
        )


def test_requested_shutdown_skips_startup_readiness(monkeypatch) -> None:
    stop = threading.Event()
    stop.set()
    monkeypatch.setattr(
        document_processor,
        "check_worker_ready",
        lambda **_kwargs: pytest.fail("readiness should not run"),
    )

    document_processor.run_worker(
        once=False,
        stop_event=stop,
        session_factory=fake_session_factory,
        storage=ReadyStorage(),
    )


def test_worker_check_cli_does_not_install_handlers_or_run_worker(monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        document_processor,
        "check_worker_ready",
        lambda: calls.append("check"),
    )
    monkeypatch.setattr(
        document_processor,
        "_install_shutdown_handlers",
        lambda _stop: pytest.fail("handlers should not be installed"),
    )
    monkeypatch.setattr(
        document_processor,
        "run_worker",
        lambda **_kwargs: pytest.fail("worker should not run"),
    )

    document_processor.main(["--check"])

    assert calls == ["check"]


def test_worker_check_cli_exits_nonzero_when_not_ready(monkeypatch) -> None:
    def fail_readiness() -> None:
        raise document_processor.ReadinessError("database is unavailable")

    monkeypatch.setattr(document_processor, "check_worker_ready", fail_readiness)

    with pytest.raises(SystemExit) as exc_info:
        document_processor.main(["--check"])

    assert exc_info.value.code == 1


def test_signal_handlers_request_graceful_shutdown(monkeypatch) -> None:
    handlers = {}
    stop = document_processor._SignalStopEvent()

    def capture_handler(signal_number, handler):
        handlers[signal_number] = handler

    monkeypatch.setattr(signal, "signal", capture_handler)

    document_processor._install_shutdown_handlers(stop)
    handlers[signal.SIGTERM](signal.SIGTERM, None)

    assert stop.is_set()
    assert signal.SIGINT in handlers
    assert signal.SIGTERM in handlers


def test_process_next_job_checks_shutdown_before_opening_session() -> None:
    sessions_opened = 0

    def session_factory():
        nonlocal sessions_opened
        sessions_opened += 1
        return FakeSession()

    assert not document_processor.process_next_job(
        session_factory=session_factory,
        shutdown_requested=lambda: True,
    )
    assert sessions_opened == 0


@pytest.mark.skipif(os.name == "nt", reason="POSIX process-group signal behavior")
def test_extraction_child_ignores_worker_sigterm() -> None:
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    parent_connection, child_connection = context.Pipe(duplex=False)
    job = ClaimedJob(
        id=1,
        document_id=uuid4(),
        course_id=1,
        claim_token=str(uuid4()),
        attempt_count=1,
        max_attempts=3,
        storage_provider=SignalingSlowStorage.provider,
        storage_key="document.txt",
        file_hash="0" * 64,
        file_type="txt",
        file_size=16,
    )
    process = context.Process(
        target=document_processor._extraction_process,
        args=(child_connection, SignalingSlowStorage(ready), job),
    )
    document_processor._start_extraction_process(process)
    child_connection.close()
    try:
        os.kill(process.pid, signal.SIGTERM)
        assert ready.wait(timeout=5)
        time.sleep(0.2)
        assert process.is_alive()
    finally:
        process.kill()
        process.join(timeout=5)
        parent_connection.close()


def test_worker_runs_periodic_purge_and_backfill(monkeypatch) -> None:
    stop = threading.Event()
    purge_calls = 0
    backfill_calls = 0

    def mock_purge(**kwargs):
        nonlocal purge_calls
        purge_calls += 1

    def mock_backfill(**kwargs):
        nonlocal backfill_calls
        backfill_calls += 1

    def mock_process(**kwargs):
        stop.set()
        return False

    monkeypatch.setattr(document_processor, "check_worker_ready", lambda **k: None)
    monkeypatch.setattr(document_processor, "recover_expired_jobs", lambda *a, **k: 0)
    monkeypatch.setattr(document_processor, "run_purge", mock_purge)
    monkeypatch.setattr(document_processor, "run_backfill", mock_backfill)
    monkeypatch.setattr(document_processor, "run_ai_usage_cleanup", lambda **k: None)
    monkeypatch.setattr(document_processor, "process_next_job", mock_process)

    document_processor.run_worker(
        once=False,
        stop_event=stop,
        session_factory=fake_session_factory,
        storage=ReadyStorage(),
    )

    assert purge_calls == 1
    assert backfill_calls == 1


def test_worker_runs_periodic_ai_usage_retention_cleanup(monkeypatch) -> None:
    """P2-024: the retention job must actually be invoked by a running worker,
    not left as an unwired module with an inert config knob."""
    stop = threading.Event()
    cleanup_calls: list[dict] = []

    def mock_cleanup(**kwargs):
        cleanup_calls.append(kwargs)

    def mock_process(**kwargs):
        stop.set()
        return False

    monkeypatch.setattr(document_processor, "check_worker_ready", lambda **k: None)
    monkeypatch.setattr(document_processor, "recover_expired_jobs", lambda *a, **k: 0)
    monkeypatch.setattr(document_processor, "run_purge", lambda **k: None)
    monkeypatch.setattr(document_processor, "run_backfill", lambda **k: None)
    monkeypatch.setattr(document_processor, "run_ai_usage_cleanup", mock_cleanup)
    monkeypatch.setattr(document_processor, "process_next_job", mock_process)

    document_processor.run_worker(
        once=False,
        stop_event=stop,
        session_factory=fake_session_factory,
        storage=ReadyStorage(),
    )

    assert len(cleanup_calls) == 1
    assert "session_factory" in cleanup_calls[0]
    assert cleanup_calls[0]["stop_event"] is stop


def test_worker_maintenance_failure_does_not_crash_loop(monkeypatch) -> None:
    stop = threading.Event()

    def fail_purge(**kwargs):
        raise RuntimeError("purge error")

    def fail_backfill(**kwargs):
        raise RuntimeError("backfill error")

    def fail_cleanup(**kwargs):
        raise RuntimeError("cleanup error")

    def mock_process(**kwargs):
        stop.set()
        return True

    monkeypatch.setattr(document_processor, "check_worker_ready", lambda **k: None)
    monkeypatch.setattr(document_processor, "recover_expired_jobs", lambda *a, **k: 0)
    monkeypatch.setattr(document_processor, "run_purge", fail_purge)
    monkeypatch.setattr(document_processor, "run_backfill", fail_backfill)
    monkeypatch.setattr(document_processor, "run_ai_usage_cleanup", fail_cleanup)
    monkeypatch.setattr(document_processor, "process_next_job", mock_process)

    document_processor.run_worker(
        once=False,
        stop_event=stop,
        session_factory=fake_session_factory,
        storage=ReadyStorage(),
    )

    assert stop.is_set()


def test_worker_skips_maintenance_when_intervals_are_zero(monkeypatch) -> None:
    stop = threading.Event()
    purge_calls = 0
    backfill_calls = 0
    cleanup_calls = 0

    def mock_purge(**kwargs):
        nonlocal purge_calls
        purge_calls += 1

    def mock_backfill(**kwargs):
        nonlocal backfill_calls
        backfill_calls += 1

    def mock_cleanup(**kwargs):
        nonlocal cleanup_calls
        cleanup_calls += 1

    def mock_process(**kwargs):
        stop.set()
        return False

    import dataclasses

    custom_settings = dataclasses.replace(
        document_processor.settings,
        course_purge_interval_seconds=0.0,
        embedding_backfill_interval_seconds=0.0,
        ai_usage_cleanup_interval_seconds=0.0,
    )
    monkeypatch.setattr(document_processor, "settings", custom_settings)
    monkeypatch.setattr(document_processor, "check_worker_ready", lambda **k: None)
    monkeypatch.setattr(document_processor, "recover_expired_jobs", lambda *a, **k: 0)
    monkeypatch.setattr(document_processor, "run_purge", mock_purge)
    monkeypatch.setattr(document_processor, "run_backfill", mock_backfill)
    monkeypatch.setattr(document_processor, "run_ai_usage_cleanup", mock_cleanup)
    monkeypatch.setattr(document_processor, "process_next_job", mock_process)

    document_processor.run_worker(
        once=False,
        stop_event=stop,
        session_factory=fake_session_factory,
        storage=ReadyStorage(),
    )

    assert purge_calls == 0
    assert backfill_calls == 0
    assert cleanup_calls == 0


LEAK_STORAGE_KEY = "courses/42/uploads/2f9c-lecture-08-final.pdf"


def _format_record(record: logging.LogRecord) -> dict:
    """Render a record the way the deployed formatter does.

    Asserting on raw LogRecord attributes proves nothing about what an operator
    can see: JsonFormatter drops every ``extra`` key outside its allowlist, so a
    field can be set on the record and still never reach CloudWatch.
    """
    from backend.app.observability import JsonFormatter

    formatter = JsonFormatter(service="worker", environment="test")
    return json.loads(formatter.format(record))


def test_record_failure_emits_permanent_failure_alert(monkeypatch) -> None:
    """The alert an operator actually receives carries its scope and runbook.

    The leak guard names a value the job genuinely holds. An earlier version
    asserted a filename was absent, but ClaimedJob has no filename field, so the
    assertion could never fail no matter what the worker logged. The storage key
    is the real canary: it is a path into object storage. The document id is not
    a leak, it is the identifier the runbook is followed with.
    """
    from workers.document_processor import DocumentProcessingError

    records: list[logging.LogRecord] = []

    class CapturingHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    logger = logging.getLogger("workers.document_processor")
    handler = CapturingHandler()
    logger.addHandler(handler)

    try:
        monkeypatch.setattr(
            document_processor,
            "fail_job",
            lambda *a, **k: "failed",
        )

        document_id = uuid4()
        job = ClaimedJob(
            id=101,
            document_id=document_id,
            course_id=42,
            claim_token="claim-tok-123",
            attempt_count=3,
            max_attempts=3,
            storage_provider="local:test",
            storage_key=LEAK_STORAGE_KEY,
            file_hash="a" * 64,
            file_type="pdf",
            file_size=1024,
            correlation_id="corr-test-123",
        )

        err = DocumentProcessingError(
            "CORRUPT_PDF",
            "PDF content is unreadable.",
            retryable=False,
            failed_stage="extracting_text",
        )

        res = document_processor._record_failure(fake_session_factory, job, err)
        assert res == "failed"

        alert_logs = [
            r
            for r in records
            if getattr(r, "event", None) == "permanent_document_failure"
        ]
        assert len(alert_logs) == 1
        emitted = _format_record(alert_logs[0])

        assert emitted["event"] == "permanent_document_failure"
        assert emitted["job_id"] == 101
        assert emitted["course_id"] == 42
        assert emitted["failed_stage"] == "extracting_text"
        assert emitted["error_code"] == "CORRUPT_PDF"
        assert emitted["runbook"] == "docs/runbooks/stuck_document.md"

        assert emitted["document_id"] == str(document_id)

        assert LEAK_STORAGE_KEY not in json.dumps(emitted)
    finally:
        logger.removeHandler(handler)


def test_process_next_job_emits_stage_failure_metrics(monkeypatch) -> None:
    """StageFailed is emitted where it is produced, not where the alert is.

    _record_failure emits no EMF at all; the stage counters come from
    process_next_job, so a test that only calls _record_failure can never
    observe them.
    """
    from workers.document_processor import DocumentProcessingError

    emf_records: list[logging.LogRecord] = []

    class MetricsHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            emf_records.append(record)

    class GettableSession(FakeSession):
        def get(self, *_args, **_kwargs):
            return None

    def session_factory() -> GettableSession:
        return GettableSession()

    job = ClaimedJob(
        id=202,
        document_id=uuid4(),
        course_id=42,
        claim_token="claim-tok-202",
        attempt_count=3,
        max_attempts=3,
        storage_provider="ready-test",
        storage_key=LEAK_STORAGE_KEY,
        file_hash="b" * 64,
        file_type="pdf",
        file_size=2048,
        correlation_id="corr-test-202",
    )

    metrics_logger = logging.getLogger("lumina.metrics")
    metrics_handler = MetricsHandler()
    previous_level = metrics_logger.level
    metrics_logger.addHandler(metrics_handler)
    metrics_logger.setLevel(logging.INFO)

    try:
        monkeypatch.setattr(document_processor, "claim_next_job", lambda *a, **k: job)
        monkeypatch.setattr(
            document_processor, "resolve_prompt_context", lambda *a, **k: None
        )
        monkeypatch.setattr(document_processor, "_heartbeat_loop", lambda *a, **k: None)
        monkeypatch.setattr(document_processor, "fail_job", lambda *a, **k: "failed")

        def raise_corrupt(*_args, **_kwargs):
            raise DocumentProcessingError(
                "CORRUPT_PDF",
                "PDF content is unreadable.",
                retryable=False,
                failed_stage="extracting_text",
            )

        monkeypatch.setattr(document_processor, "_extract_with_timeout", raise_corrupt)

        handled = document_processor.process_next_job(
            session_factory=session_factory,
            storage=ReadyStorage(),
            worker_id="worker-test",
            lease_seconds=60,
        )
        assert handled is True
    finally:
        metrics_logger.removeHandler(metrics_handler)
        metrics_logger.setLevel(previous_level)

    emitted = [getattr(r, "emf", {}) for r in emf_records]
    assert any("StageFailed" in payload for payload in emitted), emitted
    assert any("JobsFailed" in payload for payload in emitted), emitted

    stage_payload = next(p for p in emitted if "StageFailed" in p)
    assert stage_payload["StageFailed"] == 1
    assert stage_payload["Stage"] == "extracting_text"
    assert not any("StageRetried" in payload for payload in emitted)


def test_worker_runs_jobs_concurrently_across_slots(monkeypatch) -> None:
    stop = threading.Event()
    barrier = threading.Barrier(2, timeout=5)
    lock = threading.Lock()
    worker_ids: list[str] = []

    def process_job(
        *, session_factory, storage, worker_id, shutdown_requested, claim_describe=False
    ):
        with lock:
            worker_ids.append(worker_id)
        barrier.wait()
        stop.set()
        return True

    monkeypatch.setattr(document_processor, "check_worker_ready", lambda **k: None)
    monkeypatch.setattr(document_processor, "_default_worker_id", lambda: "stable-id")
    monkeypatch.setattr(document_processor, "recover_expired_jobs", lambda *a, **k: 0)
    monkeypatch.setattr(document_processor, "run_purge", lambda **k: None)
    monkeypatch.setattr(document_processor, "run_backfill", lambda **k: None)
    monkeypatch.setattr(document_processor, "run_ai_usage_cleanup", lambda **k: None)
    monkeypatch.setattr(document_processor, "process_next_job", process_job)

    worker = threading.Thread(
        target=document_processor.run_worker,
        kwargs={
            "once": False,
            "stop_event": stop,
            "session_factory": fake_session_factory,
            "storage": ReadyStorage(),
            "concurrency": 2,
        },
    )
    worker.start()
    worker.join(timeout=10)

    assert not worker.is_alive()
    assert sorted(set(worker_ids)) == ["stable-id:slot-0", "stable-id:slot-1"]


def test_worker_slots_share_one_maintenance_cycle(monkeypatch) -> None:
    stop = threading.Event()
    barrier = threading.Barrier(3, timeout=5)
    purge_done = threading.Event()
    purge_calls = 0

    def mock_purge(**kwargs):
        nonlocal purge_calls
        purge_calls += 1
        purge_done.set()

    def process_job(**kwargs):
        barrier.wait()
        purge_done.wait(timeout=5)
        stop.set()
        return True

    monkeypatch.setattr(document_processor, "check_worker_ready", lambda **k: None)
    monkeypatch.setattr(document_processor, "_default_worker_id", lambda: "stable-id")
    monkeypatch.setattr(document_processor, "recover_expired_jobs", lambda *a, **k: 0)
    monkeypatch.setattr(document_processor, "run_purge", mock_purge)
    monkeypatch.setattr(document_processor, "run_backfill", lambda **k: None)
    monkeypatch.setattr(document_processor, "run_ai_usage_cleanup", lambda **k: None)
    monkeypatch.setattr(document_processor, "process_next_job", process_job)

    worker = threading.Thread(
        target=document_processor.run_worker,
        kwargs={
            "once": False,
            "stop_event": stop,
            "session_factory": fake_session_factory,
            "storage": ReadyStorage(),
            "concurrency": 3,
        },
    )
    worker.start()
    worker.join(timeout=10)

    assert not worker.is_alive()
    assert purge_calls == 1


def test_worker_slot_fatal_error_stops_every_slot_and_propagates(monkeypatch) -> None:
    stop = threading.Event()

    def process_job(**kwargs):
        raise document_processor.WorkerProcessFatalError("unreapable subprocess")

    monkeypatch.setattr(document_processor, "check_worker_ready", lambda **k: None)
    monkeypatch.setattr(document_processor, "_default_worker_id", lambda: "stable-id")
    monkeypatch.setattr(document_processor, "recover_expired_jobs", lambda *a, **k: 0)
    monkeypatch.setattr(document_processor, "run_purge", lambda **k: None)
    monkeypatch.setattr(document_processor, "run_backfill", lambda **k: None)
    monkeypatch.setattr(document_processor, "run_ai_usage_cleanup", lambda **k: None)
    monkeypatch.setattr(document_processor, "process_next_job", process_job)

    with pytest.raises(document_processor.WorkerProcessFatalError):
        document_processor.run_worker(
            once=False,
            stop_event=stop,
            session_factory=fake_session_factory,
            storage=ReadyStorage(),
            concurrency=2,
        )


def test_once_forces_a_single_slot_and_the_bare_identity(monkeypatch) -> None:
    stop = threading.Event()
    worker_ids: list[str] = []

    def process_job(
        *, session_factory, storage, worker_id, shutdown_requested, claim_describe=False
    ):
        worker_ids.append(worker_id)
        return True

    monkeypatch.setattr(document_processor, "check_worker_ready", lambda **k: None)
    monkeypatch.setattr(document_processor, "_default_worker_id", lambda: "stable-id")
    monkeypatch.setattr(document_processor, "recover_expired_jobs", lambda *a, **k: 0)
    monkeypatch.setattr(document_processor, "run_purge", lambda **k: None)
    monkeypatch.setattr(document_processor, "run_backfill", lambda **k: None)
    monkeypatch.setattr(document_processor, "run_ai_usage_cleanup", lambda **k: None)
    monkeypatch.setattr(document_processor, "process_next_job", process_job)

    document_processor.run_worker(
        once=True,
        stop_event=stop,
        session_factory=fake_session_factory,
        storage=ReadyStorage(),
        concurrency=4,
    )

    assert worker_ids == ["stable-id"]


def test_run_worker_rejects_non_positive_concurrency() -> None:
    with pytest.raises(ValueError):
        document_processor.run_worker(once=False, concurrency=0)


def test_supervision_logs_each_in_flight_job_once_with_the_time_its_attempt_has_left(
    worker_events,
) -> None:
    shutdown.register(_in_flight_job(job_id=11, seconds_left=120.0))
    shutdown.register(
        _in_flight_job(
            job_id=12,
            kind=shutdown.KIND_GENERATION,
            job_type="generate_quiz",
            seconds_left=300.0,
            document_id=None,
        )
    )
    late_job = _in_flight_job(
        job_id=13,
        kind=shutdown.KIND_PROFILE_DOCUMENT,
        job_type="profile_document_processing",
        seconds_left=60.0,
        course_id=None,
    )
    stop = threading.Event()
    stop.set()

    def keep_running_until_the_late_job_was_announced() -> None:
        worker_events.wait_for("worker_shutdown_waiting_for_job", count=2)
        shutdown.register(late_job)
        worker_events.wait_for("worker_shutdown_waiting_for_job", count=3)
        time.sleep(0.5)

    thread = threading.Thread(
        target=keep_running_until_the_late_job_was_announced, daemon=True
    )
    thread.start()

    abandoned = shutdown.supervise(
        [thread],
        stop,
        mode=WORKER_SHUTDOWN_MODE_DRAIN,
        session_factory=fake_session_factory,
    )

    assert abandoned is False
    announcements = worker_events.events("worker_shutdown_waiting_for_job")
    assert sorted(record.job_id for record in announcements) == [11, 12, 13]
    records = {record.job_id: record for record in announcements}
    payloads = {record.job_id: _format_record(record) for record in announcements}

    course_job = payloads[11]
    assert course_job["level"] == "INFO"
    assert course_job["job_type"] == "course_document_processing"
    assert course_job["operation_id"] == "processing_job:course:11"
    assert course_job["attempt_number"] == 1
    assert course_job["worker_id"] == "documents:slot-0"
    assert course_job["course_id"] == 42
    assert course_job["user_id"] == 7
    assert course_job["document_id"] == "5f0c9a52-0d7e-4f5e-9a51-3c2b1d0e9f11"
    assert course_job["job_status"] == "running"
    assert 118_000 < course_job["duration_ms"] <= 120_000
    assert course_job["message"] == (
        "Shutdown requested; waiting for course_document job 11 "
        "(course_document_processing) with "
        f"{round(course_job['duration_ms'] / 1000)}s left in its attempt"
    )

    generation_job = payloads[12]
    assert generation_job["job_type"] == "generate_quiz"
    assert generation_job["operation_id"] == "generation_job:generate_quiz:12"
    assert records[12].document_id is None
    assert 298_000 < generation_job["duration_ms"] <= 300_000

    profile_job = payloads[13]
    assert profile_job["operation_id"] == "processing_job:profile:13"
    assert records[13].course_id is None
    assert 58_000 < profile_job["duration_ms"] <= 60_000
    assert worker_events.events("worker_shutdown_aborting_job") == []


def test_abort_supervision_releases_jobs_still_running_at_its_deadline(
    monkeypatch, worker_events
) -> None:
    monkeypatch.setattr(shutdown, "ABORT_SHUTDOWN_SECONDS", 0.5)
    released: list[tuple[object, int, str]] = []

    def release(session, job_id, claim_token) -> bool:
        released.append((session, job_id, claim_token))
        return True

    shutdown.register(_in_flight_job(job_id=21, releaser=release))
    blocked = threading.Event()
    thread = threading.Thread(target=blocked.wait, args=(10,), daemon=True)
    thread.start()
    stop = threading.Event()
    stop.set()

    started = time.monotonic()
    try:
        abandoned = shutdown.supervise(
            [thread],
            stop,
            mode=WORKER_SHUTDOWN_MODE_ABORT,
            session_factory=fake_session_factory,
        )
        elapsed = time.monotonic() - started
        still_running = thread.is_alive()
    finally:
        blocked.set()
        thread.join(timeout=5)

    assert abandoned is True
    assert still_running
    assert 0.5 <= elapsed < 2.0
    assert [(job_id, token) for _session, job_id, token in released] == [
        (21, "claim-21")
    ]
    assert isinstance(released[0][0], FakeSession)
    aborting = worker_events.events("worker_shutdown_aborting_job")
    assert [record.job_id for record in aborting] == [21]
    assert _format_record(aborting[0])["level"] == "WARNING"
    assert (
        aborting[0]
        .getMessage()
        .startswith(
            "Shutdown requested; aborting course_document job 21 "
            "(course_document_processing) with "
        )
    )
    assert [
        record.job_id for record in worker_events.events("worker_shutdown_job_released")
    ] == [21]
    assert [
        record.item_count
        for record in worker_events.events("worker_shutdown_deadline_exceeded")
    ] == [1]


def test_drain_supervision_waits_for_every_thread_without_releasing(
    monkeypatch, worker_events
) -> None:
    monkeypatch.setattr(shutdown, "ABORT_SHUTDOWN_SECONDS", 0.2)
    shutdown.register(_in_flight_job(job_id=31))
    quick = threading.Event()
    slow = threading.Event()
    threads = [
        threading.Thread(target=quick.wait, args=(10,), daemon=True),
        threading.Thread(target=slow.wait, args=(10,), daemon=True),
    ]
    for thread in threads:
        thread.start()
    stop = threading.Event()
    stop.set()

    def finish_well_after_the_abort_deadline() -> None:
        worker_events.wait_for("worker_shutdown_waiting_for_job")
        quick.set()
        time.sleep(0.6)
        slow.set()

    finisher = threading.Thread(
        target=finish_well_after_the_abort_deadline, daemon=True
    )
    finisher.start()
    started = time.monotonic()

    abandoned = shutdown.supervise(
        threads,
        stop,
        mode=WORKER_SHUTDOWN_MODE_DRAIN,
        session_factory=fake_session_factory,
    )

    elapsed = time.monotonic() - started
    finisher.join(timeout=5)
    assert abandoned is False
    assert elapsed > 2 * shutdown.ABORT_SHUTDOWN_SECONDS
    assert not any(thread.is_alive() for thread in threads)
    assert [
        record.job_id
        for record in worker_events.events("worker_shutdown_waiting_for_job")
    ] == [31]
    assert worker_events.events("worker_shutdown_job_released") == []
    assert worker_events.events("worker_shutdown_deadline_exceeded") == []


def test_release_in_flight_survives_a_release_that_raises(worker_events) -> None:
    released: list[int] = []

    def locked_out(_session, _job_id, _claim_token) -> bool:
        raise OperationalError(
            "UPDATE processing_jobs", {}, Exception("database is locked")
        )

    def release(_session, job_id, _claim_token) -> bool:
        released.append(job_id)
        return True

    def already_finished(_session, _job_id, _claim_token) -> bool:
        return False

    shutdown.register(_in_flight_job(job_id=41, releaser=locked_out))
    shutdown.register(_in_flight_job(job_id=42, releaser=release))
    shutdown.register(_in_flight_job(job_id=43, releaser=already_finished))

    assert shutdown.release_in_flight(fake_session_factory) == 1

    assert released == [42]
    failures = worker_events.events("worker_shutdown_release_failed")
    assert [record.job_id for record in failures] == [41]
    assert failures[0].exc_info[0] is OperationalError
    assert [
        record.job_id for record in worker_events.events("worker_shutdown_job_released")
    ] == [42]
    assert [
        record.job_id
        for record in worker_events.events("worker_shutdown_job_not_released")
    ] == [43]


@pytest.mark.parametrize(
    "mode", [WORKER_SHUTDOWN_MODE_ABORT, WORKER_SHUTDOWN_MODE_DRAIN]
)
def test_an_idle_combined_worker_stops_within_two_seconds(
    mode, session_factory, monkeypatch, worker_events
) -> None:
    _isolate_the_combined_worker(monkeypatch, mode=mode)
    documents_polled = threading.Event()
    generations_polled = threading.Event()
    poll_documents = document_processor.process_next_job
    poll_generations = generation_processor.process_next_generation_job

    def poll_the_document_queue(**kwargs) -> bool:
        claimed = poll_documents(**kwargs)
        documents_polled.set()
        return claimed

    def poll_the_generation_queue(**kwargs) -> bool:
        claimed = poll_generations(**kwargs)
        generations_polled.set()
        return claimed

    monkeypatch.setattr(document_processor, "process_next_job", poll_the_document_queue)
    monkeypatch.setattr(
        generation_processor, "process_next_generation_job", poll_the_generation_queue
    )
    stop = threading.Event()
    stop_requested_at: list[float] = []

    def stop_once_both_queues_were_polled() -> None:
        documents_polled.wait(timeout=10)
        generations_polled.wait(timeout=10)
        stop_requested_at.append(time.monotonic())
        stop.set()

    trigger = threading.Thread(target=stop_once_both_queues_were_polled, daemon=True)
    trigger.start()

    abandoned = worker.run_worker(stop_event=stop, session_factory=session_factory)

    returned_at = time.monotonic()
    trigger.join(timeout=5)
    assert abandoned is False
    assert documents_polled.is_set()
    assert generations_polled.is_set()
    assert stop_requested_at
    assert returned_at - stop_requested_at[0] < 2.0
    assert not [
        thread
        for thread in threading.enumerate()
        if thread.name in {"documents", "generations"}
    ]
    assert worker_events.events("document_worker_stopped")
    assert worker_events.events("generation_worker_stopped")
    assert worker_events.events("worker_shutdown_deadline_exceeded") == []


def test_the_combined_worker_finishes_a_single_pass_without_being_stopped(
    session_factory, monkeypatch, worker_events
) -> None:
    _isolate_the_combined_worker(monkeypatch, mode=WORKER_SHUTDOWN_MODE_ABORT)
    stop = threading.Event()

    abandoned = worker.run_worker(
        once=True, stop_event=stop, session_factory=session_factory
    )

    assert abandoned is False
    assert not stop.is_set()
    assert worker_events.events("worker_shutdown_aborting_job") == []
    assert worker_events.events("worker_shutdown_deadline_exceeded") == []
    assert not [
        thread
        for thread in threading.enumerate()
        if thread.name in {"documents", "generations"}
    ]


@pytest.mark.parametrize("abandoned", [True, False])
def test_the_combined_worker_exits_at_once_only_when_it_abandoned_a_thread(
    monkeypatch, abandoned
) -> None:
    calls: list[object] = []
    monkeypatch.setattr(worker, "configure_logging", lambda **_k: None)
    monkeypatch.setattr(worker, "_install_shutdown_handlers", lambda _stop: None)
    monkeypatch.setattr(worker, "run_worker", lambda **_k: abandoned)
    monkeypatch.setattr(
        worker.shutdown, "kill_child_processes", lambda: calls.append("kill") or 0
    )
    monkeypatch.setattr(worker.logging, "shutdown", lambda: calls.append("flush"))
    monkeypatch.setattr(worker.os, "_exit", lambda code: calls.append(("exit", code)))

    worker.main([])

    assert calls == (["kill", "flush", ("exit", 0)] if abandoned else ["kill"])


def test_kill_child_processes_kills_and_reaps_a_running_child(worker_events) -> None:
    process = multiprocessing.get_context("spawn").Process(
        target=time.sleep, args=(60,)
    )
    process.start()
    try:
        assert shutdown.kill_child_processes() == 1
        assert not process.is_alive()
        assert multiprocessing.active_children() == []
    finally:
        if process.is_alive():
            process.kill()
        process.join(timeout=5)
    assert [
        record.item_count
        for record in worker_events.events("worker_child_processes_killed")
    ] == [1]


@pytest.mark.skipif(os.name == "nt", reason="POSIX process-group signal behavior")
def test_extraction_children_are_killed_although_they_ignore_sigterm(
    worker_events,
) -> None:
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    parent_connection, child_connection = context.Pipe(duplex=False)
    job = ClaimedJob(
        id=1,
        document_id=uuid4(),
        course_id=1,
        claim_token=str(uuid4()),
        attempt_count=1,
        max_attempts=3,
        storage_provider=SignalingSlowStorage.provider,
        storage_key="document.txt",
        file_hash="0" * 64,
        file_type="txt",
        file_size=16,
    )
    process = context.Process(
        target=document_processor._extraction_process,
        args=(child_connection, SignalingSlowStorage(ready), job),
    )
    document_processor._start_extraction_process(process)
    child_connection.close()
    try:
        assert ready.wait(timeout=5)
        os.kill(process.pid, signal.SIGTERM)
        time.sleep(0.2)
        assert process.is_alive()

        assert shutdown.kill_child_processes() == 1

        assert not process.is_alive()
        assert multiprocessing.active_children() == []
    finally:
        if process.is_alive():
            process.kill()
        process.join(timeout=5)
        parent_connection.close()
    assert [
        record.item_count
        for record in worker_events.events("worker_child_processes_killed")
    ] == [1]
