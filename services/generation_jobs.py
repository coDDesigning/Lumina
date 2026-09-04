"""Durable, fenced state transitions for backgrounded AI generation jobs.

A generation is a single provider call rather than the multi-stage pipeline
``services/processing_jobs.py`` drives, so this module keeps that module's
fenced-claim vocabulary — claim token, lease, heartbeat, expiry recovery — and
drops the staging. It shares the same durable clock and write serialisation on
purpose: two job tables that disagree about what "now" means cannot be reasoned
about together.

The one rule that is not in the document queue: a student may only occupy so
many running slots at once. The limit is applied when a job is claimed, never
when it is enqueued, so a third request is accepted and waits rather than being
refused work the student has already paid for.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from collections.abc import Callable
from uuid import uuid4

from sqlalchemy import case, func, select, update
from sqlalchemy.orm import Session, aliased, selectinload

from backend.app.config import settings
from backend.app.models import (
    GENERATION_JOB_TYPES,
    JOB_STATUS_FAILED,
    JOB_STATUS_QUEUED,
    JOB_STATUS_RUNNING,
    JOB_STATUS_SUCCEEDED,
    JOB_TYPE_GENERATE_FLASHCARD,
    JOB_TYPE_GENERATE_QUIZ,
    JOB_TYPE_GENERATE_STUDY_GUIDE,
    Course,
    GenerationJob,
    User,
)
from backend.app.observability import get_request_id

# The durable clock and the write serialisation are shared with the document
# queue rather than reimplemented, so both tables order their transitions
# against exactly one notion of the current time.
from services.processing_jobs import _database_now, _start_transition
from services.credits import ChargeReceipt, CreditService
from utils.ai_errors import InsufficientCreditsError

# How far back a finished job stays visible to the client that is rebuilding its
# panel. It bounds the list a course read has to return; the rows themselves are
# never deleted here, because a generation's result outlives the notification.
RECENT_WINDOW_SECONDS = 24 * 60 * 60
MAX_LISTED_JOBS = 50
ResultWriter = Callable[[], tuple[int | None, int | None]]

# The ledger name every job type is charged under. It is one table rather than a
# literal at each charge site, and it is asserted complete at import: a job type
# the queue accepts but cannot price is a failed enqueue for work the student
# asked for, and that must surface at startup instead of per request.
CREDIT_SOURCE_TYPES: dict[str, str] = {
    JOB_TYPE_GENERATE_STUDY_GUIDE: "study_guide",
    JOB_TYPE_GENERATE_QUIZ: "quiz",
    JOB_TYPE_GENERATE_FLASHCARD: "flashcard",
}
if set(CREDIT_SOURCE_TYPES) != set(GENERATION_JOB_TYPES):
    raise RuntimeError(
        "Every generation job type needs a credit source: missing "
        f"{sorted(set(GENERATION_JOB_TYPES) - set(CREDIT_SOURCE_TYPES))}"
    )


class GenerationJobStateError(RuntimeError):
    """A requested transition is not valid for the current durable state."""


class InsufficientCreditsForGenerationError(InsufficientCreditsError):
    """The account cannot cover the generation, so nothing was enqueued."""


class GenerationJobNotRetryableError(RuntimeError):
    """Only a terminal failed generation can be retried."""


class GenerationJobNotDismissableError(RuntimeError):
    """Only a generation that has finished can be cleared from the panel."""


@dataclass(frozen=True, slots=True)
class ClaimedGenerationJob:
    """Everything a worker slot needs to run one generation off its own session."""

    id: int
    course_id: int
    user_id: int
    job_type: str
    request_payload: str
    claim_token: str
    attempt_count: int
    max_attempts: int
    charge_amount: float | None
    charge_transaction_id: int | None
    correlation_id: str | None

    @property
    def charge_receipt(self) -> ChargeReceipt | None:
        """The charge to reverse, rebuilt from the row rather than the request.

        ``None`` means no charge was taken at all. An exempt account produces a
        receipt whose transaction id is ``None``, which ``CreditService.refund``
        already treats as nothing to reverse.
        """
        if self.charge_amount is None:
            return None
        return ChargeReceipt(
            user_id=self.user_id,
            amount=self.charge_amount,
            transaction_id=self.charge_transaction_id,
        )


@dataclass(frozen=True, slots=True)
class GenerationQueueMetrics:
    queued: int
    running: int
    oldest_queued_age_seconds: float


def _public_error_message(message: str) -> str:
    normalized = " ".join(message.replace("\x00", "").split())
    return (normalized or "Generation failed.")[:500]


def _clear_lease(job: GenerationJob) -> None:
    job.lease_owner = None
    job.claim_token = None
    job.claimed_at = None
    job.heartbeat_at = None
    job.lease_expires_at = None


def enqueue_generation_job(
    session: Session,
    *,
    course_id: int,
    user_id: int,
    job_type: str,
    request_payload: str,
    credit_cost: float,
    correlation_id: str | None = None,
    max_attempts: int | None = None,
    now: datetime | None = None,
) -> GenerationJob:
    """Charge for one generation and record it as queued work.

    The charge is taken here, by the request the student is waiting on, so an
    account that cannot cover the generation is told immediately instead of
    discovering it from a job that failed minutes later. From this point the
    receipt lives on the row: every path that abandons the work — the worker,
    the expiry sweep — reverses it from there.
    """
    if job_type not in GENERATION_JOB_TYPES:
        raise ValueError(f"Unknown generation job type: {job_type}")
    if max_attempts is None:
        max_attempts = settings.generation_job_max_attempts
    if max_attempts <= 0:
        raise ValueError("max_attempts must be positive")
    if correlation_id is None:
        correlation_id = get_request_id()

    # The lazy monthly grant may commit, so settle it before opening the
    # serialized transaction that must contain both the charge and the job.
    CreditService.ensure_current_period_grant(session, user_id)
    _start_transition(session)
    receipt = CreditService.charge(
        session,
        user_id,
        credit_cost,
        source_type=CREDIT_SOURCE_TYPES[job_type],
        commit=False,
    )
    if receipt is None:
        session.rollback()
        raise InsufficientCreditsForGenerationError("Insufficient credits.")

    try:
        available_at = _database_now(session, now)
        job = GenerationJob(
            course_id=course_id,
            user_id=user_id,
            job_type=job_type,
            correlation_id=correlation_id,
            request_payload=request_payload,
            status=JOB_STATUS_QUEUED,
            attempt_count=0,
            max_attempts=max_attempts,
            available_at=available_at,
            charge_amount=receipt.amount,
            charge_transaction_id=receipt.transaction_id,
            charge_refunded=False,
        )
        session.add(job)
        session.commit()
    except Exception:
        # The charge and the queued row are one transaction. A process can never
        # die between paying for work and recording the work it paid for.
        session.rollback()
        raise

    session.refresh(job)
    return job


def retry_generation_job(
    session: Session,
    *,
    course_id: int,
    user_id: int,
    job_id: int,
    now: datetime | None = None,
) -> GenerationJob | None:
    """Clone a failed job once, charging the same price as the original request."""
    CreditService.ensure_current_period_grant(session, user_id)
    _start_transition(session)
    statement = select(GenerationJob).where(
        GenerationJob.id == job_id,
        GenerationJob.course_id == course_id,
        GenerationJob.user_id == user_id,
    )
    if session.get_bind().dialect.name == "postgresql":
        statement = statement.with_for_update(of=GenerationJob)
    original = session.scalar(statement)
    if original is None:
        session.rollback()
        return None
    if original.status != JOB_STATUS_FAILED:
        session.rollback()
        raise GenerationJobNotRetryableError("Only a failed generation can be retried.")

    existing = session.scalar(
        select(GenerationJob).where(GenerationJob.retry_of_job_id == original.id)
    )
    if existing is not None:
        if original.dismissed_at is None:
            original.dismissed_at = _database_now(session, now)
            session.commit()
        else:
            session.rollback()
        return existing
    if original.charge_amount is None:
        session.rollback()
        raise GenerationJobNotRetryableError(
            "The original generation has no reusable price."
        )

    receipt = CreditService.charge(
        session,
        user_id,
        original.charge_amount,
        source_type=CREDIT_SOURCE_TYPES[original.job_type],
        commit=False,
    )
    if receipt is None:
        session.rollback()
        raise InsufficientCreditsForGenerationError("Insufficient credits.")

    retried = GenerationJob(
        course_id=course_id,
        user_id=user_id,
        job_type=original.job_type,
        correlation_id=get_request_id(),
        request_payload=original.request_payload,
        status=JOB_STATUS_QUEUED,
        attempt_count=0,
        max_attempts=settings.generation_job_max_attempts,
        available_at=_database_now(session, now),
        charge_amount=receipt.amount,
        charge_transaction_id=receipt.transaction_id,
        charge_refunded=False,
        retry_of_job_id=original.id,
    )
    original.dismissed_at = _database_now(session, now)
    session.add(retried)
    session.commit()
    session.refresh(retried)
    return retried


def dismiss_generation_job(
    session: Session,
    *,
    course_id: int,
    user_id: int,
    job_id: int,
    now: datetime | None = None,
) -> GenerationJob | None:
    """Clear one finished generation out of the student's panel for good.

    A failure the student has read otherwise sits there for the whole recent
    window with a retry button that has already been taken, so dismissal is what
    makes the panel a list of work rather than a list of history. Only a run
    that has finished may be cleared: hiding one still queued would leave the
    student watching for a result nothing is going to show them.
    """
    _start_transition(session)
    job = session.scalar(
        select(GenerationJob).where(
            GenerationJob.id == job_id,
            GenerationJob.course_id == course_id,
            GenerationJob.user_id == user_id,
        )
    )
    if job is None:
        session.rollback()
        return None
    if job.status not in (JOB_STATUS_SUCCEEDED, JOB_STATUS_FAILED):
        session.rollback()
        raise GenerationJobNotDismissableError(
            "Only a finished generation can be dismissed."
        )
    if job.dismissed_at is None:
        job.dismissed_at = _database_now(session, now)
        session.commit()
    else:
        session.rollback()
    return job


def generation_queue_metrics(
    session: Session,
    *,
    now: datetime | None = None,
) -> GenerationQueueMetrics:
    """Queue depth for the worker's metrics log, in one round trip."""
    measured_at = _database_now(session, now)
    row = session.execute(
        select(
            func.count().filter(GenerationJob.status == JOB_STATUS_QUEUED),
            func.count().filter(GenerationJob.status == JOB_STATUS_RUNNING),
            func.min(
                case(
                    (
                        GenerationJob.status == JOB_STATUS_QUEUED,
                        GenerationJob.available_at,
                    )
                )
            ),
        )
    ).one()
    queued, running, oldest_available_at = row
    if oldest_available_at is None:
        oldest_age = 0.0
    else:
        if oldest_available_at.tzinfo is None:
            oldest_available_at = oldest_available_at.replace(tzinfo=timezone.utc)
        oldest_age = max(
            0.0,
            (
                measured_at - oldest_available_at.astimezone(timezone.utc)
            ).total_seconds(),
        )
    return GenerationQueueMetrics(
        queued=int(queued or 0),
        running=int(running or 0),
        oldest_queued_age_seconds=oldest_age,
    )


