"""The ledger explains the balance, and nothing changes a balance without it.

Every test here defends one half of that claim: either that a balance change
left a truthful row behind, or that a refused operation left nothing at all.
The invariant helper is the thread running through them.
"""

from dataclasses import replace

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from backend.app.config import settings
from backend.app.models import CreditTransaction, User
from schemas.credits import CreditActorType, CreditReason
from schemas.user import UserUpdate
from services import credits as credits_service
from services.credits import ChargeReceipt, CreditActor, CreditService
from services.user import UserService
from tests.conftest import set_credit_policy
from utils.exceptions import BadRequestException


def ledger_sum(session: Session, user_id: int) -> float:
    return (
        session.scalar(
            select(func.coalesce(func.sum(CreditTransaction.delta), 0.0)).where(
                CreditTransaction.user_id == user_id
            )
        )
        or 0.0
    )


def assert_balance_is_derivable(session: Session, user_id: int) -> None:
    """The invariant: a metered balance equals the sum of its deltas."""
    user = session.get(User, user_id)
    assert user is not None
    if user.credits is None:
        return
    assert user.credits == pytest.approx(ledger_sum(session, user_id))


def rows(session: Session, user_id: int, reason: CreditReason | None = None):
    statement = select(CreditTransaction).where(CreditTransaction.user_id == user_id)
    if reason is not None:
        statement = statement.where(CreditTransaction.reason == reason.value)
    return list(session.scalars(statement.order_by(CreditTransaction.id)).all())


def set_balance(session_factory: sessionmaker[Session], user_id: int, balance: float):
    """Move a balance the way a support correction would, ledger included."""
    with session_factory() as session:
        user = session.get(User, user_id)
        delta = balance - (user.credits or 0.0)
        if delta:
            CreditService.adjust(
                session,
                user_id,
                delta,
                actor=CreditActor.admin(user_id, "fixture@example.com"),
                note="Test balance setup",
            )


def test_registration_grants_credits_and_records_why(authz_api):
    response = authz_api.client.post(
        "/api/auth/register",
        json={
            "name": "New Learner",
            "email": "new-learner@example.com",
            "password": "correct horse battery",
        },
    )
    assert response.status_code == 200

    with authz_api.session_factory() as session:
        user = session.scalar(
            select(User).where(User.email == "new-learner@example.com")
        )
        assert user is not None
        assert user.credits == settings.credit_initial_grant

        grants = rows(session, user.id)
        assert len(grants) == 1
        assert grants[0].reason == CreditReason.INITIAL_GRANT.value
        assert grants[0].actor_type == CreditActorType.SYSTEM.value
        assert grants[0].delta == settings.credit_initial_grant
        assert grants[0].balance_after == settings.credit_initial_grant
        assert_balance_is_derivable(session, user.id)


def test_a_new_account_does_not_also_collect_that_months_grant(authz_api):
    """The registration grant claims the month, so 50 does not become 100."""
    authz_api.client.post(
        "/api/auth/register",
        json={
            "name": "Same Month",
            "email": "same-month@example.com",
            "password": "correct horse battery",
        },
    )

    with authz_api.session_factory() as session:
        user = session.scalar(
            select(User).where(User.email == "same-month@example.com")
        )
        CreditService.ensure_current_period_grant(session, user.id)

    with authz_api.session_factory() as session:
        user = session.scalar(
            select(User).where(User.email == "same-month@example.com")
        )
        assert user.credits == settings.credit_initial_grant
        assert rows(session, user.id, CreditReason.PERIODIC_GRANT) == []
        assert_balance_is_derivable(session, user.id)


def test_a_charge_records_who_spent_it_and_on_what(authz_api):
    with authz_api.session_factory() as session:
        receipt = CreditService.charge(
            session, authz_api.user_a_id, 1.0, source_type="study_guide", source_id=7
        )
        assert receipt is not None

        charges = rows(session, authz_api.user_a_id, CreditReason.GENERATION_CHARGE)
        assert len(charges) == 1
        assert charges[0].delta == -1.0
        assert charges[0].balance_after == 49.0
        assert charges[0].actor_type == CreditActorType.USER.value
        assert charges[0].actor_user_id == authz_api.user_a_id
        assert charges[0].source_type == "study_guide"
        assert charges[0].source_id == 7
        assert_balance_is_derivable(session, authz_api.user_a_id)


