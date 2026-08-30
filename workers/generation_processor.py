"""Separate process entry point for backgrounded AI generation.

The document queue forks a child process per job because parsing an untrusted
file can hang or die in a way a thread cannot be taken back from. A generation
is one provider call that already carries its own deadline, so a slot here is a
thread and the attempt bound is enforced by refusing to renew the lease: the
generation is never killed, it is abandoned, and its claim token stops being
valid the moment the lease lapses. Anything it writes afterwards is rejected by
the same fencing that protects the document queue.

Delivery is at-least-once, as it is for documents. The provider call may be
repeated after a worker dies, but artifact persistence and job completion share
one fenced transaction. A worker whose lease expired therefore cannot publish
an orphaned duplicate after another worker has claimed the job.
"""

import argparse
import logging
import os
import signal
import socket
import threading
import time
from collections.abc import Callable, Sequence
from typing import Protocol
from uuid import uuid4

from sqlalchemy.orm import Session

from backend.app.config import settings
from backend.app.database import SessionLocal
from backend.app.models import (
    JOB_TYPE_GENERATE_FLASHCARD,
    JOB_TYPE_GENERATE_QUIZ,
    JOB_TYPE_GENERATE_STUDY_GUIDE,
)
from backend.app.observability import (
    bind_request_id,
    configure_logging,
    emit_emf_metrics,
    reset_request_id,
)
from backend.app.readiness import ReadinessError, check_readiness
from schemas.quiz import (
    QuizGenerationContext,
    QuizGenerationSettings,
    QuizRequest,
)
from schemas.study_guide import (
    StudyGuideGenerationContext,
    StudyGuideGenerationSettings,
    StudyGuideRequest,
)
from schemas.flashcard import (
    FlashcardGenerationContext,
    FlashcardGenerationSettings,
    FlashcardRequest,
)
from services.generation_jobs import (
    ClaimedGenerationJob,
    GenerationJobStateError,
    claim_next_generation_job,
    complete_generation_job,
    fail_generation_job,
    generation_queue_metrics,
    heartbeat_generation_job,
    recover_expired_generation_jobs,
)
from services.quiz import QuizService
from services.study_guide import StudyGuideService
from services.flashcard import FlashcardService
from services.text_generation import (
    get_text_generation_provider,
    resolve_effective_model,
)
from storage.base import Storage
from storage.dependencies import get_storage
from utils.ai_errors import (
    PUBLIC_MESSAGES,
    AiErrorCode,
    classify_generation_error,
)

logger = logging.getLogger(__name__)
SessionFactory = Callable[[], Session]
ResultPersister = Callable[[Session], tuple[int | None, int | None]]
RECOVERY_BATCH_SIZE = 100
MAX_RECOVERY_BATCHES_PER_PASS = 10
HEARTBEAT_SHUTDOWN_SECONDS = 30

# Failures worth spending the student's already-taken credit on a second time.
# Everything outside this set describes a request that will fail identically on
# every attempt -- no material, an unusable model -- so retrying it only delays
# telling the student something they have to act on.
RETRYABLE_ERROR_CODES = frozenset(
    {
        AiErrorCode.PROVIDER_UNAVAILABLE,
        AiErrorCode.PROVIDER_TIMEOUT,
        AiErrorCode.PROVIDER_RATE_LIMITED,
        AiErrorCode.RETRIEVAL_UNAVAILABLE,
        AiErrorCode.INVALID_GENERATED_STRUCTURE,
    }
)


class StopEvent(Protocol):
    def is_set(self) -> bool: ...

    def wait(self, timeout: float) -> bool: ...


class _SignalStopEvent:
    """Lock-free stop flag written by Python's main-thread signal handler."""

    def __init__(self) -> None:
        self.requested = False

    def is_set(self) -> bool:
        return self.requested

    def wait(self, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while not self.requested:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(0.1, remaining))
        return self.requested


def _default_worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{uuid4()}"