def claim_next_generation_job(
    session: Session,
    worker_id: str,
    lease_seconds: int,
    *,
    max_active_per_user: int | None = None,
    now: datetime | None = None,
) -> ClaimedGenerationJob | None:
    """Take the oldest queued generation whose owner has a free slot.

    The per-user ceiling is enforced here rather than at enqueue, which is what
    makes a third request wait instead of being refused. It is evaluated inside
    the claiming transaction and re-asserted by the guarded ``UPDATE``, so two
    slots racing for one student's third job cannot both win.
    """
    worker_id = worker_id.strip()
    if not worker_id:
        raise ValueError("worker_id must not be empty")
    if lease_seconds <= 0:
        raise ValueError("lease_seconds must be positive")
    if max_active_per_user is None:
        max_active_per_user = settings.generation_job_max_active_per_user
    if max_active_per_user <= 0:
        raise ValueError("max_active_per_user must be positive")

    _start_transition(session)
    eligibility_time = _database_now(session, now)
    dialect_name = session.get_bind().dialect.name

    # The inner count is over the same table as the outer select, so it needs an
    # alias of its own: left to auto-correlate it would resolve both sides to
    # the candidate row and count nothing.
    running = aliased(GenerationJob)
    running_for_owner = (
        select(func.count())
        .select_from(running)
        .where(
            running.user_id == GenerationJob.user_id,
            running.status == JOB_STATUS_RUNNING,
        )
        .correlate(GenerationJob)
        .scalar_subquery()
    )
    statement = (
        select(GenerationJob.id)
        .join(Course, Course.id == GenerationJob.course_id)
        .where(
            GenerationJob.status == JOB_STATUS_QUEUED,
            GenerationJob.available_at <= eligibility_time,
            GenerationJob.attempt_count < GenerationJob.max_attempts,
            Course.is_deleted.is_(False),
            running_for_owner < max_active_per_user,
        )
        .order_by(GenerationJob.available_at, GenerationJob.id)
        .limit(1)
    )
    if dialect_name == "postgresql":
        statement = statement.with_for_update(of=GenerationJob, skip_locked=True)

    job_id = session.scalar(statement)
    if job_id is None:
        session.rollback()
        return None

    user_id = session.scalar(
        select(GenerationJob.user_id).where(GenerationJob.id == job_id)
    )
    if user_id is None:
        session.rollback()
        return None
    # Different slots can select different queued rows for the same student.
    # Locking that student's row serializes the final slot check on PostgreSQL;
    # SQLite is already serialized by ``_start_transition``.
    session.scalar(select(User.id).where(User.id == user_id).with_for_update())
    running_count = session.scalar(
        select(func.count())
        .select_from(GenerationJob)
        .where(
            GenerationJob.user_id == user_id,
            GenerationJob.status == JOB_STATUS_RUNNING,
        )
    )
    if int(running_count or 0) >= max_active_per_user:
        session.rollback()
        return None

    row = session.execute(
        select(
            GenerationJob.id,
            GenerationJob.course_id,
            GenerationJob.user_id,
            GenerationJob.job_type,
            GenerationJob.request_payload,
            GenerationJob.attempt_count,
            GenerationJob.max_attempts,
            GenerationJob.charge_amount,
            GenerationJob.charge_transaction_id,
            GenerationJob.correlation_id,
        ).where(GenerationJob.id == job_id)
    ).one_or_none()
    if row is None:
        session.rollback()
        return None

    claimed_at = _database_now(session, now)
    lease_expires_at = claimed_at + timedelta(seconds=lease_seconds)
    claim_token = str(uuid4())
    result = session.execute(
        update(GenerationJob)
        .where(
            GenerationJob.id == job_id,
            GenerationJob.status == JOB_STATUS_QUEUED,
            GenerationJob.available_at <= claimed_at,
            GenerationJob.attempt_count < GenerationJob.max_attempts,
        )
        .values(
            status=JOB_STATUS_RUNNING,
            attempt_count=GenerationJob.attempt_count + 1,
            started_at=case(
                (GenerationJob.started_at.is_(None), claimed_at),
                else_=GenerationJob.started_at,
            ),
            claimed_at=claimed_at,
            heartbeat_at=claimed_at,
            lease_expires_at=lease_expires_at,
            lease_owner=worker_id[:255],
            claim_token=claim_token,
            finished_at=None,
            last_error_code=None,
            last_error_message=None,
            updated_at=claimed_at,
        )
    )
    if result.rowcount != 1:
        session.rollback()
        return None

    session.commit()
    return ClaimedGenerationJob(
        id=row.id,
        course_id=row.course_id,
        user_id=row.user_id,
        job_type=row.job_type,
        request_payload=row.request_payload,
        claim_token=claim_token,
        attempt_count=row.attempt_count + 1,
        max_attempts=row.max_attempts,
        charge_amount=row.charge_amount,
        charge_transaction_id=row.charge_transaction_id,
        correlation_id=row.correlation_id,
    )


