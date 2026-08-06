import logging
import multiprocessing
import os
import signal
import threading
import time
from io import BytesIO
from uuid import uuid4

import pytest

from services.processing_jobs import ClaimedJob
from workers import document_processor


class FakeSession:
    def __enter__(self):
        return self

    def __exit__(self, _exception_type, _exception, _traceback):
        return False


def fake_session_factory() -> FakeSession:
    return FakeSession()


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

    monkeypatch.setattr(document_processor, "recover_expired_jobs", lambda *_a, **_k: 0)

    def process_job(*, session_factory, worker_id, shutdown_requested):
        session_factories.append(session_factory)
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

    monkeypatch.setattr(document_processor, "recover_expired_jobs", recover)
    monkeypatch.setattr(document_processor, "process_next_job", process_job)

    document_processor.run_worker(
        once=False,
        stop_event=stop,
        session_factory=fake_session_factory,
    )

    assert claims == 0


def test_worker_uses_one_generated_identity_for_all_claims(monkeypatch) -> None:
    stop = threading.Event()
    worker_ids: list[str] = []

    monkeypatch.setattr(document_processor, "_default_worker_id", lambda: "stable-id")
    monkeypatch.setattr(document_processor, "recover_expired_jobs", lambda *_a, **_k: 0)

    def process_job(*, session_factory, worker_id, shutdown_requested):
        assert session_factory is fake_session_factory
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
    )

    assert worker_ids == ["stable-id", "stable-id"]


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