def test_a_refund_points_at_the_charge_it_reverses(authz_api):
    with authz_api.session_factory() as session:
        receipt = CreditService.charge(
            session, authz_api.user_a_id, 1.0, source_type="quiz"
        )
        CreditService.refund(session, receipt)

        refunds = rows(session, authz_api.user_a_id, CreditReason.GENERATION_REFUND)
        assert len(refunds) == 1
        assert refunds[0].delta == 1.0
        assert refunds[0].refunds_transaction_id == receipt.transaction_id
        assert refunds[0].source_type == "quiz"

        user = session.get(User, authz_api.user_a_id)
        assert user.credits == 50.0
        # The charge is not erased: history keeps both sides.
        assert len(rows(session, authz_api.user_a_id)) == 3
        assert_balance_is_derivable(session, authz_api.user_a_id)


def test_one_charge_cannot_be_refunded_twice(authz_api):
    with authz_api.session_factory() as session:
        receipt = CreditService.charge(
            session, authz_api.user_a_id, 1.0, source_type="flashcard"
        )
        CreditService.refund(session, receipt)
        CreditService.refund(session, receipt)
        CreditService.refund(session, receipt)

        assert (
            len(rows(session, authz_api.user_a_id, CreditReason.GENERATION_REFUND)) == 1
        )
        assert session.get(User, authz_api.user_a_id).credits == 50.0
        assert_balance_is_derivable(session, authz_api.user_a_id)


def test_the_database_refuses_a_second_refund_of_one_charge(authz_api):
    """The uniqueness guard holds even if the service check is bypassed."""
    with authz_api.session_factory() as session:
        receipt = CreditService.charge(
            session, authz_api.user_a_id, 1.0, source_type="ai_tutor"
        )
        CreditService.refund(session, receipt)

    with authz_api.session_factory() as session:
        duplicate = CreditTransaction(
            user_id=authz_api.user_a_id,
            delta=1.0,
            balance_after=51.0,
            reason=CreditReason.GENERATION_REFUND.value,
            actor_type=CreditActorType.SYSTEM.value,
            refunds_transaction_id=receipt.transaction_id,
        )
        session.add(duplicate)
        with pytest.raises(IntegrityError):
            session.commit()


def test_a_refused_charge_leaves_no_trace(authz_api):
    set_balance(authz_api.session_factory, authz_api.user_a_id, 2.0)

    with authz_api.session_factory() as session:
        before = len(rows(session, authz_api.user_a_id))
        receipt = CreditService.charge(
            session, authz_api.user_a_id, 5.0, source_type="quiz"
        )

        assert receipt is None
        assert session.get(User, authz_api.user_a_id).credits == 2.0
        assert len(rows(session, authz_api.user_a_id)) == before
        assert_balance_is_derivable(session, authz_api.user_a_id)


def test_concurrent_charges_cannot_overspend(authz_api):
    """Two requests, one credit: exactly one wins and the balance never goes negative."""
    set_balance(authz_api.session_factory, authz_api.user_a_id, 1.0)

    with authz_api.session_factory() as first, authz_api.session_factory() as second:
        outcomes = [
            CreditService.charge(first, authz_api.user_a_id, 1.0, source_type="quiz"),
            CreditService.charge(second, authz_api.user_a_id, 1.0, source_type="quiz"),
        ]

    granted = [outcome for outcome in outcomes if outcome is not None]
    assert len(granted) == 1

    with authz_api.session_factory() as session:
        assert session.get(User, authz_api.user_a_id).credits == 0.0
        assert (
            len(rows(session, authz_api.user_a_id, CreditReason.GENERATION_CHARGE)) == 1
        )
        assert_balance_is_derivable(session, authz_api.user_a_id)