def heartbeat_generation_job(
    session: Session,
    job_id: int,
    claim_token: str,
    lease_seconds: int,
    *,
    now: datetime | None = None,
) -> bool:
    """Extend one lease, and only for the slot still holding its claim token."""
    if lease_seconds <= 0:
        raise ValueError("lease_seconds must be positive")

    _start_transition(session)
    beat_at = _database_now(session, now)
    result = session.execute(
        update(GenerationJob)
        .where(
            GenerationJob.id == job_id,
            GenerationJob.status == JOB_STATUS_RUNNING,
            GenerationJob.claim_token == claim_token,
            GenerationJob.lease_expires_at > beat_at,
        )
        .values(
            heartbeat_at=beat_at,
            lease_expires_at=beat_at + timedelta(seconds=lease_seconds),
            updated_at=beat_at,
        )
    )
    if result.rowcount != 1:
        session.rollback()
        return False
    session.commit()
    return True


def complete_generation_job(
    session: Session,
    job_id: int,
    claim_token: str,
    *,
    generated_output_id: int | None = None,
    quiz_id: int | None = None,
    result_writer: ResultWriter | None = None,
    now: datetime | None = None,
) -> None:
    """Record the artifact this run produced and release the lease.

    The result is required: a generation that persisted nothing has not
    succeeded, whatever the provider returned.
    """
    if result_writer is not None and (
        generated_output_id is not None or quiz_id is not None
    ):
        raise ValueError("Result ids and result_writer are mutually exclusive")
    if result_writer is None and (generated_output_id is None) == (quiz_id is None):
        raise ValueError(
            "A completed generation records exactly one of "
            "generated_output_id or quiz_id"
        )

    _start_transition(session)
    finished_at = _database_now(session, now)
    job = session.get(GenerationJob, job_id, with_for_update=True)
    if job is None:
        session.rollback()
        raise GenerationJobStateError(f"Generation job {job_id} does not exist")
    if (
        job.status != JOB_STATUS_RUNNING
        or job.claim_token != claim_token
        or job.lease_expires_at is None
        or job.lease_expires_at <= finished_at
    ):
        session.rollback()
        raise GenerationJobStateError(
            f"Generation job {job_id} is not held by this claim"
        )

    if result_writer is not None:
        generated_output_id, quiz_id = result_writer()
    if (generated_output_id is None) == (quiz_id is None):
        session.rollback()
        raise ValueError(
            "A completed generation records exactly one of "
            "generated_output_id or quiz_id"
        )

    job.status = JOB_STATUS_SUCCEEDED
    job.generated_output_id = generated_output_id
    job.quiz_id = quiz_id
    job.finished_at = finished_at
    job.last_error_code = None
    job.last_error_message = None
    job.updated_at = finished_at
    _clear_lease(job)
    session.commit()