def _heartbeat_loop(
    session_factory: SessionFactory,
    job: ClaimedGenerationJob,
    lease_seconds: int,
    stop: threading.Event,
    claim_lost: threading.Event,
    attempt_deadline: float,
) -> None:
    """Hold the lease while the attempt is still allowed to be running.

    Letting the lease lapse is how an overrunning generation is bounded. The
    thread cannot be cancelled, so instead the job stops being ours: the reaper
    requeues or fails it, and the guarded writes reject whatever the abandoned
    call eventually returns.
    """
    interval = min(30.0, max(0.05, lease_seconds / 3))
    while not stop.is_set():
        if time.monotonic() >= attempt_deadline:
            logger.warning(
                "Generation job %s exceeded its attempt budget; releasing the lease",
                job.id,
                extra={
                    "event": "generation_attempt_timeout",
                    "job_id": job.id,
                    "job_type": job.job_type,
                    "course_id": job.course_id,
                },
            )
            claim_lost.set()
            return
        try:
            with session_factory() as session:
                current = heartbeat_generation_job(
                    session, job.id, job.claim_token, lease_seconds
                )
        except Exception:
            logger.exception("Failed to heartbeat generation job %s", job.id)
            if stop.wait(interval):
                return
            continue
        if not current:
            claim_lost.set()
            return
        if stop.wait(interval):
            return


def _run_study_guide(session: Session, job: ClaimedGenerationJob) -> ResultPersister:
    request = StudyGuideRequest.model_validate_json(job.request_payload)
    effective_model = resolve_effective_model(
        request.model,
        None,
        required_capability="study_guide",
    )
    try:
        provider = get_text_generation_provider(effective_model=effective_model)
    except TypeError:
        provider = get_text_generation_provider()

    generation = StudyGuideService.generate(
        session,
        job.course_id,
        request,
        provider,
        user_id=job.user_id,
        prepaid_charge=job.charge_receipt,
    )
    applied_settings = StudyGuideGenerationSettings.from_request(
        generation.effective_request,
        retrieval_limit=settings.retrieval_chunk_limit,
        retrieval_min_similarity=settings.retrieval_min_similarity,
    )
    generation_context = StudyGuideGenerationContext.from_material(
        generation.material,
        profile_knowledge=generation.profile_knowledge,
    ).model_dump_json()
    session.commit()

    def persist(result_session: Session) -> tuple[int | None, int | None]:
        persisted = StudyGuideService.save_generated_output(
            result_session,
            job.course_id,
            generation.study_guide,
            user_id=job.user_id,
            model_used=generation.model_used,
            summary_mode=generation.effective_request.summary_mode,
            generation_settings=applied_settings.model_dump_json(),
            generation_context=generation_context,
            commit=False,
        )
        return persisted.id, None

    return persist


def _run_quiz(session: Session, job: ClaimedGenerationJob) -> ResultPersister:
    request = QuizRequest.model_validate_json(job.request_payload)
    effective_model = resolve_effective_model(
        request.model,
        None,
        required_capability="quiz",
    )
    provider = get_text_generation_provider(
        effective_model=effective_model,
        require_json_mode=True,
    )

    generation = QuizService.generate(
        session,
        job.course_id,
        request,
        provider,
        user_id=job.user_id,
        prepaid_charge=job.charge_receipt,
    )
    applied_settings = QuizGenerationSettings.from_request(
        generation.effective_request,
        retrieval_limit=settings.retrieval_chunk_limit,
        retrieval_min_similarity=settings.retrieval_min_similarity,
    ).model_dump_json()
    applied_context = QuizGenerationContext.from_material(
        generation.material,
        profile_knowledge=generation.profile_knowledge,
    ).model_dump_json()

    session.commit()

    def persist(result_session: Session) -> tuple[int | None, int | None]:
        persisted = QuizService.save_generated_quiz(
            result_session,
            job.course_id,
            generation.quiz,
            user_id=job.user_id,
            model_used=generation.model_used,
            generation_settings=applied_settings,
            generation_context=applied_context,
            citations=generation.material.citation_map,
            commit=False,
        )
        return None, persisted.view.id

    return persist


def _run_flashcard(session: Session, job: ClaimedGenerationJob) -> ResultPersister:
    request = FlashcardRequest.model_validate_json(job.request_payload)
    effective_model = resolve_effective_model(
        request.model,
        None,
        required_capability="flashcard",
    )
    provider = get_text_generation_provider(
        effective_model=effective_model,
        require_json_mode=True,
    )

    generation = FlashcardService.generate(
        session,
        job.course_id,
        provider,
        request=request,
        user_id=job.user_id,
        prepaid_charge=job.charge_receipt,
    )
    applied_settings = FlashcardGenerationSettings.from_request(
        generation.effective_request,
        retrieval_limit=settings.retrieval_chunk_limit,
        retrieval_min_similarity=settings.retrieval_min_similarity,
    ).model_dump_json()
    applied_context = FlashcardGenerationContext.from_material(
        generation.material,
        profile_knowledge=generation.profile_knowledge,
    ).model_dump_json()

    session.commit()

    def persist(result_session: Session) -> tuple[int | None, int | None]:
        persisted = FlashcardService.save_generated_flashcards(
            result_session,
            job.course_id,
            generation.flashcards,
            user_id=job.user_id,
            model_used=generation.model_used,
            generation_settings=applied_settings,
            generation_context=applied_context,
            commit=False,
        )
        return persisted.id, None

    return persist


