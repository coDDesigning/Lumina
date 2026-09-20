import logging
import multiprocessing
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from sqlalchemy.orm import Session

from backend.app.config import WORKER_SHUTDOWN_MODE_DRAIN
from services.document_lock import release_process_generation_locks

logger = logging.getLogger(__name__)

SessionFactory = Callable[[], Session]
KIND_COURSE_DOCUMENT = "course_document"
KIND_PROFILE_DOCUMENT = "profile_document"
KIND_GENERATION = "generation"
ABORT_SHUTDOWN_SECONDS = 10.0


@dataclass(frozen=True, slots=True)
class InFlightJob:
    kind: str
    job_id: int
    job_type: str
    claim_token: str
    attempt_number: int
    worker_id: str
    deadline: float
    releaser: Callable[[Session, int, str], bool]
    course_id: int | None
    user_id: int | None
    document_id: str | None


_registry_lock = threading.Lock()
_registry: dict[tuple[str, int, str], InFlightJob] = {}


def _key(job: InFlightJob) -> tuple[str, int, str]:
    return (job.kind, job.job_id, job.claim_token)


def register(job: InFlightJob) -> None:
    with _registry_lock:
        _registry[_key(job)] = job


def unregister(job: InFlightJob) -> None:
    with _registry_lock:
        _registry.pop(_key(job), None)


def in_flight() -> list[InFlightJob]:
    with _registry_lock:
        return list(_registry.values())


def _operation_id(job: InFlightJob) -> str:
    if job.kind == KIND_GENERATION:
        return f"generation_job:{job.job_type}:{job.job_id}"
    scope = "profile" if job.kind == KIND_PROFILE_DOCUMENT else "course"
    return f"processing_job:{scope}:{job.job_id}"


def _log_fields(job: InFlightJob) -> dict[str, object]:
    return {
        "job_id": job.job_id,
        "job_type": job.job_type,
        "operation_id": _operation_id(job),
        "attempt_number": job.attempt_number,
        "worker_id": job.worker_id,
        "course_id": job.course_id,
        "user_id": job.user_id,
        "document_id": job.document_id,
    }


def release(job: InFlightJob, session_factory: SessionFactory) -> bool:
    try:
        with session_factory() as session:
            released = job.releaser(session, job.job_id, job.claim_token)
    except Exception:
        logger.warning(
            "Could not return %s job %s to the queue; lease recovery will requeue it",
            job.kind,
            job.job_id,
            exc_info=True,
            extra={"event": "worker_shutdown_release_failed", **_log_fields(job)},
        )
        return False
    if released:
        logger.info(
            "Returned %s job %s to the queue without spending its attempt",
            job.kind,
            job.job_id,
            extra={
                "event": "worker_shutdown_job_released",
                "job_status": "queued",
                **_log_fields(job),
            },
        )
        return True
    logger.info(
        "%s job %s was no longer held by this worker, so it was left as it was",
        job.kind,
        job.job_id,
        extra={"event": "worker_shutdown_job_not_released", **_log_fields(job)},
    )
    return False


def release_in_flight(session_factory: SessionFactory) -> int:
    released = 0
    released_generation = False
    for job in in_flight():
        if release(job, session_factory):
            released += 1
            released_generation = released_generation or job.kind == KIND_GENERATION
    if released_generation:
        try:
            with session_factory() as session:
                locks = release_process_generation_locks(session)
        except Exception:
            logger.warning(
                "Could not release the document generation locks this worker held; "
                "they expire on their own",
                exc_info=True,
                extra={"event": "document_lock_release_failed"},
            )
        else:
            if locks:
                logger.info(
                    "Released %s document generation locks held by aborted generations",
                    locks,
                    extra={
                        "event": "worker_generation_locks_released",
                        "item_count": locks,
                    },
                )
    return released


def kill_child_processes() -> int:
    children = multiprocessing.active_children()
    for child in children:
        child.kill()
    for child in children:
        child.join(2)
    if children:
        logger.warning(
            "Killed %s worker subprocesses that were still running",
            len(children),
            extra={
                "event": "worker_child_processes_killed",
                "item_count": len(children),
            },
        )
    return len(children)


def _announce(job: InFlightJob, mode: str) -> None:
    remaining = max(0.0, job.deadline - time.monotonic())
    if mode == WORKER_SHUTDOWN_MODE_DRAIN:
        logger.info(
            "Shutdown requested; waiting for %s job %s (%s) with %ss left in its attempt",
            job.kind,
            job.job_id,
            job.job_type,
            round(remaining),
            extra={
                "event": "worker_shutdown_waiting_for_job",
                "job_status": "running",
                "duration_ms": round(remaining * 1000, 3),
                **_log_fields(job),
            },
        )
        return
    logger.warning(
        "Shutdown requested; aborting %s job %s (%s) with %ss left in its attempt",
        job.kind,
        job.job_id,
        job.job_type,
        round(remaining),
        extra={
            "event": "worker_shutdown_aborting_job",
            "job_status": "running",
            "duration_ms": round(remaining * 1000, 3),
            **_log_fields(job),
        },
    )


def supervise(
    threads: Sequence[threading.Thread],
    stop: threading.Event,
    *,
    mode: str,
    session_factory: SessionFactory,
) -> bool:
    while any(thread.is_alive() for thread in threads):
        if stop.wait(0.2):
            break
    else:
        return False

    announced: set[tuple[str, int, str]] = set()
    deadline = (
        None
        if mode == WORKER_SHUTDOWN_MODE_DRAIN
        else time.monotonic() + ABORT_SHUTDOWN_SECONDS
    )
    while True:
        for job in in_flight():
            key = _key(job)
            if key not in announced:
                announced.add(key)
                _announce(job, mode)
        alive = [thread for thread in threads if thread.is_alive()]
        if not alive:
            return False
        if deadline is not None and time.monotonic() >= deadline:
            break
        alive[0].join(
            timeout=(
                0.2
                if deadline is None
                else max(0.0, min(0.2, deadline - time.monotonic()))
            )
        )

    release_in_flight(session_factory)
    kill_child_processes()
    logger.warning(
        "%s worker threads were still running at the shutdown deadline; "
        "their jobs were released and the worker exits",
        len(alive),
        extra={"event": "worker_shutdown_deadline_exceeded", "item_count": len(alive)},
    )
    return True