def fail_generation_job(
    session: Session,
    job_id: int,
    claim_token: str,
    *,
    error_code: str,
    error_message: str,
    retryable: bool,
    already_refunded: bool = False,
    retry_delay_seconds: float = 0,
    now: datetime | None = None,
) -> str | None:
    """Fail or requeue one run, giving the credit back when it is really over.

    ``already_refunded`` is how a caller reports that the generation service has
    reversed the charge on its own way out; the refund is idempotent either way,
    but the flag keeps the row honest about it. A requeued attempt keeps the
    charge, because the work the student paid for is still going to happen.
    """
    sanitized_error_code = error_code.replace("\x00", "").strip()
    if not sanitized_error_code:
        raise ValueError("error_code must not be empty")

    _start_transition(session)
    failed_at = _database_now(session, now)
    job = session.get(GenerationJob, job_id, with_for_update=True)
    if job is None:
        session.rollback()
        raise GenerationJobStateError(f"Generation job {job_id} does not exist")
    if (
        job.status != JOB_STATUS_RUNNING
        or job.claim_token != claim_token
        or job.lease_expires_at is None
        or job.lease_expires_at <= failed_at
    ):
        session.rollback()
        raise GenerationJobStateError(
            f"Generation job {job_id} is not held by this claim"
        )

    should_retry = retryable and job.attempt_count < job.max_attempts
    job.status = JOB_STATUS_QUEUED if should_retry else JOB_STATUS_FAILED
    job.available_at = (
        failed_at + timedelta(seconds=max(0.0, retry_delay_seconds))
        if should_retry
        else failed_at
    )
    job.finished_at = None if should_retry else failed_at
    job.last_error_code = sanitized_error_code[:100]
    job.last_error_message = _public_error_message(error_message)
    job.updated_at = failed_at
    _clear_lease(job)

    receipt = None
    if not should_retry and not job.charge_refunded and job.charge_amount is not None:
        receipt = ChargeReceipt(
            user_id=job.user_id,
            amount=job.charge_amount,
            transaction_id=job.charge_transaction_id,
        )
        CreditService.refund(session, receipt, commit=False)
        job.charge_refunded = True
    final_status = job.status
    session.commit()
    return final_status


