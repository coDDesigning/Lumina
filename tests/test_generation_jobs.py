"""The durable state machine behind a backgrounded generation.

These tests are about the row, not the feature: what a claim is allowed to take,
what an abandoned run costs the student, and what a client rebuilding its panel
is allowed to see.
"""

from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from backend.app.models import (
    JOB_STATUS_FAILED,
    JOB_STATUS_QUEUED,
    JOB_STATUS_RUNNING,
    JOB_STATUS_SUCCEEDED,
    JOB_TYPE_GENERATE_QUIZ,
    JOB_TYPE_GENERATE_STUDY_GUIDE,
    JOB_TYPE_GENERATE_FLASHCARD,
    Course,
    GeneratedOutput,
    GenerationJob,
    Role,
    User,
)
from services.generation_jobs import (
    GenerationJobStateError,
    InsufficientCreditsForGenerationError,
    claim_next_generation_job,
    complete_generation_job,
    enqueue_generation_job,
    fail_generation_job,
    generation_queue_metrics,
    get_generation_job,
    heartbeat_generation_job,
    list_course_generation_jobs,
    recover_expired_generation_jobs,
    retry_generation_job,
)

from tests.conftest import assert_balance_is_derivable, seed_registration_grant
from workers import generation_processor

LEASE_SECONDS = 60


def _make_user(session: Session, email: str, credits: float | None) -> User:
    role = session.scalar(select(Role).where(Role.name == "user"))
    assert role is not None
    user = User(
        name=email.split("@")[0],
        email=email,
        password_hash="not-used-by-these-tests",
        role=role,
        credits=credits,
        is_banned=False,
    )
    seed_registration_grant(user)
    session.add(user)
    session.commit()
    if credits is not None:
        # The registration grant would otherwise decide the balance; these tests
        # pick one so a charge either fits or does not.
        user.credits = credits
        session.commit()
    return user


def _make_course(session: Session, owner: User, title: str) -> Course:
    course = Course(
        title=title,
        description=None,
        semester="Fall",
        exam_date=date(2026, 6, 15),
        owner=owner,
        is_deleted=False,
    )
    session.add(course)
    session.commit()
    return course


def _enqueue(
    session: Session,
    course: Course,
    user: User,
    *,
    job_type: str = JOB_TYPE_GENERATE_STUDY_GUIDE,
    payload: str = '{"topic_focus": "Chapter 1"}',
    cost: float = 1.0,
    max_attempts: int | None = None,
) -> GenerationJob:
    return enqueue_generation_job(
        session,
        course_id=course.id,
        user_id=user.id,
        job_type=job_type,
        request_payload=payload,
        credit_cost=cost,
        max_attempts=max_attempts,
    )


@pytest.fixture
def owner(db_session: Session) -> User:
    return _make_user(db_session, "generation-owner@example.com", credits=100.0)


@pytest.fixture
def course(db_session: Session, owner: User) -> Course:
    return _make_course(db_session, owner, "Generation Course")


def test_enqueue_charges_and_records_queued_work(
    db_session: Session, owner: User, course: Course
) -> None:
    before = owner.credits

    job = _enqueue(db_session, course, owner)

    assert job.status == JOB_STATUS_QUEUED
    assert job.attempt_count == 0
    assert job.finished_at is None
    assert job.claim_token is None
    assert job.charge_amount == pytest.approx(1.0)
    assert job.charge_transaction_id is not None
    assert job.charge_refunded is False
    db_session.refresh(owner)
    assert owner.credits == pytest.approx(before - 1.0)
    assert_balance_is_derivable(db_session, owner.id)


