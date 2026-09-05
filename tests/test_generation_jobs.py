"""The durable state machine behind a backgrounded generation.

These tests are about the row, not the feature: what a claim is allowed to take,
what an abandoned run costs the student, and what a client rebuilding its panel
is allowed to see.
"""

from concurrent.futures import ThreadPoolExecutor
import hashlib
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from backend.app.models import (
    GENERATION_JOB_TYPES,
    JOB_STATUS_FAILED,
    JOB_STATUS_QUEUED,
    JOB_STATUS_RUNNING,
    JOB_STATUS_SUCCEEDED,
    JOB_TYPE_GENERATE_QUIZ,
    JOB_TYPE_GENERATE_STUDY_GUIDE,
    JOB_TYPE_GENERATE_FLASHCARD,
    Course,
    CreditTransaction,
    GeneratedOutput,
    GenerationJob,
    Quiz,
    Role,
    User,
)
from schemas.credits import CreditReason
from schemas.generation_job import GenerationJobView
from generation_fixtures import (
    GENERATION_FEATURES,
    RecordingProvider,
    seed_ready_material,
)
from services.credits import GENERATION_CREDIT_COSTS, CreditService
from services.generation_jobs import (
    CREDIT_SOURCE_TYPES,
    GenerationJobNotDismissableError,
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
    dismiss_generation_job,
    retry_generation_job,
)

from tests.conftest import assert_balance_is_derivable, seed_registration_grant
from workers import generation_processor

LEASE_SECONDS = 60

JOB_TYPE_BY_FEATURE = {
    "quiz": JOB_TYPE_GENERATE_QUIZ,
    "study_guide": JOB_TYPE_GENERATE_STUDY_GUIDE,
    "flashcards": JOB_TYPE_GENERATE_FLASHCARD,
}


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