RUNNERS: dict[str, Callable[[Session, ClaimedGenerationJob], ResultPersister]] = {
    JOB_TYPE_GENERATE_STUDY_GUIDE: _run_study_guide,
    JOB_TYPE_GENERATE_QUIZ: _run_quiz,
    JOB_TYPE_GENERATE_FLASHCARD: _run_flashcard,
}


def _record_failure(
    session_factory: SessionFactory,
    job: ClaimedGenerationJob,
    exc: BaseException,
) -> str | None:
    """Fail or requeue the job, storing a code the panel can already render.

    The stored code is the same vocabulary the synchronous routes return in
    ``X-Error-Code``, so a generation that failed in the background explains
    itself through the mapping the client already has.
    """
    code = classify_generation_error(exc)
    retryable = code in RETRYABLE_ERROR_CODES
    exponent = min(6, max(0, job.attempt_count - 1))
    retry_delay = min(60.0, 2.0**exponent)

    with session_factory() as session:
        resulting_status = fail_generation_job(
            session,
            job.id,
            job.claim_token,
            error_code=code.value,
            error_message=PUBLIC_MESSAGES[code],
            retryable=retryable,
            retry_delay_seconds=retry_delay,
        )

    if resulting_status == "failed":
        logger.error(
            "Permanent generation failure for job %s (course %s): %s",
            job.id,
            job.course_id,
            code.value,
            extra={
                "event": "permanent_generation_failure",
                "job_id": job.id,
                "job_type": job.job_type,
                "course_id": job.course_id,
                "user_id": job.user_id,
                "error_code": code.value,
            },
            exc_info=exc,
        )
    return resulting_status


def _stop_heartbeat(stop: threading.Event, heartbeat: threading.Thread) -> None:
    stop.set()
    heartbeat.join(timeout=HEARTBEAT_SHUTDOWN_SECONDS)


def process_next_generation_job(
    *,
    session_factory: SessionFactory = SessionLocal,
    worker_id: str | None = None,
    lease_seconds: int | None = None,
    shutdown_requested: Callable[[], bool] | None = None,
) -> bool:
    """Claim and run at most one generation. Returns whether one was run."""
    if shutdown_requested is not None and shutdown_requested():
        return False
    if lease_seconds is None:
        lease_seconds = settings.generation_job_lease_seconds
    if worker_id is None:
        worker_id = _default_worker_id()

    with session_factory() as session:
        if shutdown_requested is not None and shutdown_requested():
            return False
        job = claim_next_generation_job(session, worker_id, lease_seconds)
    if job is None:
        return False

    runner = RUNNERS.get(job.job_type)
    token = bind_request_id(job.correlation_id)
    try:
        if runner is None:
            # A job type this build does not know how to run. Failing it is the
            # only honest outcome: retrying would burn attempts against code
            # that does not exist here, and leaving it queued would hold one of
            # the student's slots forever.
            with session_factory() as session:
                fail_generation_job(
                    session,
                    job.id,
                    job.claim_token,
                    error_code=AiErrorCode.GENERATION_FAILED.value,
                    error_message=PUBLIC_MESSAGES[AiErrorCode.GENERATION_FAILED],
                    retryable=False,
                )
            logger.error("Generation job %s has unknown type %s", job.id, job.job_type)
            return True

        started = time.monotonic()
        attempt_deadline = started + settings.generation_job_attempt_timeout_seconds
        stop = threading.Event()
        claim_lost = threading.Event()
        heartbeat = threading.Thread(
            target=_heartbeat_loop,
            args=(
                session_factory,
                job,
                lease_seconds,
                stop,
                claim_lost,
                attempt_deadline,
            ),
            daemon=True,
        )
        heartbeat.start()
        try:
            with session_factory() as session:
                persist_result = runner(session, job)
            if claim_lost.is_set() or time.monotonic() >= attempt_deadline:
                raise GenerationJobStateError(
                    f"Generation job {job.id} exceeded its attempt budget"
                )
            with session_factory() as session:
                complete_generation_job(
                    session,
                    job.id,
                    job.claim_token,
                    result_writer=lambda: persist_result(session),
                )
        except GenerationJobStateError:
            # The claim moved on without us -- an expired lease that the reaper
            # already requeued, most often. Whoever holds it now owns the
            # outcome, so this attempt says nothing further about it.
            logger.warning(
                "Generation job %s was no longer held by this worker", job.id
            )
        except Exception as exc:
            if claim_lost.is_set():
                logger.warning(
                    "Generation job %s failed after its lease was released",
                    job.id,
                    exc_info=exc,
                )
            else:
                try:
                    _record_failure(session_factory, job, exc)
                except GenerationJobStateError:
                    logger.warning(
                        "Generation job %s could not be failed under this claim",
                        job.id,
                    )
        else:
            logger.info(
                "Generation job %s completed",
                job.id,
                extra={
                    "event": "generation_job_completed",
                    "job_id": job.id,
                    "job_type": job.job_type,
                    "course_id": job.course_id,
                    "duration_ms": round((time.monotonic() - started) * 1000, 3),
                },
            )
        finally:
            _stop_heartbeat(stop, heartbeat)
    finally:
        reset_request_id(token)
    return True