def test_worker_persists_the_result_and_completion_atomically(
    db_session: Session,
    session_factory: sessionmaker[Session],
    owner: User,
    course: Course,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queued = _enqueue(db_session, course, owner)

    def run(_session: Session, _job) -> generation_processor.ResultPersister:
        def persist(result_session: Session) -> tuple[int | None, int | None]:
            output = GeneratedOutput(
                course_id=course.id,
                user_id=owner.id,
                model_used="test-model",
                output_type="study_guide",
                content='{"title":"Durable guide"}',
            )
            result_session.add(output)
            result_session.flush()
            return output.id, None

        return persist

    monkeypatch.setitem(
        generation_processor.RUNNERS,
        JOB_TYPE_GENERATE_STUDY_GUIDE,
        run,
    )

    assert generation_processor.process_next_generation_job(
        session_factory=session_factory,
        worker_id="generation-test-worker",
        lease_seconds=LEASE_SECONDS,
    )

    db_session.expire_all()
    completed = db_session.get(GenerationJob, queued.id)
    assert completed is not None
    assert completed.status == JOB_STATUS_SUCCEEDED
    assert completed.generated_output_id is not None
    assert db_session.get(GeneratedOutput, completed.generated_output_id) is not None


def test_worker_rolls_back_an_artifact_when_completion_fails(
    db_session: Session,
    session_factory: sessionmaker[Session],
    owner: User,
    course: Course,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    starting_balance = owner.credits
    queued = _enqueue(db_session, course, owner, max_attempts=1)

    def run(_session: Session, _job) -> generation_processor.ResultPersister:
        def persist(result_session: Session) -> tuple[int | None, int | None]:
            result_session.add(
                GeneratedOutput(
                    course_id=course.id,
                    user_id=owner.id,
                    model_used="test-model",
                    output_type="study_guide",
                    content='{"title":"Must roll back"}',
                )
            )
            result_session.flush()
            raise RuntimeError("persistence failed")

        return persist

    monkeypatch.setitem(
        generation_processor.RUNNERS,
        JOB_TYPE_GENERATE_STUDY_GUIDE,
        run,
    )

    assert generation_processor.process_next_generation_job(
        session_factory=session_factory,
        worker_id="generation-test-worker",
        lease_seconds=LEASE_SECONDS,
    )

    db_session.expire_all()
    failed = db_session.get(GenerationJob, queued.id)
    assert failed is not None
    assert failed.status == JOB_STATUS_FAILED
    assert db_session.scalars(select(GeneratedOutput)).all() == []
    refreshed_owner = db_session.get(User, owner.id)
    assert refreshed_owner is not None
    assert refreshed_owner.credits == pytest.approx(starting_balance)
    assert_balance_is_derivable(db_session, owner.id)


def test_enqueue_refuses_an_account_that_cannot_cover_the_generation(
    db_session: Session, course: Course
) -> None:
    """A refused charge leaves no job, so nothing is queued that nobody paid for."""
    broke = _make_user(db_session, "broke@example.com", credits=0.5)
    broke_course = _make_course(db_session, broke, "Broke Course")

    with pytest.raises(InsufficientCreditsForGenerationError):
        _enqueue(db_session, broke_course, broke, cost=1.0)

    assert db_session.scalars(select(GenerationJob)).all() == []
    db_session.refresh(broke)
    assert broke.credits == pytest.approx(0.5)


def test_enqueue_is_free_for_an_exempt_account(db_session: Session) -> None:
    """An unmetered account still gets a job, and a receipt with nothing to reverse."""
    exempt = _make_user(db_session, "exempt@example.com", credits=None)
    exempt_course = _make_course(db_session, exempt, "Exempt Course")

    job = _enqueue(db_session, exempt_course, exempt)

    assert job.status == JOB_STATUS_QUEUED
    assert job.charge_amount == pytest.approx(1.0)
    assert job.charge_transaction_id is None


def test_claim_takes_the_oldest_queued_job(
    db_session: Session, owner: User, course: Course
) -> None:
    first = _enqueue(db_session, course, owner)
    second = _enqueue(db_session, course, owner)

    claimed = claim_next_generation_job(db_session, "worker-1", LEASE_SECONDS)

    assert claimed is not None
    assert claimed.id == first.id
    assert claimed.id != second.id
    assert claimed.attempt_count == 1
    assert claimed.charge_receipt is not None
    assert claimed.charge_receipt.amount == pytest.approx(1.0)

    db_session.expire_all()
    row = db_session.get(GenerationJob, first.id)
    assert row is not None
    assert row.status == JOB_STATUS_RUNNING
    assert row.claim_token == claimed.claim_token
    assert row.lease_owner == "worker-1"
    assert row.started_at is not None


def test_claim_holds_a_third_job_until_a_slot_frees(
    db_session: Session, owner: User, course: Course
) -> None:
    """The per-student ceiling queues the overflow rather than refusing it.

    This is the whole reason the limit lives in the claim: all three jobs were
    accepted and charged, and the third only waits for a slot.
    """
    _enqueue(db_session, course, owner)
    _enqueue(db_session, course, owner)
    third = _enqueue(db_session, course, owner)

    first_claim = claim_next_generation_job(
        db_session, "worker-1", LEASE_SECONDS, max_active_per_user=2
    )
    second_claim = claim_next_generation_job(
        db_session, "worker-2", LEASE_SECONDS, max_active_per_user=2
    )
    blocked = claim_next_generation_job(
        db_session, "worker-3", LEASE_SECONDS, max_active_per_user=2
    )

    assert first_claim is not None and second_claim is not None
    assert blocked is None

    db_session.expire_all()
    waiting = db_session.get(GenerationJob, third.id)
    assert waiting is not None
    assert waiting.status == JOB_STATUS_QUEUED

    output = _persisted_output(db_session, course, owner)
    complete_generation_job(
        db_session,
        first_claim.id,
        first_claim.claim_token,
        generated_output_id=output.id,
    )

    now_claimable = claim_next_generation_job(
        db_session, "worker-3", LEASE_SECONDS, max_active_per_user=2
    )
    assert now_claimable is not None
    assert now_claimable.id == third.id


def test_one_students_queue_does_not_block_another(
    db_session: Session, owner: User, course: Course
) -> None:
    """The ceiling is per student, so a busy account cannot starve the rest."""
    _enqueue(db_session, course, owner)
    _enqueue(db_session, course, owner)
    other = _make_user(db_session, "other@example.com", credits=100.0)
    other_course = _make_course(db_session, other, "Other Course")
    other_job = _enqueue(db_session, other_course, other)

    claim_next_generation_job(db_session, "w1", LEASE_SECONDS, max_active_per_user=2)
    claim_next_generation_job(db_session, "w2", LEASE_SECONDS, max_active_per_user=2)
    third = claim_next_generation_job(
        db_session, "w3", LEASE_SECONDS, max_active_per_user=2
    )

    assert third is not None
    assert third.id == other_job.id


def test_claim_skips_a_deleted_course(
    db_session: Session, owner: User, course: Course
) -> None:
    _enqueue(db_session, course, owner)
    course.is_deleted = True
    db_session.commit()

    assert claim_next_generation_job(db_session, "worker-1", LEASE_SECONDS) is None


def _persisted_output(session: Session, course: Course, owner: User) -> GeneratedOutput:
    output = GeneratedOutput(
        course_id=course.id,
        user_id=owner.id,
        output_type="study_guide",
        content="# Result",
    )
    session.add(output)
    session.commit()
    return output


def test_complete_records_the_artifact_the_run_produced(
    db_session: Session, owner: User, course: Course
) -> None:
    _enqueue(db_session, course, owner)
    claimed = claim_next_generation_job(db_session, "worker-1", LEASE_SECONDS)
    assert claimed is not None
    output = _persisted_output(db_session, course, owner)

    complete_generation_job(
        db_session, claimed.id, claimed.claim_token, generated_output_id=output.id
    )

    db_session.expire_all()
    row = db_session.get(GenerationJob, claimed.id)
    assert row is not None
    assert row.status == JOB_STATUS_SUCCEEDED
    assert row.generated_output_id == output.id
    assert row.quiz_id is None
    assert row.finished_at is not None
    assert row.claim_token is None
    assert row.charge_refunded is False


def test_complete_rejects_a_stale_claim_token(
    db_session: Session, owner: User, course: Course
) -> None:
    """Fencing: a worker that lost its lease cannot still report success."""
    _enqueue(db_session, course, owner)
    claimed = claim_next_generation_job(db_session, "worker-1", LEASE_SECONDS)
    assert claimed is not None
    output = _persisted_output(db_session, course, owner)

    with pytest.raises(GenerationJobStateError):
        complete_generation_job(
            db_session,
            claimed.id,
            "00000000-0000-0000-0000-000000000000",
            generated_output_id=output.id,
        )

    db_session.expire_all()
    row = db_session.get(GenerationJob, claimed.id)
    assert row is not None
    assert row.status == JOB_STATUS_RUNNING


def test_complete_requires_exactly_one_result(
    db_session: Session, owner: User, course: Course
) -> None:
    _enqueue(db_session, course, owner)
    claimed = claim_next_generation_job(db_session, "worker-1", LEASE_SECONDS)
    assert claimed is not None

    with pytest.raises(ValueError):
        complete_generation_job(db_session, claimed.id, claimed.claim_token)


def test_a_terminal_failure_gives_the_credit_back(
    db_session: Session, owner: User, course: Course
) -> None:
    before = owner.credits
    _enqueue(db_session, course, owner)
    claimed = claim_next_generation_job(db_session, "worker-1", LEASE_SECONDS)
    assert claimed is not None

    status = fail_generation_job(
        db_session,
        claimed.id,
        claimed.claim_token,
        error_code="PROVIDER_ERROR",
        error_message="The provider refused the prompt.",
        retryable=False,
    )

    assert status == JOB_STATUS_FAILED
    db_session.expire_all()
    row = db_session.get(GenerationJob, claimed.id)
    assert row is not None
    assert row.charge_refunded is True
    assert row.last_error_code == "PROVIDER_ERROR"
    assert row.finished_at is not None
    owner_row = db_session.get(User, owner.id)
    assert owner_row is not None
    assert owner_row.credits == pytest.approx(before)
    assert_balance_is_derivable(db_session, owner.id)


def test_a_retryable_failure_requeues_and_keeps_the_charge(
    db_session: Session, owner: User, course: Course
) -> None:
    """The work is still going to happen, so the student stays charged for it."""
    before = owner.credits
    _enqueue(db_session, course, owner, max_attempts=2)
    claimed = claim_next_generation_job(db_session, "worker-1", LEASE_SECONDS)
    assert claimed is not None

    status = fail_generation_job(
        db_session,
        claimed.id,
        claimed.claim_token,
        error_code="PROVIDER_TIMEOUT",
        error_message="Timed out.",
        retryable=True,
    )

    assert status == JOB_STATUS_QUEUED
    db_session.expire_all()
    row = db_session.get(GenerationJob, claimed.id)
    assert row is not None
    assert row.charge_refunded is False
    assert row.finished_at is None
    owner_row = db_session.get(User, owner.id)
    assert owner_row is not None
    assert owner_row.credits == pytest.approx(before - 1.0)


def test_a_refund_is_not_paid_twice(
    db_session: Session, owner: User, course: Course
) -> None:
    """A generation service that already refunded must not be reversed again."""
    before = owner.credits
    _enqueue(db_session, course, owner)
    claimed = claim_next_generation_job(db_session, "worker-1", LEASE_SECONDS)
    assert claimed is not None
    from services.credits import CreditService

    CreditService.refund(db_session, claimed.charge_receipt)

    fail_generation_job(
        db_session,
        claimed.id,
        claimed.claim_token,
        error_code="INVALID_STRUCTURE",
        error_message="Bad shape.",
        retryable=False,
        already_refunded=True,
    )

    owner_row = db_session.get(User, owner.id)
    assert owner_row is not None
    assert owner_row.credits == pytest.approx(before)
    assert_balance_is_derivable(db_session, owner.id)


def test_an_expired_lease_is_recovered_rather_than_left_running(
    db_session: Session, owner: User, course: Course
) -> None:
    """A killed worker must not leave a student watching a spinner forever."""
    _enqueue(db_session, course, owner, max_attempts=1)
    claimed = claim_next_generation_job(db_session, "worker-1", LEASE_SECONDS)
    assert claimed is not None
    before_recovery = db_session.get(User, owner.id)
    assert before_recovery is not None
    charged_balance = before_recovery.credits

    later = datetime.now(timezone.utc) + timedelta(seconds=LEASE_SECONDS + 1)
    recovered = recover_expired_generation_jobs(db_session, now=later)

    assert recovered == 1
    db_session.expire_all()
    row = db_session.get(GenerationJob, claimed.id)
    assert row is not None
    assert row.status == JOB_STATUS_FAILED
    assert row.last_error_code == "LEASE_EXPIRED"
    assert row.charge_refunded is True
    owner_row = db_session.get(User, owner.id)
    assert owner_row is not None
    assert owner_row.credits == pytest.approx(charged_balance + 1.0)


def test_an_expired_lease_with_attempts_left_is_requeued(
    db_session: Session, owner: User, course: Course
) -> None:
    _enqueue(db_session, course, owner, max_attempts=2)
    claimed = claim_next_generation_job(db_session, "worker-1", LEASE_SECONDS)
    assert claimed is not None

    later = datetime.now(timezone.utc) + timedelta(seconds=LEASE_SECONDS + 1)
    assert recover_expired_generation_jobs(db_session, now=later) == 1

    db_session.expire_all()
    row = db_session.get(GenerationJob, claimed.id)
    assert row is not None
    assert row.status == JOB_STATUS_QUEUED
    assert row.charge_refunded is False


def test_heartbeat_extends_only_the_holder_of_the_claim(
    db_session: Session, owner: User, course: Course
) -> None:
    _enqueue(db_session, course, owner)
    claimed = claim_next_generation_job(db_session, "worker-1", LEASE_SECONDS)
    assert claimed is not None

    assert heartbeat_generation_job(
        db_session, claimed.id, claimed.claim_token, LEASE_SECONDS
    )
    assert not heartbeat_generation_job(
        db_session, claimed.id, "00000000-0000-0000-0000-000000000000", LEASE_SECONDS
    )


def test_the_panel_lists_only_the_reading_students_jobs(
    db_session: Session, owner: User, course: Course
) -> None:
    """A shared course must not show one student another's generations."""
    mine = _enqueue(db_session, course, owner)
    stranger = _make_user(db_session, "stranger@example.com", credits=100.0)
    theirs = enqueue_generation_job(
        db_session,
        course_id=course.id,
        user_id=stranger.id,
        job_type=JOB_TYPE_GENERATE_QUIZ,
        request_payload="{}",
        credit_cost=1.0,
    )

    listed = list_course_generation_jobs(db_session, course.id, owner.id)

    assert [job.id for job in listed] == [mine.id]
    assert theirs.id not in [job.id for job in listed]


def test_a_long_finished_job_falls_out_of_the_panel(
    db_session: Session, owner: User, course: Course
) -> None:
    _enqueue(db_session, course, owner)
    claimed = claim_next_generation_job(db_session, "worker-1", LEASE_SECONDS)
    assert claimed is not None
    output = _persisted_output(db_session, course, owner)
    complete_generation_job(
        db_session, claimed.id, claimed.claim_token, generated_output_id=output.id
    )

    much_later = datetime.now(timezone.utc) + timedelta(days=2)
    assert (
        list_course_generation_jobs(db_session, course.id, owner.id, now=much_later)
        == []
    )


def test_get_generation_job_is_scoped_to_its_course_and_owner(
    db_session: Session, owner: User, course: Course
) -> None:
    job = _enqueue(db_session, course, owner)
    elsewhere = _make_course(db_session, owner, "Elsewhere")

    assert get_generation_job(db_session, course.id, owner.id, job.id) is not None
    assert get_generation_job(db_session, elsewhere.id, owner.id, job.id) is None


def test_queue_metrics_report_depth(
    db_session: Session, owner: User, course: Course
) -> None:
    _enqueue(db_session, course, owner)
    _enqueue(db_session, course, owner)
    claim_next_generation_job(db_session, "worker-1", LEASE_SECONDS)

    metrics = generation_queue_metrics(db_session)

    assert metrics.queued == 1
    assert metrics.running == 1
    assert metrics.oldest_queued_age_seconds >= 0.0


def test_enqueue_and_retry_flashcard_generation_job(
    db_session: Session, owner: User, course: Course
) -> None:
    job = _enqueue(
        db_session,
        course,
        owner,
        job_type=JOB_TYPE_GENERATE_FLASHCARD,
        payload="{}",
    )
    assert job.job_type == JOB_TYPE_GENERATE_FLASHCARD
    assert job.status == JOB_STATUS_QUEUED

    claimed = claim_next_generation_job(db_session, "worker-1", LEASE_SECONDS)
    assert claimed is not None
    assert claimed.id == job.id

    fail_generation_job(
        db_session,
        claimed.id,
        claimed.claim_token,
        error_code="provider_unavailable",
        error_message="Unavailable",
        retryable=False,
    )
    retried = retry_generation_job(
        db_session,
        course_id=course.id,
        user_id=owner.id,
        job_id=job.id,
    )
    assert retried is not None
    assert retried.job_type == JOB_TYPE_GENERATE_FLASHCARD
    assert retried.status == JOB_STATUS_QUEUED

