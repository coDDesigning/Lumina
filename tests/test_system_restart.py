from pathlib import Path

import pytest
from sqlalchemy.orm import Session, sessionmaker

from backend.app import settings_overrides
from backend.app.settings_overrides import (
    BASE_REVISION,
    RESTART_DRAINING,
    RESTART_FAILED,
    RESTART_QUEUED,
    RESTART_RESTARTING,
    RestartRequest,
)
from services import system_restart
from services.system_restart import (
    InFlightWork,
    RestartAlreadyRunning,
    RestartCoordinator,
    count_in_flight_work,
    request_restart,
)


@pytest.fixture(autouse=True)
def isolated_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "system-settings"
    monkeypatch.setenv("SYSTEM_SETTINGS_DIRECTORY", str(directory))
    settings_overrides._active_revision = BASE_REVISION
    settings_overrides._applied_keys = ()
    settings_overrides._rolled_back_from = None
    settings_overrides._baseline = None
    return directory


def _request(revision: int = 1) -> RestartRequest:
    return request_restart(
        target_revision=revision,
        actor_id=7,
        changed_keys=("OCR_DPI",),
        drain_timeout_seconds=60,
    )


def test_a_restart_request_starts_queued() -> None:
    request = _request()

    assert request.state == RESTART_QUEUED
    assert request.target_revision == 1
    assert request.actor_id == 7
    assert request.changed_keys == ("OCR_DPI",)
    assert request.drain_deadline is not None
    assert settings_overrides.read_restart_request() == request


def test_a_second_request_is_refused_while_one_is_active() -> None:
    _request()

    with pytest.raises(RestartAlreadyRunning):
        _request()


def test_a_request_is_allowed_once_the_previous_one_finished() -> None:
    first = _request()
    settings_overrides.write_restart_request(
        RestartRequest(
            request_id=first.request_id,
            state="ready",
            target_revision=first.target_revision,
            requested_at=first.requested_at,
            updated_at=first.updated_at,
        )
    )

    assert _request(2).target_revision == 2


def test_an_idle_queue_restarts_without_draining(monkeypatch) -> None:
    request = _request()
    monkeypatch.setattr(
        system_restart,
        "count_in_flight_work",
        lambda session: InFlightWork(0, 0, 0),
    )
    stopped: list[bool] = []

    RestartCoordinator(
        request,
        session_factory=lambda: _FakeSession(),
        drain_timeout_seconds=5,
        poll_seconds=0.01,
        exit_delay_seconds=0.0,
        stop_process=lambda: stopped.append(True),
    ).run()

    final = settings_overrides.read_restart_request()
    assert final is not None
    assert final.state == RESTART_RESTARTING
    assert final.detail is None
    assert stopped == [True]


def test_the_restart_waits_for_work_then_proceeds(monkeypatch) -> None:
    request = _request()
    counts = iter([InFlightWork(2, 0, 0), InFlightWork(1, 0, 0), InFlightWork(0, 0, 0)])
    monkeypatch.setattr(
        system_restart,
        "count_in_flight_work",
        lambda session: next(counts),
    )
    stopped: list[bool] = []

    RestartCoordinator(
        request,
        session_factory=lambda: _FakeSession(),
        drain_timeout_seconds=5,
        poll_seconds=0.01,
        exit_delay_seconds=0.0,
        stop_process=lambda: stopped.append(True),
    ).run()

    final = settings_overrides.read_restart_request()
    assert final is not None
    assert final.state == RESTART_RESTARTING
    assert final.detail is None
    assert stopped == [True]


def test_work_still_running_at_the_deadline_is_reported_and_requeued(
    monkeypatch,
) -> None:
    request = _request()
    monkeypatch.setattr(
        system_restart,
        "count_in_flight_work",
        lambda session: InFlightWork(1, 0, 2),
    )
    stopped: list[bool] = []

    RestartCoordinator(
        request,
        session_factory=lambda: _FakeSession(),
        drain_timeout_seconds=0,
        poll_seconds=0.01,
        exit_delay_seconds=0.0,
        stop_process=lambda: stopped.append(True),
    ).run()

    final = settings_overrides.read_restart_request()
    assert final is not None
    assert final.state == RESTART_RESTARTING
    assert final.detail is not None
    assert "returned to the queue" in final.detail
    assert stopped == [True]


def test_a_drain_marks_the_request_draining(monkeypatch) -> None:
    request = _request()
    seen: list[str] = []
    counts = iter([InFlightWork(1, 0, 0), InFlightWork(0, 0, 0)])

    def _count(session):
        current = settings_overrides.read_restart_request()
        if current is not None:
            seen.append(current.state)
        return next(counts)

    monkeypatch.setattr(system_restart, "count_in_flight_work", _count)

    RestartCoordinator(
        request,
        session_factory=lambda: _FakeSession(),
        drain_timeout_seconds=5,
        poll_seconds=0.01,
        exit_delay_seconds=0.0,
        stop_process=lambda: None,
    ).run()

    assert RESTART_DRAINING in seen


def test_a_failed_drain_never_stops_the_process(monkeypatch) -> None:
    request = _request()

    def _explode(session):
        raise RuntimeError("database is gone")

    monkeypatch.setattr(system_restart, "count_in_flight_work", _explode)
    stopped: list[bool] = []

    RestartCoordinator(
        request,
        session_factory=lambda: _FakeSession(),
        drain_timeout_seconds=5,
        poll_seconds=0.01,
        exit_delay_seconds=0.0,
        stop_process=lambda: stopped.append(True),
    ).run()

    final = settings_overrides.read_restart_request()
    assert final is not None
    assert final.state == RESTART_FAILED
    assert stopped == []


def test_in_flight_work_counts_all_three_queues(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        work = count_in_flight_work(session)

    assert work.documents == 0
    assert work.profile_documents == 0
    assert work.generations == 0
    assert work.total == 0
    assert work.as_payload() == {
        "documents": 0,
        "profile_documents": 0,
        "generations": 0,
        "total": 0,
    }


class _FakeSession:
    def close(self) -> None:
        return None


def test_the_worker_stops_when_a_new_revision_is_targeted(monkeypatch) -> None:
    import threading

    from workers import worker as worker_module

    stop = threading.Event()
    settings_overrides.write_restart_request(
        RestartRequest(
            request_id="req-1",
            state=RESTART_RESTARTING,
            target_revision=9,
            requested_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
        )
    )

    captured: dict[str, object] = {}

    def fake_supervise(threads, stop_event, *, mode, session_factory):
        captured["watcher_saw"] = _drain_watcher(stop_event)
        return False

    monkeypatch.setattr(worker_module.shutdown, "supervise", fake_supervise)
    monkeypatch.setattr(
        worker_module, "run_document_worker", lambda **kwargs: stop.wait(0.01)
    )
    monkeypatch.setattr(
        worker_module, "run_generation_worker", lambda **kwargs: stop.wait(0.01)
    )

    worker_module.run_worker(session_factory=lambda: _FakeSession())

    assert captured["watcher_saw"] is True


def _drain_watcher(stop_event) -> bool:
    import time

    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if stop_event.is_set():
            return True
        time.sleep(0.05)
    return False


def test_readiness_promotes_the_active_revision(monkeypatch, tmp_path) -> None:
    saved = settings_overrides.save_overrides(
        {"OCR_DPI": "150"}, expected_revision=BASE_REVISION
    )
    settings_overrides.apply_overrides()

    assert settings_overrides.load_last_known_good() is None

    import main

    main.promote_active_revision()

    good = settings_overrides.load_last_known_good()
    assert good is not None
    assert good.revision == saved.revision