def test_an_administrator_grant_names_the_administrator(authz_api):
    set_balance(authz_api.session_factory, authz_api.user_a_id, 5.0)

    response = authz_api.client.post(
        "/api/admin/users/owner-a@example.com/credits/grant",
        json={"amount": 20, "note": "Support adjustment"},
        headers=authz_api.authorization_admin,
    )
    assert response.status_code == 200
    assert response.json()["data"]["user"]["credits"] == 25.0

    with authz_api.session_factory() as session:
        grant = rows(session, authz_api.user_a_id, CreditReason.ADMIN_GRANT)[-1]
        assert grant.delta == 20.0
        assert grant.balance_after == 25.0
        assert grant.actor_type == CreditActorType.ADMIN.value
        assert grant.actor_user_id == authz_api.admin_id
        assert grant.actor_label == "authz-admin@example.com"
        assert grant.note == "Support adjustment"
        assert_balance_is_derivable(session, authz_api.user_a_id)


def test_an_administrator_grant_may_exceed_the_automatic_ceiling(authz_api):
    """The ceiling bounds automatic granting, not a deliberate support decision."""
    response = authz_api.client.post(
        "/api/admin/users/owner-a@example.com/credits/grant",
        json={"amount": 500},
        headers=authz_api.authorization_admin,
    )

    assert response.status_code == 200
    assert response.json()["data"]["user"]["credits"] == 550.0


def test_a_negative_adjustment_reduces_the_balance(authz_api):
    set_balance(authz_api.session_factory, authz_api.user_a_id, 25.0)

    response = authz_api.client.post(
        "/api/admin/users/owner-a@example.com/credits/adjust",
        json={"delta": -5, "note": "Correction"},
        headers=authz_api.authorization_admin,
    )

    assert response.status_code == 200
    assert response.json()["data"]["user"]["credits"] == 20.0
    with authz_api.session_factory() as session:
        assert_balance_is_derivable(session, authz_api.user_a_id)


def test_an_adjustment_below_zero_is_rejected_whole(authz_api):
    set_balance(authz_api.session_factory, authz_api.user_a_id, 2.0)

    with authz_api.session_factory() as session:
        before = len(rows(session, authz_api.user_a_id))

    response = authz_api.client.post(
        "/api/admin/users/owner-a@example.com/credits/adjust",
        json={"delta": -5},
        headers=authz_api.authorization_admin,
    )

    assert response.status_code == 400
    with authz_api.session_factory() as session:
        assert session.get(User, authz_api.user_a_id).credits == 2.0
        assert len(rows(session, authz_api.user_a_id)) == before


def test_an_unmetered_account_cannot_be_granted_credits(authz_api):
    """An administrator holds no balance, so there is nothing to grant into."""
    response = authz_api.client.post(
        "/api/admin/users/authz-admin@example.com/credits/grant",
        json={"amount": 10},
        headers=authz_api.authorization_admin,
    )

    assert response.status_code == 400
    with authz_api.session_factory() as session:
        assert session.get(User, authz_api.admin_id).credits is None
        assert rows(session, authz_api.admin_id) == []


def test_a_zero_adjustment_is_refused(authz_api):
    response = authz_api.client.post(
        "/api/admin/users/owner-a@example.com/credits/adjust",
        json={"delta": 0},
        headers=authz_api.authorization_admin,
    )
    assert response.status_code == 422


def test_credit_administration_requires_an_administrator(authz_api):
    response = authz_api.client.post(
        "/api/admin/users/owner-b@example.com/credits/grant",
        json={"amount": 10},
        headers=authz_api.authorization_a,
    )
    assert response.status_code == 403


def test_the_monthly_grant_lands_once_however_often_it_is_checked(authz_api):
    set_balance(authz_api.session_factory, authz_api.user_a_id, 10.0)
    _clear_period_rows(authz_api)

    for _ in range(3):
        with authz_api.session_factory() as session:
            CreditService.ensure_current_period_grant(session, authz_api.user_a_id)

    with authz_api.session_factory() as session:
        periodic = rows(session, authz_api.user_a_id, CreditReason.PERIODIC_GRANT)
        assert len(periodic) == 1
        assert periodic[0].delta == 50.0
        assert periodic[0].grant_period == credits_service.current_grant_period()
        assert session.get(User, authz_api.user_a_id).credits == 60.0
        assert_balance_is_derivable(session, authz_api.user_a_id)