@pytest.mark.parametrize("feature", GENERATION_FEATURES, ids=str)
def test_worker_runs_the_shipped_runner_and_links_what_it_wrote(
    feature,
    db_session: Session,
    session_factory: sessionmaker[Session],
    owner: User,
    course: Course,
    retrieval_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Run the runner the worker actually ships, not a stub standing in for it.

    A runner that misreads the result its own service returns fails every
    generation of that type at the last step, after the model has been paid for,
    and no test that replaces the runner can see it.
    """
    seed_ready_material(
        db_session,
        course.id,
        ["Sorting compares elements pairwise until the list is ordered."],
        file_hash=hashlib.sha256(f"runner-{feature.name}".encode()).hexdigest(),
        retrieval_env=retrieval_env,
    )
    monkeypatch.setattr(
        generation_processor,
        "get_text_generation_provider",
        lambda *args, **kwargs: RecordingProvider(feature.provider_payload()),
    )
    queued = _enqueue(
        db_session,
        course,
        owner,
        job_type=JOB_TYPE_BY_FEATURE[feature.name],
        payload=feature.build_request(use_profile_knowledge=False).model_dump_json(),
    )

    assert generation_processor.process_next_generation_job(
        session_factory=session_factory,
        worker_id="generation-test-worker",
        lease_seconds=LEASE_SECONDS,
    )

    db_session.expire_all()
    finished = db_session.get(GenerationJob, queued.id)
    assert finished is not None
    assert finished.status == JOB_STATUS_SUCCEEDED, finished.last_error_message
    if feature.output_type == "quiz":
        assert finished.generated_output_id is None
        assert db_session.get(Quiz, finished.quiz_id) is not None
    else:
        assert finished.quiz_id is None
        output = db_session.get(GeneratedOutput, finished.generated_output_id)
        assert output is not None
        assert output.output_type == feature.output_type


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


@pytest.mark.database_contract
def test_concurrent_claim_is_exclusive_and_charges_only_once(
    db_session: Session,
    session_factory: sessionmaker[Session],
    owner: User,
    course: Course,
) -> None:
    starting_balance = owner.credits
    queued = _enqueue(db_session, course, owner)
    job_id = queued.id
    owner_id = owner.id
    db_session.rollback()

    def claim(worker_id: str):
        with session_factory() as session:
            return claim_next_generation_job(
                session,
                worker_id,
                LEASE_SECONDS,
                max_active_per_user=2,
            )

    with ThreadPoolExecutor(max_workers=2) as executor:
        claims = list(executor.map(claim, ["worker-a", "worker-b"]))

    winners = [claim for claim in claims if claim is not None]
    assert len(winners) == 1
    assert winners[0].id == job_id

    with session_factory() as session:
        job = session.get(GenerationJob, job_id)
        user = session.get(User, owner_id)
        charge_count = session.scalar(
            select(func.count())
            .select_from(CreditTransaction)
            .where(
                CreditTransaction.user_id == owner_id,
                CreditTransaction.reason == CreditReason.GENERATION_CHARGE.value,
            )
        )
        assert job is not None
        assert job.status == JOB_STATUS_RUNNING
        assert job.attempt_count == 1
        assert job.claim_token == winners[0].claim_token
        assert user is not None
        assert user.credits == pytest.approx(starting_balance - 1.0)
        assert charge_count == 1
        assert_balance_is_derivable(session, owner_id)


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


def _succeeded_quiz_job(
    session: Session,
    course: Course,
    owner: User,
    *,
    requested: str | None,
    produced: str | None,
) -> GenerationJob:
    """One finished quiz run, told what was asked for and what answered."""
    payload = (
        '{"question_count": 5}'
        if requested is None
        else ('{"question_count": 5, "model": "%s"}' % requested)
    )
    enqueue_generation_job(
        session,
        course_id=course.id,
        user_id=owner.id,
        job_type=JOB_TYPE_GENERATE_QUIZ,
        request_payload=payload,
        credit_cost=1.0,
    )
    claimed = claim_next_generation_job(session, "worker-1", LEASE_SECONDS)
    assert claimed is not None
    quiz = Quiz(course_id=course.id, title="Q", model_used=produced)
    session.add(quiz)
    session.commit()
    complete_generation_job(session, claimed.id, claimed.claim_token, quiz_id=quiz.id)
    session.expire_all()
    finished = session.get(GenerationJob, claimed.id)
    assert finished is not None
    return finished


def test_a_run_answered_by_another_vendor_says_so(
    db_session: Session, owner: User, course: Course
) -> None:
    """A silent fallback looks identical to the model the student chose.

    The student picked Ollama; it could not be reached, so Gemini wrote the
    quiz. Saying nothing leaves them believing their choice was honoured.
    """
    job = _succeeded_quiz_job(
        db_session,
        course,
        owner,
        requested="ollama:llama3",
        produced="gemini:gemini-3.6-flash",
    )

    view = GenerationJobView.from_job(job)

    assert view.requested_model == "ollama:llama3"
    assert view.fallback_model == "gemini:gemini-3.6-flash"


def test_the_chosen_vendor_answering_is_not_reported_as_a_fallback(
    db_session: Session, owner: User, course: Course
) -> None:
    job = _succeeded_quiz_job(
        db_session,
        course,
        owner,
        requested="gemini:gemini-3.6-flash",
        produced="gemini:gemini-3.6-flash",
    )

    view = GenerationJobView.from_job(job)

    assert view.requested_model == "gemini:gemini-3.6-flash"
    assert view.fallback_model is None


def test_the_same_vendor_on_a_different_model_is_not_a_fallback(
    db_session: Session, owner: User, course: Course
) -> None:
    """Attribution spelling drifts, so only a change of vendor is reportable.

    Comparing the whole identifier would call ``gemini:gemini-3.6-flash`` and
    ``gemini:models/gemini-3.6-flash`` a fallback and warn about an outage that
    never happened.
    """
    job = _succeeded_quiz_job(
        db_session,
        course,
        owner,
        requested="gemini:gemini-3.6-flash",
        produced="gemini:models/gemini-3.6-flash",
    )

    assert GenerationJobView.from_job(job).fallback_model is None


@pytest.mark.parametrize(
    ("requested", "produced"),
    [(None, "gemini:gemini-3.6-flash"), ("ollama:llama3", None), (None, None)],
)
def test_a_fallback_is_claimed_only_when_both_vendors_are_known(
    db_session: Session, owner: User, course: Course, requested, produced
) -> None:
    job = _succeeded_quiz_job(
        db_session, course, owner, requested=requested, produced=produced
    )

    assert GenerationJobView.from_job(job).fallback_model is None


def test_an_unreadable_request_payload_never_fails_the_panel(
    db_session: Session, owner: User, course: Course
) -> None:
    """One bad row must not take the whole generation panel down with it."""
    job = _succeeded_quiz_job(
        db_session,
        course,
        owner,
        requested="ollama:llama3",
        produced="gemini:gemini-3.6-flash",
    )
    job.request_payload = "{not json at all"
    db_session.commit()

    view = GenerationJobView.from_job(job)

    assert view.requested_model is None
    assert view.fallback_model is None


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


def test_a_refund_conflict_does_not_rollback_the_failed_job_transition(
    db_session: Session,
    owner: User,
    course: Course,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queued = _enqueue(db_session, course, owner)
    claimed = claim_next_generation_job(db_session, "worker-1", LEASE_SECONDS)
    assert claimed is not None
    charged_balance = db_session.get(User, owner.id).credits
    original_record = CreditService._record

    def reject_refund(db: Session, **values):
        if values.get("refunds_transaction_id") is not None:
            raise IntegrityError("refund conflict", {}, RuntimeError("duplicate"))
        return original_record(db, **values)

    monkeypatch.setattr(CreditService, "_record", staticmethod(reject_refund))

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
    row = db_session.get(GenerationJob, queued.id)
    assert row is not None
    assert row.status == JOB_STATUS_FAILED
    assert row.charge_refunded is False
    assert row.claim_token is None
    assert row.lease_expires_at is None
    assert row.last_error_code == "PROVIDER_ERROR"
    assert db_session.get(User, owner.id).credits == charged_balance
    assert (
        db_session.scalars(
            select(CreditTransaction).where(
                CreditTransaction.refunds_transaction_id
                == queued.charge_transaction_id
            )
        ).all()
        == []
    )
    assert (
        db_session.scalars(
            select(CreditTransaction).where(
                CreditTransaction.reason == CreditReason.GENERATION_REFUND.value
            )
        ).all()
        == []
    )
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


def _fail(session: Session, job: GenerationJob) -> GenerationJob:
    claimed = claim_next_generation_job(session, "worker-1", LEASE_SECONDS)
    assert claimed is not None
    fail_generation_job(
        session,
        claimed.id,
        claimed.claim_token,
        error_code="PROVIDER_TIMEOUT",
        error_message="Timed out.",
        retryable=False,
    )
    session.expire_all()
    failed = session.get(GenerationJob, claimed.id)
    assert failed is not None
    assert failed.status == JOB_STATUS_FAILED
    return failed


def test_a_dismissed_job_leaves_the_panel(
    db_session: Session, owner: User, course: Course
) -> None:
    """A failure the student has read must be clearable, or it nags for a day."""
    failed = _fail(db_session, _enqueue(db_session, course, owner))

    dismissed = dismiss_generation_job(
        db_session, course_id=course.id, user_id=owner.id, job_id=failed.id
    )

    assert dismissed is not None
    assert dismissed.dismissed_at is not None
    assert list_course_generation_jobs(db_session, course.id, owner.id) == []


def test_dismissing_the_same_job_twice_changes_nothing(
    db_session: Session, owner: User, course: Course
) -> None:
    failed = _fail(db_session, _enqueue(db_session, course, owner))

    first = dismiss_generation_job(
        db_session, course_id=course.id, user_id=owner.id, job_id=failed.id
    )
    assert first is not None
    stamped = first.dismissed_at

    second = dismiss_generation_job(
        db_session, course_id=course.id, user_id=owner.id, job_id=failed.id
    )

    assert second is not None
    assert second.dismissed_at == stamped


def test_an_unfinished_job_cannot_be_dismissed(
    db_session: Session, owner: User, course: Course
) -> None:
    """Hiding a run that is still going would strand the student's credit."""
    job = _enqueue(db_session, course, owner)

    with pytest.raises(GenerationJobNotDismissableError):
        dismiss_generation_job(
            db_session, course_id=course.id, user_id=owner.id, job_id=job.id
        )

    assert [
        row.id for row in list_course_generation_jobs(db_session, course.id, owner.id)
    ] == [job.id]


def test_dismissal_is_scoped_to_its_course_and_owner(
    db_session: Session, owner: User, course: Course
) -> None:
    failed = _fail(db_session, _enqueue(db_session, course, owner))
    stranger = _make_user(db_session, "stranger@example.com", credits=100.0)
    elsewhere = _make_course(db_session, owner, "Elsewhere")

    assert (
        dismiss_generation_job(
            db_session, course_id=course.id, user_id=stranger.id, job_id=failed.id
        )
        is None
    )
    assert (
        dismiss_generation_job(
            db_session, course_id=elsewhere.id, user_id=owner.id, job_id=failed.id
        )
        is None
    )


def test_retrying_a_failure_clears_the_run_it_replaces(
    db_session: Session, owner: User, course: Course
) -> None:
    """Try again replaced the run, so the panel must stop offering it again."""
    failed = _fail(db_session, _enqueue(db_session, course, owner))

    replacement = retry_generation_job(
        db_session, course_id=course.id, user_id=owner.id, job_id=failed.id
    )

    assert replacement is not None
    assert replacement.id != failed.id
    listed = [
        row.id for row in list_course_generation_jobs(db_session, course.id, owner.id)
    ]
    assert listed == [replacement.id]

    again = retry_generation_job(
        db_session, course_id=course.id, user_id=owner.id, job_id=failed.id
    )

    assert again is not None
    assert again.id == replacement.id
    assert [
        row.id for row in list_course_generation_jobs(db_session, course.id, owner.id)
    ] == [replacement.id]


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


def test_every_generation_job_type_has_a_priced_credit_source() -> None:
    """A queue-able job type with no ledger name is a 500 at enqueue."""
    assert set(CREDIT_SOURCE_TYPES) == set(GENERATION_JOB_TYPES)
    for source_type in CREDIT_SOURCE_TYPES.values():
        assert source_type in GENERATION_CREDIT_COSTS