def check_worker_ready(
    *,
    session_factory: SessionFactory = SessionLocal,
    storage: Storage | None = None,
) -> None:
    if storage is None:
        storage = get_storage()
    with session_factory() as session:
        check_readiness(session, storage)


class _MaintenanceSchedule:
    def __init__(self) -> None:
        self.recovery_interval = min(
            30.0,
            max(0.1, settings.generation_job_lease_seconds / 2),
        )
        self.next_recovery = 0.0


class _CompositeStopEvent:
    def __init__(self, external: StopEvent) -> None:
        self._external = external
        self._internal = threading.Event()

    def set(self) -> None:
        self._internal.set()

    def is_set(self) -> bool:
        return self._internal.is_set() or self._external.is_set()

    def wait(self, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while not self.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            self._internal.wait(min(0.1, remaining))
        return self.is_set()


def _maintenance_cycle(
    schedule: _MaintenanceSchedule,
    *,
    session_factory: SessionFactory,
    stop: StopEvent,
) -> None:
    monotonic_now = time.monotonic()
    if monotonic_now < schedule.next_recovery:
        return

    recovery_saturated = False
    try:
        recovered_total = 0
        for _ in range(MAX_RECOVERY_BATCHES_PER_PASS):
            if stop.is_set():
                break
            with session_factory() as session:
                recovered = recover_expired_generation_jobs(
                    session,
                    limit=RECOVERY_BATCH_SIZE,
                )
            recovered_total += recovered
            if recovered < RECOVERY_BATCH_SIZE:
                break
        else:
            recovery_saturated = True
        if recovered_total:
            logger.info("Recovered %s expired generation jobs", recovered_total)
        with session_factory() as session:
            queue = generation_queue_metrics(session)
        emit_emf_metrics(
            {
                "QueuedGenerations": queue.queued,
                "RunningGenerations": queue.running,
                "OldestQueuedGenerationAgeSeconds": queue.oldest_queued_age_seconds,
                "RecoveredGenerations": recovered_total,
            },
            dimensions={
                "Service": "generation-worker",
                "Environment": settings.app_env,
            },
            units={"OldestQueuedGenerationAgeSeconds": "Seconds"},
        )
    except Exception:
        logger.exception("Failed to recover expired generation jobs")
    schedule.next_recovery = (
        monotonic_now
        if recovery_saturated
        else monotonic_now + schedule.recovery_interval
    )


def _claim_once(
    *,
    session_factory: SessionFactory,
    worker_id: str,
    stop: StopEvent,
) -> bool:
    try:
        return process_next_generation_job(
            session_factory=session_factory,
            worker_id=worker_id,
            shutdown_requested=stop.is_set,
        )
    except Exception:
        logger.exception("Generation worker iteration failed")
        return False


def _run_worker_serially(
    *,
    once: bool,
    worker_id: str,
    session_factory: SessionFactory,
    stop: StopEvent,
) -> None:
    schedule = _MaintenanceSchedule()
    while True:
        if stop.is_set():
            logger.info(
                "Shutdown requested; generation worker %s will not claim another job",
                worker_id,
            )
            return
        _maintenance_cycle(schedule, session_factory=session_factory, stop=stop)
        if stop.is_set():
            return
        processed = _claim_once(
            session_factory=session_factory,
            worker_id=worker_id,
            stop=stop,
        )
        if once:
            return
        if not processed:
            stop.wait(settings.generation_job_poll_seconds)


def _run_worker_slots(
    *,
    worker_id: str,
    concurrency: int,
    session_factory: SessionFactory,
    stop: StopEvent,
) -> None:
    composite = _CompositeStopEvent(stop)
    fatal: list[BaseException] = []
    fatal_lock = threading.Lock()

    def coordinate() -> None:
        schedule = _MaintenanceSchedule()
        while not composite.is_set():
            _maintenance_cycle(
                schedule,
                session_factory=session_factory,
                stop=composite,
            )
            composite.wait(settings.generation_job_poll_seconds)

    def claim(slot_worker_id: str) -> None:
        while not composite.is_set():
            try:
                processed = _claim_once(
                    session_factory=session_factory,
                    worker_id=slot_worker_id,
                    stop=composite,
                )
            except BaseException as exc:
                with fatal_lock:
                    fatal.append(exc)
                composite.set()
                return
            if not processed:
                composite.wait(settings.generation_job_poll_seconds)
        logger.info(
            "Shutdown requested; generation worker %s will not claim another job",
            slot_worker_id,
        )

    threads = [threading.Thread(target=coordinate, daemon=True)]
    threads.extend(
        threading.Thread(
            target=claim,
            args=(f"{worker_id}:slot-{index}",),
            daemon=True,
        )
        for index in range(concurrency)
    )
    for thread in threads:
        thread.start()
    try:
        while any(thread.is_alive() for thread in threads):
            if composite.is_set():
                break
            composite.wait(0.1)
    finally:
        composite.set()
        deadline = time.monotonic() + (
            settings.generation_job_attempt_timeout_seconds + HEARTBEAT_SHUTDOWN_SECONDS
        )
        for thread in threads:
            thread.join(timeout=max(0.0, deadline - time.monotonic()))

    with fatal_lock:
        if fatal:
            raise fatal[0]


def run_worker(
    *,
    once: bool,
    worker_id: str | None = None,
    stop_event: StopEvent | None = None,
    session_factory: SessionFactory = SessionLocal,
    storage: Storage | None = None,
    concurrency: int = 1,
) -> None:
    if concurrency < 1:
        raise ValueError("concurrency must be positive")
    stop = stop_event or threading.Event()
    if stop.is_set():
        return
    if storage is None:
        storage = get_storage()
    check_worker_ready(session_factory=session_factory, storage=storage)
    if stop.is_set():
        return

    worker_id = worker_id or _default_worker_id()
    if once:
        concurrency = 1

    logger.info("Generation worker %s started", worker_id)
    if concurrency > 1:
        _run_worker_slots(
            worker_id=worker_id,
            concurrency=concurrency,
            session_factory=session_factory,
            stop=stop,
        )
    else:
        _run_worker_serially(
            once=once,
            worker_id=worker_id,
            session_factory=session_factory,
            stop=stop,
        )
    logger.info("Generation worker %s stopped", worker_id)


def _install_shutdown_handlers(stop_event: _SignalStopEvent) -> None:
    def request_shutdown(_signum: int, _frame) -> None:
        stop_event.requested = True

    signal.signal(signal.SIGTERM, request_shutdown)
    signal.signal(signal.SIGINT, request_shutdown)


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run queued AI generations")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true")
    mode.add_argument(
        "--check",
        action="store_true",
        help="Verify the worker's dependencies and exit",
    )
    parser.add_argument("--worker-id", default=None)
    args = parser.parse_args(argv)
    configure_logging(service="generation-worker", environment=settings.app_env)
    if args.check:
        try:
            check_worker_ready()
        except ReadinessError as exc:
            logger.error("Generation worker readiness check failed: %s", exc)
            raise SystemExit(1) from None
        logger.info("Generation worker readiness check succeeded")
        return

    stop_event = _SignalStopEvent()
    _install_shutdown_handlers(stop_event)
    try:
        run_worker(
            once=args.once,
            worker_id=args.worker_id,
            stop_event=stop_event,
            concurrency=settings.generation_job_concurrency,
        )
    except ReadinessError as exc:
        logger.error("Generation worker readiness check failed: %s", exc)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