def test_the_monthly_grant_is_trimmed_to_the_ceiling(authz_api):
    """Balance 80 with a ceiling of 100 receives 20, not 50."""
    set_balance(authz_api.session_factory, authz_api.user_a_id, 80.0)
    _clear_period_rows(authz_api)

    with authz_api.session_factory() as session:
        CreditService.ensure_current_period_grant(session, authz_api.user_a_id)

    with authz_api.session_factory() as session:
        periodic = rows(session, authz_api.user_a_id, CreditReason.PERIODIC_GRANT)
        assert len(periodic) == 1
        assert periodic[0].delta == 20.0
        assert session.get(User, authz_api.user_a_id).credits == 100.0
        assert_balance_is_derivable(session, authz_api.user_a_id)


def test_a_balance_at_the_ceiling_is_left_alone(authz_api):
    set_balance(authz_api.session_factory, authz_api.user_a_id, 100.0)
    _clear_period_rows(authz_api)

    with authz_api.session_factory() as session:
        CreditService.ensure_current_period_grant(session, authz_api.user_a_id)

    with authz_api.session_factory() as session:
        assert rows(session, authz_api.user_a_id, CreditReason.PERIODIC_GRANT) == []
        assert session.get(User, authz_api.user_a_id).credits == 100.0


def test_a_balance_above_the_ceiling_is_never_reduced(authz_api):
    """Granting is capped; the ceiling never claws back an administrator's grant."""
    set_balance(authz_api.session_factory, authz_api.user_a_id, 120.0)
    _clear_period_rows(authz_api)

    with authz_api.session_factory() as session:
        CreditService.ensure_current_period_grant(session, authz_api.user_a_id)

    with authz_api.session_factory() as session:
        assert session.get(User, authz_api.user_a_id).credits == 120.0
        assert rows(session, authz_api.user_a_id, CreditReason.PERIODIC_GRANT) == []


def test_the_next_month_grants_again(authz_api, monkeypatch: pytest.MonkeyPatch):
    set_balance(authz_api.session_factory, authz_api.user_a_id, 10.0)
    _clear_period_rows(authz_api)

    monkeypatch.setattr(credits_service, "current_grant_period", lambda: "2026-09")
    with authz_api.session_factory() as session:
        CreditService.ensure_current_period_grant(session, authz_api.user_a_id)
        CreditService.ensure_current_period_grant(session, authz_api.user_a_id)

    monkeypatch.setattr(credits_service, "current_grant_period", lambda: "2026-10")
    with authz_api.session_factory() as session:
        CreditService.ensure_current_period_grant(session, authz_api.user_a_id)

    with authz_api.session_factory() as session:
        periods = [
            row.grant_period
            for row in rows(session, authz_api.user_a_id, CreditReason.PERIODIC_GRANT)
        ]
        assert periods == ["2026-09", "2026-10"]
        assert_balance_is_derivable(session, authz_api.user_a_id)


def test_a_banned_account_is_not_replenished(authz_api):
    _clear_period_rows(authz_api)
    with authz_api.session_factory() as session:
        session.get(User, authz_api.user_a_id).is_banned = True
        session.commit()

    with authz_api.session_factory() as session:
        CreditService.ensure_current_period_grant(session, authz_api.user_a_id)
        assert rows(session, authz_api.user_a_id, CreditReason.PERIODIC_GRANT) == []


def test_reading_the_balance_grants_this_months_credits(authz_api):
    set_balance(authz_api.session_factory, authz_api.user_a_id, 0.0)
    _clear_period_rows(authz_api)

    response = authz_api.client.get(
        "/api/users/me/credits", headers=authz_api.authorization_a
    )

    assert response.status_code == 200
    assert response.json()["data"]["credits"] == 50.0


def test_an_exhausted_balance_still_refuses_generation(authz_api):
    """Zero credits with this month's grant already taken is a hard 402."""
    set_balance(authz_api.session_factory, authz_api.user_a_id, 0.0)

    with authz_api.session_factory() as session:
        receipt = CreditService.charge(
            session, authz_api.user_a_id, 1.0, source_type="quiz"
        )
        assert receipt is None
        assert session.get(User, authz_api.user_a_id).credits == 0.0


