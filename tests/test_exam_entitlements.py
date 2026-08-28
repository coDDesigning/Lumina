"""A topic is bought once, lazily, and the purchase outlives the plan."""

import pytest
from sqlalchemy import select

from backend.app.models import CreditTransaction, ExamTopicUnlock
from conftest import assert_balance_is_derivable, set_balance
from services.credits import GENERATION_CREDIT_COSTS, CreditService
from services.exam_entitlements import ExamEntitlementService
from utils.ai_errors import InsufficientCreditsError

PRICE = GENERATION_CREDIT_COSTS["exam_topic_unlock"]


def unlocks(session_factory, course_id: int, user_id: int):
    with session_factory() as session:
        return session.scalars(
            select(ExamTopicUnlock).where(
                ExamTopicUnlock.course_id == course_id,
                ExamTopicUnlock.user_id == user_id,
            )
        ).all()


def balance_of(session_factory, user_id: int):
    from backend.app.models import User

    with session_factory() as session:
        return session.get(User, user_id).credits


def test_the_first_artifact_for_a_topic_pays_and_the_second_does_not(
    authz_api,
) -> None:
    before = balance_of(authz_api.session_factory, authz_api.user_a_id)

    with authz_api.session_factory() as session:
        first = ExamEntitlementService.ensure_unlocked(
            session, authz_api.a_course_id, authz_api.user_a_id, "graph-traversal"
        )
    with authz_api.session_factory() as session:
        second = ExamEntitlementService.ensure_unlocked(
            session, authz_api.a_course_id, authz_api.user_a_id, "graph-traversal"
        )

    assert first.charged is True
    assert first.amount == PRICE
    assert second.charged is False
    assert second.amount == 0.0
    assert balance_of(authz_api.session_factory, authz_api.user_a_id) == before - PRICE
    assert (
        len(
            unlocks(
                authz_api.session_factory, authz_api.a_course_id, authz_api.user_a_id
            )
        )
        == 1
    )


def test_a_second_topic_is_a_second_purchase(authz_api) -> None:
    before = balance_of(authz_api.session_factory, authz_api.user_a_id)

    for key in ("graph-traversal", "dynamic-programming"):
        with authz_api.session_factory() as session:
            ExamEntitlementService.ensure_unlocked(
                session, authz_api.a_course_id, authz_api.user_a_id, key
            )

    assert (
        balance_of(authz_api.session_factory, authz_api.user_a_id) == before - 2 * PRICE
    )
    with authz_api.session_factory() as session:
        assert ExamEntitlementService.unlocked_topic_keys(
            session, authz_api.a_course_id, authz_api.user_a_id
        ) == {"graph-traversal", "dynamic-programming"}


def test_the_same_topic_in_another_course_is_its_own_purchase(authz_api) -> None:
    """An unlock is scoped to the course whose material it buys access to."""
    with authz_api.session_factory() as session:
        ExamEntitlementService.ensure_unlocked(
            session, authz_api.a_course_id, authz_api.user_a_id, "graph-traversal"
        )
        second = ExamEntitlementService.ensure_unlocked(
            session, authz_api.b_course_id, authz_api.user_a_id, "graph-traversal"
        )

    assert second.charged is True


def test_an_empty_balance_is_refused_without_granting_access(authz_api) -> None:
    set_balance(authz_api.session_factory, authz_api.user_a_id, 0.0)

    with authz_api.session_factory() as session:
        with pytest.raises(InsufficientCreditsError):
            ExamEntitlementService.ensure_unlocked(
                session, authz_api.a_course_id, authz_api.user_a_id, "graph-traversal"
            )

    assert (
        unlocks(authz_api.session_factory, authz_api.a_course_id, authz_api.user_a_id)
        == []
    )
    assert balance_of(authz_api.session_factory, authz_api.user_a_id) == 0.0


def test_an_unmetered_account_unlocks_without_a_ledger_row(authz_api) -> None:
    from backend.app.models import User

    with authz_api.session_factory() as session:
        session.get(User, authz_api.user_a_id).credits = None
        session.commit()

    with authz_api.session_factory() as session:
        unlock = ExamEntitlementService.ensure_unlocked(
            session, authz_api.a_course_id, authz_api.user_a_id, "graph-traversal"
        )

    assert unlock.charged is True
    rows = unlocks(
        authz_api.session_factory, authz_api.a_course_id, authz_api.user_a_id
    )
    assert len(rows) == 1
    assert rows[0].credit_transaction_id is None
    assert balance_of(authz_api.session_factory, authz_api.user_a_id) is None


def test_the_charge_is_recorded_as_a_derivable_ledger_entry(authz_api) -> None:
    with authz_api.session_factory() as session:
        ExamEntitlementService.ensure_unlocked(
            session, authz_api.a_course_id, authz_api.user_a_id, "graph-traversal"
        )

    with authz_api.session_factory() as session:
        entry = session.scalars(
            select(CreditTransaction).where(
                CreditTransaction.user_id == authz_api.user_a_id,
                CreditTransaction.source_type == "exam_topic_unlock",
            )
        ).one()
        assert entry.delta == -PRICE
        assert_balance_is_derivable(session, authz_api.user_a_id)

        unlock = session.scalars(select(ExamTopicUnlock)).one()
        assert unlock.credit_transaction_id == entry.id
        assert unlock.amount == PRICE


def test_a_topic_unlocked_concurrently_is_never_billed_twice(
    authz_api, monkeypatch
) -> None:
    """The unique key is the arbiter, and the loser's charge is reversed."""
    before = balance_of(authz_api.session_factory, authz_api.user_a_id)

    original = CreditService.charge

    def charge_then_race(db, user_id, amount, **kwargs):
        receipt = original(db, user_id, amount, **kwargs)
        with authz_api.session_factory() as other:
            other.add(
                ExamTopicUnlock(
                    course_id=authz_api.a_course_id,
                    user_id=authz_api.user_a_id,
                    topic_key="graph-traversal",
                    amount=amount,
                )
            )
            other.commit()
        return receipt

    monkeypatch.setattr(CreditService, "charge", staticmethod(charge_then_race))

    with authz_api.session_factory() as session:
        unlock = ExamEntitlementService.ensure_unlocked(
            session, authz_api.a_course_id, authz_api.user_a_id, "graph-traversal"
        )

    assert unlock.charged is False
    assert balance_of(authz_api.session_factory, authz_api.user_a_id) == before
    assert (
        len(
            unlocks(
                authz_api.session_factory,
                authz_api.a_course_id,
                authz_api.user_a_id,
            )
        )
        == 1
    )
    with authz_api.session_factory() as session:
        assert_balance_is_derivable(session, authz_api.user_a_id)