def recover_expired_generation_jobs(
    session: Session,
    *,
    limit: int = 100,
    now: datetime | None = None,
) -> int:
    """Reclaim runs whose worker died, so nothing stays running forever.

    This is the only path that can end a job nobody is holding, which is what
    keeps a killed process from leaving a student watching a spinner that will
    never resolve. A run that has attempts left is requeued and keeps its
    charge; one that is out of attempts is failed and refunded.
    """
    if limit <= 0:
        raise ValueError("limit must be positive")

    _start_transition(session)
    recovered_at = _database_now(session, now)
    statement = (
        select(GenerationJob)
        .where(
            GenerationJob.status == JOB_STATUS_RUNNING,
            GenerationJob.lease_expires_at <= recovered_at,
        )
        .order_by(GenerationJob.id)
        .limit(limit)
    )
    if session.get_bind().dialect.name == "postgresql":
        statement = statement.with_for_update(of=GenerationJob, skip_locked=True)
    jobs = list(session.scalars(statement))
    if not jobs:
        session.rollback()
        return 0

    for job in jobs:
        should_retry = job.attempt_count < job.max_attempts
        job.status = JOB_STATUS_QUEUED if should_retry else JOB_STATUS_FAILED
        job.available_at = recovered_at
        job.finished_at = None if should_retry else recovered_at
        job.last_error_code = "LEASE_EXPIRED"
        job.last_error_message = "The worker lease expired before completion."
        job.updated_at = recovered_at
        _clear_lease(job)
        if (
            not should_retry
            and not job.charge_refunded
            and job.charge_amount is not None
        ):
            receipt = ChargeReceipt(
                user_id=job.user_id,
                amount=job.charge_amount,
                transaction_id=job.charge_transaction_id,
            )
            CreditService.refund(session, receipt, commit=False)
            job.charge_refunded = True

    recovered = len(jobs)
    session.commit()

    return recovered