def test_a_deployment_without_metering_charges_nothing(
    authz_api, monkeypatch: pytest.MonkeyPatch
):
    set_balance(authz_api.session_factory, authz_api.user_a_id, 0.0)
    monkeypatch.setattr(
        credits_service, "settings", replace(settings, credit_metering_enabled=False)
    )

    with authz_api.session_factory() as session:
        before = len(rows(session, authz_api.user_a_id))
        receipt = CreditService.charge(
            session, authz_api.user_a_id, 1.0, source_type="study_guide"
        )

        assert receipt is not None
        assert receipt.is_exempt is True
        assert session.get(User, authz_api.user_a_id).credits == 0.0
        assert len(rows(session, authz_api.user_a_id)) == before


def test_a_deployment_without_metering_reports_no_balance(
    authz_api, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(
        credits_service, "settings", replace(settings, credit_metering_enabled=False)
    )

    response = authz_api.client.get(
        "/api/users/me/credits", headers=authz_api.authorization_a
    )

    assert response.status_code == 200
    assert response.json()["data"]["credits"] is None


def test_a_deployment_without_metering_refuses_administration(
    authz_api, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(
        credits_service, "settings", replace(settings, credit_metering_enabled=False)
    )

    response = authz_api.client.post(
        "/api/admin/users/owner-a@example.com/credits/grant",
        json={"amount": 10},
        headers=authz_api.authorization_admin,
    )

    assert response.status_code == 400


def test_a_failed_ledger_write_rolls_the_balance_back(
    authz_api, monkeypatch: pytest.MonkeyPatch
):
    """Balance and ledger move together or not at all."""

    def explode(*args, **kwargs):
        raise RuntimeError("ledger unavailable")

    monkeypatch.setattr(CreditService, "_record", staticmethod(explode))

    with authz_api.session_factory() as session:
        with pytest.raises(RuntimeError):
            CreditService.charge(session, authz_api.user_a_id, 1.0, source_type="quiz")
        session.rollback()

    with authz_api.session_factory() as session:
        assert session.get(User, authz_api.user_a_id).credits == 50.0
        assert rows(session, authz_api.user_a_id, CreditReason.GENERATION_CHARGE) == []
        assert_balance_is_derivable(session, authz_api.user_a_id)


def test_demoting_an_administrator_rebaselines_the_ledger(authz_api):
    """An account re-entering metering gets a balance its deltas explain.

    Spending first is what makes the case real: the stored deltas then sum to
    47 while promotion and demotion hand the account 50 back, so without a
    re-baselining row the ledger would stop explaining the balance.
    """
    with authz_api.session_factory() as session:
        for _ in range(3):
            CreditService.charge(session, authz_api.user_a_id, 1.0, source_type="quiz")
        assert session.get(User, authz_api.user_a_id).credits == 47.0

    authz_api.client.put(
        "/api/admin/users/owner-a@example.com/role?role=admin",
        headers=authz_api.authorization_admin,
    )
    with authz_api.session_factory() as session:
        assert session.get(User, authz_api.user_a_id).credits is None

    authz_api.client.put(
        "/api/admin/users/owner-a@example.com/role?role=user",
        headers=authz_api.authorization_admin,
    )

    with authz_api.session_factory() as session:
        user = session.get(User, authz_api.user_a_id)
        assert user.credits == settings.credit_initial_grant
        reset = rows(session, authz_api.user_a_id, CreditReason.METERING_RESET)
        assert len(reset) == 1
        assert reset[0].delta == 3.0
        assert_balance_is_derivable(session, authz_api.user_a_id)


def test_a_user_reads_their_own_history_newest_first(authz_api):
    with authz_api.session_factory() as session:
        receipt = CreditService.charge(
            session, authz_api.user_a_id, 1.0, source_type="quiz"
        )
        CreditService.refund(session, receipt)

    response = authz_api.client.get(
        "/api/users/me/credit-transactions", headers=authz_api.authorization_a
    )

    assert response.status_code == 200
    history = response.json()["data"]
    assert [entry["reason"] for entry in history] == [
        "generation_refund",
        "generation_charge",
        "initial_grant",
    ]
    assert sum(entry["delta"] for entry in history) == 50.0


def test_an_administrator_reads_another_users_history(authz_api):
    response = authz_api.client.get(
        "/api/admin/users/owner-a@example.com/credit-transactions",
        headers=authz_api.authorization_admin,
    )

    assert response.status_code == 200
    assert response.json()["data"][0]["reason"] == "initial_grant"


def test_a_user_cannot_read_another_users_history(authz_api):
    response = authz_api.client.get(
        "/api/admin/users/owner-a@example.com/credit-transactions",
        headers=authz_api.authorization_b,
    )
    assert response.status_code == 403


def test_a_configured_policy_drives_the_grant_amounts(
    authz_api, monkeypatch: pytest.MonkeyPatch
):
    """The numbers come from configuration, not from literals in the service."""
    set_credit_policy(monkeypatch, credit_periodic_grant=7.0, credit_max_balance=1000.0)
    set_balance(authz_api.session_factory, authz_api.user_a_id, 3.0)
    _clear_period_rows(authz_api)

    with authz_api.session_factory() as session:
        CreditService.ensure_current_period_grant(session, authz_api.user_a_id)
        assert session.get(User, authz_api.user_a_id).credits == 10.0


def test_a_charge_must_be_positive(authz_api):
    """A negative charge would credit the account while filing it as spending."""
    with authz_api.session_factory() as session:
        with pytest.raises(ValueError):
            CreditService.charge(session, authz_api.user_a_id, -5.0, source_type="quiz")
        assert session.get(User, authz_api.user_a_id).credits == 50.0
        assert rows(session, authz_api.user_a_id, CreditReason.GENERATION_CHARGE) == []


def test_a_grant_must_be_positive(authz_api):
    with authz_api.session_factory() as session:
        with pytest.raises(BadRequestException):
            CreditService.grant(
                session,
                authz_api.user_a_id,
                -5.0,
                actor=CreditActor.admin(authz_api.admin_id, "a@example.com"),
            )


def test_refunding_an_exempt_receipt_does_nothing(authz_api):
    receipt = ChargeReceipt(user_id=authz_api.user_a_id, amount=1.0)

    with authz_api.session_factory() as session:
        before = len(rows(session, authz_api.user_a_id))
        CreditService.refund(session, receipt)
        assert len(rows(session, authz_api.user_a_id)) == before
        assert session.get(User, authz_api.user_a_id).credits == 50.0


def test_the_profile_update_path_cannot_move_a_balance(authz_api):
    """A balance changes only through CreditService, never through a field write.

    ``UserUpdate`` once carried a ``credits`` field that ``update_user`` applied
    with a plain setattr, which would have moved a balance with no ledger row
    behind it. Nothing may reintroduce that path.
    """
    assert "credits" not in UserUpdate.model_fields

    # A client sending it anyway is dropped rather than honoured.
    smuggled = UserUpdate.model_validate({"credits": 9999, "is_banned": False})
    assert "credits" not in smuggled.model_dump(exclude_unset=True)

    with authz_api.session_factory() as session:
        before = len(rows(session, authz_api.user_a_id))
        UserService.update_user(session, "owner-a@example.com", smuggled)

    with authz_api.session_factory() as session:
        assert session.get(User, authz_api.user_a_id).credits == 50.0
        assert len(rows(session, authz_api.user_a_id)) == before


def test_every_ledger_reason_is_one_the_policy_supports(authz_api):
    """The enum reflects the implemented lifecycle, so purchase is absent."""
    assert "purchase" not in {reason.value for reason in CreditReason}


def _clear_period_rows(authz_api) -> None:
    """Free the current period so a replenishment test can observe a grant.

    Fixture users carry the registration grant, which claims the month exactly
    as it does in production. Tests about the monthly grant need that claim
    released first.
    """
    with authz_api.session_factory() as session:
        for row in rows(session, authz_api.user_a_id):
            if row.grant_period is not None:
                row.grant_period = None
        session.commit()
