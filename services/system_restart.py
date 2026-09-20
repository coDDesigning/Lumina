from __future__ import annotations

import logging
import os
import signal
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app import settings_overrides
from backend.app.models import (
    JOB_STATUS_QUEUED,
    JOB_STATUS_RUNNING,
    GenerationJob,
    ProcessingJob,
    ProfileProcessingJob,
)
from backend.app.settings_overrides import (
    RESTART_DRAINING,
    RESTART_FAILED,
    RESTART_QUEUED,
    RESTART_RESTARTING,
    RestartRequest,
)

logger = logging.getLogger(__name__)

ACTIVE_STATUSES = (JOB_STATUS_QUEUED, JOB_STATUS_RUNNING)
POLL_SECONDS = 2.0
EXIT_DELAY_SECONDS = 1.0


class RestartUnsupported(RuntimeError):
    pass


class RestartAlreadyRunning(RuntimeError):
    pass


class NothingToApply(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class InFlightWork:
    documents: int
    profile_documents: int
    generations: int

    @property
    def total(self) -> int:
        return self.documents + self.profile_documents + self.generations

    def as_payload(self) -> dict[str, int]:
        return {
            "documents": self.documents,
            "profile_documents": self.profile_documents,
            "generations": self.generations,
            "total": self.total,
        }


def count_in_flight_work(session: Session) -> InFlightWork:
    def _count(model) -> int:
        statement = select(func.count()).where(model.status.in_(ACTIVE_STATUSES))
        return int(session.execute(statement).scalar_one() or 0)

    return InFlightWork(
        documents=_count(ProcessingJob),
        profile_documents=_count(ProfileProcessingJob),
        generations=_count(GenerationJob),
    )


def _now() -> datetime:
    return datetime.now(timezone.utc)


def request_restart(
    *,
    target_revision: int,
    actor_id: int | None,
    changed_keys: tuple[str, ...],
    drain_timeout_seconds: int,
) -> RestartRequest:
    existing = settings_overrides.read_restart_request()
    if existing is not None and existing.is_active:
        raise RestartAlreadyRunning(
            "A restart is already in progress for this deployment."
        )

    timestamp = _now()
    deadline = timestamp + timedelta(seconds=drain_timeout_seconds)
    request = RestartRequest(
        request_id=uuid.uuid4().hex,
        state=RESTART_QUEUED,
        target_revision=target_revision,
        requested_at=timestamp.isoformat(timespec="seconds"),
        updated_at=timestamp.isoformat(timespec="seconds"),
        drain_deadline=deadline.isoformat(timespec="seconds"),
        actor_id=actor_id,
        changed_keys=changed_keys,
    )
    return settings_overrides.write_restart_request(request)


def _transition(
    request: RestartRequest, state: str, detail: str | None = None
) -> RestartRequest:
    updated = RestartRequest(
        request_id=request.request_id,
        state=state,
        target_revision=request.target_revision,
        requested_at=request.requested_at,
        updated_at=_now().isoformat(timespec="seconds"),
        drain_deadline=request.drain_deadline,
        detail=detail,
        actor_id=request.actor_id,
        changed_keys=request.changed_keys,
    )
    return settings_overrides.write_restart_request(updated)


class RestartCoordinator:
    def __init__(
        self,
        request: RestartRequest,
        *,
        session_factory: Callable[[], Session],
        drain_timeout_seconds: int,
        poll_seconds: float = POLL_SECONDS,
        exit_delay_seconds: float = EXIT_DELAY_SECONDS,
        stop_process: Callable[[], None] | None = None,
    ) -> None:
        self._request = request
        self._session_factory = session_factory
        self._drain_timeout = drain_timeout_seconds
        self._poll = poll_seconds
        self._exit_delay = exit_delay_seconds
        self._stop_process = stop_process or _terminate_self
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self.run, name="lumina-restart-coordinator", daemon=True
        )
        self._thread.start()

    def run(self) -> None:
        try:
            remaining = self._drain()
        except Exception:
            logger.exception(
                "Restart drain failed",
                extra={
                    "event": "system_restart_failed",
                    "stage": "drain",
                    "success": False,
                },
            )
            _transition(self._request, RESTART_FAILED, "Could not check active jobs.")
            return

        detail = None
        if remaining:
            detail = (
                f"{remaining} job(s) were still running at the drain deadline and "
                "were returned to the queue."
            )
        logger.info(
            "Restarting to apply configuration",
            extra={
                "event": "system_restart_applying",
                "settings_revision": self._request.target_revision,
                "item_count": remaining,
                "success": True,
            },
        )
        _transition(self._request, RESTART_RESTARTING, detail)
        time.sleep(self._exit_delay)
        self._stop_process()

    def _drain(self) -> int:
        if self._drain_timeout <= 0:
            return self._snapshot().total

        _transition(self._request, RESTART_DRAINING)
        deadline = time.monotonic() + self._drain_timeout
        while True:
            work = self._snapshot()
            if work.total == 0:
                return 0
            if time.monotonic() >= deadline:
                return work.total
            time.sleep(min(self._poll, max(0.0, deadline - time.monotonic())))

    def _snapshot(self) -> InFlightWork:
        session = self._session_factory()
        try:
            return count_in_flight_work(session)
        finally:
            session.close()


def _terminate_self() -> None:
    logger.info(
        "Stopping the process so the supervisor restarts it",
        extra={"event": "system_restart_process_stopping", "success": True},
    )
    os.kill(os.getpid(), signal.SIGTERM)