def cancel_course_generation_jobs(
    session: Session,
    course_id: int,
    *,
    now: datetime | None = None,
) -> int:
    """Fence and refund unfinished work before a course is permanently deleted."""
    cancelled_at = _database_now(session, now)
    statement = (
        select(GenerationJob)
        .where(
            GenerationJob.course_id == course_id,
            GenerationJob.status.in_((JOB_STATUS_QUEUED, JOB_STATUS_RUNNING)),
        )
        .order_by(GenerationJob.id)
    )
    if session.get_bind().dialect.name == "postgresql":
        statement = statement.with_for_update(of=GenerationJob)

    jobs = list(session.scalars(statement))
    for job in jobs:
        job.status = JOB_STATUS_FAILED
        job.available_at = cancelled_at
        job.finished_at = cancelled_at
        job.last_error_code = "generation_failed"
        job.last_error_message = "The course was deleted before generation completed."
        job.updated_at = cancelled_at
        _clear_lease(job)
        if not job.charge_refunded and job.charge_amount is not None:
            CreditService.refund(
                session,
                ChargeReceipt(
                    user_id=job.user_id,
                    amount=job.charge_amount,
                    transaction_id=job.charge_transaction_id,
                ),
                commit=False,
            )
            job.charge_refunded = True
    return len(jobs)


def list_course_generation_jobs(
    session: Session,
    course_id: int,
    user_id: int,
    *,
    limit: int = MAX_LISTED_JOBS,
    now: datetime | None = None,
) -> list[GenerationJob]:
    """The panel's contents: this student's unfinished and recent runs.

    Scoped to the requesting account rather than the course, because a shared
    course must not show one student the generations another student paid for.
    """
    if limit <= 0:
        raise ValueError("limit must be positive")

    read_at = _database_now(session, now)
    cutoff = read_at - timedelta(seconds=RECENT_WINDOW_SECONDS)
    statement = (
        select(GenerationJob)
        .options(
            selectinload(GenerationJob.quiz),
            selectinload(GenerationJob.generated_output),
        )
        .where(
            GenerationJob.course_id == course_id,
            GenerationJob.user_id == user_id,
            GenerationJob.dismissed_at.is_(None),
            (GenerationJob.status.in_((JOB_STATUS_QUEUED, JOB_STATUS_RUNNING)))
            | (GenerationJob.finished_at >= cutoff),
        )
        .order_by(GenerationJob.created_at.desc(), GenerationJob.id.desc())
        .limit(min(limit, MAX_LISTED_JOBS))
    )
    return list(session.scalars(statement))


def get_generation_job(
    session: Session,
    course_id: int,
    user_id: int,
    job_id: int,
) -> GenerationJob | None:
    """One job, scoped so a job id cannot be read out of another course."""
    return session.scalar(
        select(GenerationJob).where(
            GenerationJob.id == job_id,
            GenerationJob.course_id == course_id,
            GenerationJob.user_id == user_id,
        )
    )
