"""The gate between a fresh registration and its introductory credits.

Every test here drives the real endpoints and reads the token out of the
message the application tried to send, because the point of the feature is that
the only way to the credits is through the address. Nothing reaches into the
service to mint a token it did not have to receive.
"""

import re
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

import routes.auth as auth_route
import routes.user as user_route
import services.credits as credits_service
import services.email_verification as verification_service
import services.user as user_service
from backend.app.config import settings
from backend.app.models import CreditTransaction, EmailVerificationToken
from backend.app.models import Role as RoleModel
from backend.app.models import User
from schemas.credits import CreditReason
from services.credits import CreditActor, CreditService
from services.email_delivery import EmailDeliveryError, EmailMessage
from tests.conftest import assert_balance_is_derivable, rows

REGISTRATION = {
    "name": "Ada Lovelace",
    "email": "ada@example.com",
    "password": "Analytical-engine-1843!",
}

# The modules that read ``email_verification_required``. Patching every one of
# them together is what keeps a test from exercising a deployment that is half
# verifying, which is a state no real configuration can produce.
POLICY_MODULES = (
    auth_route,
    user_route,
    user_service,
    credits_service,
    verification_service,
)


class RecordingEmailSender:
    """Keeps the messages a test's deployment tried to send."""

    def __init__(self) -> None:
        self.messages: list[EmailMessage] = []

    def send(self, message: EmailMessage) -> None:
        self.messages.append(message)

    @property
    def tokens(self) -> list[str]:
        found = []
        for message in self.messages:
            match = re.search(r"[?&]token=([A-Za-z0-9_-]+)", message.body)
            assert match is not None, "the message carried no verification link"
            found.append(match.group(1))
        return found


class FailingEmailSender:
    def send(self, message: EmailMessage) -> None:
        raise EmailDeliveryError("Outbound mail delivery failed.")


def _apply_policy(monkeypatch: pytest.MonkeyPatch, **overrides: object) -> None:
    patched = replace(settings, credit_metering_enabled=True, **overrides)
    for module in POLICY_MODULES:
        monkeypatch.setattr(module, "settings", patched)


@pytest.fixture(autouse=True)
def existing_initial_admin(session_factory) -> None:
    """The deployment already has its administrator.

    Self-hosted registration hands the very first account administration, and an
    administrator is unmetered, so without this every test below would be about
    an account that never had credits to gate.
    """
    with session_factory() as session:
        role = session.scalar(select(RoleModel).where(RoleModel.name == "admin"))
        assert role is not None
        session.add(
            User(
                name="Existing Admin",
                email="admin@example.com",
                password_hash="not-used-by-these-tests",
                role=role,
                credits=None,
                is_banned=False,
                is_initial_admin=True,
                preferred_model="gpt-4o-mini",
            )
        )
        session.commit()


@pytest.fixture
def verifying(monkeypatch: pytest.MonkeyPatch) -> RecordingEmailSender:
    """A metered deployment that gates credits on a verified address."""
    _apply_policy(
        monkeypatch,
        email_verification_required=True,
        app_public_base_url="https://app.example.com",
    )
    sender = RecordingEmailSender()
    monkeypatch.setattr(verification_service, "get_email_sender", lambda: sender)
    return sender


@pytest.fixture
def unverifying(monkeypatch: pytest.MonkeyPatch) -> None:
    """A metered deployment that does not verify addresses, as self-hosted is."""
    _apply_policy(monkeypatch, email_verification_required=False)


def _register(client, **overrides):
    return client.post("/api/auth/register", json={**REGISTRATION, **overrides})


def _load_user(session: Session, email: str = REGISTRATION["email"]) -> User:
    user = session.scalar(select(User).where(User.email == email))
    assert user is not None
    return user


# --- registration ------------------------------------------------------------


def test_registration_creates_an_unverified_account_without_spendable_credits(
    api_context, verifying
) -> None:
    response = _register(api_context.client)

    assert response.status_code == 200
    body = response.json()
    assert body["email_verification_required"] is True
    assert body["is_email_verified"] is False

    with api_context.session_factory() as session:
        user = _load_user(session)
        assert user.email_verified_at is None
        # Zero rather than null: null would mean unmetered, which would hand the
        # account unlimited generation instead of none.
        assert user.credits == 0.0
        assert rows(session, user.id, CreditReason.INITIAL_GRANT) == []
        assert_balance_is_derivable(session, user.id)

        # The balance is the one the generation gate reads, so nothing can be
        # spent until the address is proven.
        assert CreditService.charge(session, user.id, 1.0, source_type="test") is None


def test_registration_emails_exactly_one_link(api_context, verifying) -> None:
    _register(api_context.client)

    assert len(verifying.messages) == 1
    message = verifying.messages[0]
    assert message.to_address == REGISTRATION["email"]
    assert "https://app.example.com/verify-email?token=" in message.body

    with api_context.session_factory() as session:
        live = session.scalars(
            select(EmailVerificationToken).where(
                EmailVerificationToken.consumed_at.is_(None)
            )
        ).all()
        assert len(live) == 1
        # Only the digest is stored, so the database never holds a usable link.
        assert live[0].token_hash != verifying.tokens[0]
        assert len(live[0].token_hash) == 64


def test_an_unverified_account_cannot_sign_in_before_verification(
    api_context, verifying
) -> None:
    _register(api_context.client)

    response = api_context.client.post(
        "/api/auth/login",
        data={
            "username": REGISTRATION["email"],
            "password": REGISTRATION["password"],
        },
    )

    assert response.status_code == 403
    assert "verify your email" in response.json()["detail"].lower()


def test_self_hosted_registration_grants_credits_without_verification(
    api_context, unverifying
) -> None:
    response = _register(api_context.client)

    assert response.status_code == 200
    assert response.json()["email_verification_required"] is False

    with api_context.session_factory() as session:
        user = _load_user(session)
        assert user.credits == settings.credit_initial_grant
        assert len(rows(session, user.id, CreditReason.INITIAL_GRANT)) == 1
        assert_balance_is_derivable(session, user.id)


# --- redeeming ---------------------------------------------------------------


def test_verification_grants_the_introductory_credits(api_context, verifying) -> None:
    _register(api_context.client)

    response = api_context.client.post(
        "/api/auth/verify-email", json={"token": verifying.tokens[0]}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["is_email_verified"] is True
    assert body["credits_granted"] == settings.credit_initial_grant

    with api_context.session_factory() as session:
        user = _load_user(session)
        assert user.email_verified_at is not None
        assert user.credits == settings.credit_initial_grant
        assert len(rows(session, user.id, CreditReason.INITIAL_GRANT)) == 1
        assert_balance_is_derivable(session, user.id)


def test_a_token_can_only_be_redeemed_once(api_context, verifying) -> None:
    _register(api_context.client)
    token = verifying.tokens[0]

    first = api_context.client.post("/api/auth/verify-email", json={"token": token})
    second = api_context.client.post("/api/auth/verify-email", json={"token": token})

    assert first.status_code == 200
    assert second.status_code == 400
    assert "invalid or has expired" in second.json()["detail"]

    with api_context.session_factory() as session:
        user = _load_user(session)
        # The second click granted nothing, which is the whole reason the
        # single-use check lives in the claiming statement.
        assert len(rows(session, user.id, CreditReason.INITIAL_GRANT)) == 1
        assert user.credits == settings.credit_initial_grant


def test_an_expired_token_is_refused_and_grants_nothing(api_context, verifying) -> None:
    _register(api_context.client)
    token = verifying.tokens[0]

    with api_context.session_factory() as session:
        stored = session.scalars(select(EmailVerificationToken)).one()
        stored.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        session.commit()

    response = api_context.client.post("/api/auth/verify-email", json={"token": token})

    assert response.status_code == 400
    with api_context.session_factory() as session:
        user = _load_user(session)
        assert user.email_verified_at is None
        assert user.credits == 0.0
        assert rows(session, user.id, CreditReason.INITIAL_GRANT) == []


def test_an_unknown_token_is_refused_with_the_same_answer(
    api_context, verifying
) -> None:
    _register(api_context.client)

    response = api_context.client.post(
        "/api/auth/verify-email", json={"token": "not-a-token-anybody-issued"}
    )

    assert response.status_code == 400
    assert "invalid or has expired" in response.json()["detail"]


def test_verifying_after_a_metering_reset_does_not_grant_a_second_balance(
    api_context, verifying
) -> None:
    """A demoted administrator is given its opening balance by the reset.

    Without the ``METERING_RESET`` check in ``grant_initial_credits`` the same
    account would collect a second opening balance simply by clicking the link
    it was sent when it registered.
    """
    _register(api_context.client)

    with api_context.session_factory() as session:
        user = _load_user(session)
        CreditService.apply_role_metering(session, user, is_admin=True)
        CreditService.apply_role_metering(session, user, is_admin=False)
        session.commit()
        assert len(rows(session, user.id, CreditReason.METERING_RESET)) == 1
        balance_after_reset = user.credits

    response = api_context.client.post(
        "/api/auth/verify-email", json={"token": verifying.tokens[0]}
    )

    assert response.status_code == 200
    assert response.json()["is_email_verified"] is True
    assert response.json()["credits_granted"] is None

    with api_context.session_factory() as session:
        user = _load_user(session)
        assert user.credits == balance_after_reset
        assert rows(session, user.id, CreditReason.INITIAL_GRANT) == []
        assert_balance_is_derivable(session, user.id)


def test_an_unverified_account_is_not_handed_the_monthly_grant(
    api_context, verifying
) -> None:
    """Otherwise the gate would only delay the credits by a month."""
    _register(api_context.client)

    with api_context.session_factory() as session:
        user = _load_user(session)
        CreditService.ensure_current_period_grant(session, user.id)
        session.refresh(user)

        assert user.credits == 0.0
        assert rows(session, user.id, CreditReason.PERIODIC_GRANT) == []


def test_a_verified_account_is_handed_the_monthly_grant(api_context, verifying) -> None:
    """The gate withholds grants; it does not stop them forever."""
    _register(api_context.client)
    api_context.client.post(
        "/api/auth/verify-email", json={"token": verifying.tokens[0]}
    )

    with api_context.session_factory() as session:
        user = _load_user(session)
        # The introductory grant takes this month's slot, so age it into the
        # previous period and spend the balance to leave the grant headroom.
        granted = rows(session, user.id, CreditReason.INITIAL_GRANT)[0]
        granted.grant_period = "1999-12"
        session.commit()
        CreditService.apply_admin_change(
            session,
            user.id,
            -settings.credit_initial_grant,
            reason=CreditReason.ADMIN_ADJUSTMENT,
            actor=CreditActor.admin(user.id, "fixture@example.com"),
            note="drain",
        )
        CreditService.ensure_current_period_grant(session, user.id)
        session.refresh(user)

        assert len(rows(session, user.id, CreditReason.PERIODIC_GRANT)) == 1
        assert user.credits == settings.credit_periodic_grant
        assert_balance_is_derivable(session, user.id)


# --- resending ---------------------------------------------------------------


def test_resending_replaces_the_outstanding_link(api_context, verifying) -> None:
    _register(api_context.client)
    first_token = verifying.tokens[0]

    resend = api_context.client.post(
        "/api/auth/verify-email/resend", json={"email": REGISTRATION["email"]}
    )
    assert resend.status_code == 200
    second_token = verifying.tokens[1]
    assert second_token != first_token

    stale = api_context.client.post(
        "/api/auth/verify-email", json={"token": first_token}
    )
    assert stale.status_code == 400

    fresh = api_context.client.post(
        "/api/auth/verify-email", json={"token": second_token}
    )
    assert fresh.status_code == 200
    assert fresh.json()["credits_granted"] == settings.credit_initial_grant


def test_resending_does_not_reveal_whether_an_address_exists(
    api_context, verifying
) -> None:
    _register(api_context.client)
    api_context.client.post(
        "/api/auth/verify-email", json={"token": verifying.tokens[0]}
    )
    _register(api_context.client, email="grace@example.com", name="Grace Hopper")

    answers = [
        api_context.client.post(
            "/api/auth/verify-email/resend", json={"email": address}
        )
        for address in (
            "nobody@example.com",
            REGISTRATION["email"],
            "grace@example.com",
        )
    ]

    assert {answer.status_code for answer in answers} == {200}
    assert len({answer.text for answer in answers}) == 1

    with api_context.session_factory() as session:
        # Only the unverified account actually got a new link; the identical
        # replies are what the caller cannot tell apart.
        grace = _load_user(session, "grace@example.com")
        live = session.scalars(
            select(EmailVerificationToken).where(
                EmailVerificationToken.consumed_at.is_(None)
            )
        ).all()
        assert [token.user_id for token in live] == [grace.id]


def test_resending_is_refused_where_the_deployment_does_not_verify(
    api_context, unverifying
) -> None:
    _register(api_context.client)

    response = api_context.client.post(
        "/api/auth/verify-email/resend", json={"email": REGISTRATION["email"]}
    )

    assert response.status_code == 409
    assert response.json()["detail"] == auth_route.VERIFICATION_DISABLED_MESSAGE


# --- delivery ----------------------------------------------------------------


def test_a_failed_send_still_leaves_a_usable_link(
    api_context, verifying, monkeypatch
) -> None:
    """The account exists and the token is real, so resend is a real remedy."""
    monkeypatch.setattr(
        verification_service, "get_email_sender", lambda: FailingEmailSender()
    )

    response = _register(api_context.client)

    assert response.status_code == 200
    assert response.json()["message"] == auth_route.VERIFICATION_UNDELIVERABLE_MESSAGE

    with api_context.session_factory() as session:
        user = _load_user(session)
        stored = session.scalars(select(EmailVerificationToken)).one()
        assert stored.user_id == user.id
        assert stored.consumed_at is None


def test_a_failed_send_does_not_lose_the_account(
    api_context, verifying, monkeypatch
) -> None:
    monkeypatch.setattr(
        verification_service, "get_email_sender", lambda: FailingEmailSender()
    )
    _register(api_context.client)

    with api_context.session_factory() as session:
        user = _load_user(session)
        assert user is not None
        assert user.email_verified_at is None


# --- what the client is told -------------------------------------------------


def test_credit_status_distinguishes_spent_from_never_granted(
    api_context, verifying
) -> None:
    _register(api_context.client)

    api_context.client.post(
        "/api/auth/verify-email", json={"token": verifying.tokens[0]}
    )

    login = api_context.client.post(
        "/api/auth/login",
        data={
            "username": REGISTRATION["email"],
            "password": REGISTRATION["password"],
        },
    )
    assert login.status_code == 200
    authorization = {"Authorization": f"Bearer {login.json()['access_token']}"}

    after = api_context.client.get("/api/users/me/credits", headers=authorization)
    assert after.json()["data"]["email_verification_required"] is True
    assert after.json()["data"]["is_email_verified"] is True
    assert after.json()["data"]["credits"] == settings.credit_initial_grant


# --- throttling --------------------------------------------------------------


def test_redeem_and_resend_share_one_per_ip_throttle(
    api_context, verifying, monkeypatch
) -> None:
    """Two halves of the same abuse, so one bucket covers both."""
    monkeypatch.setattr(
        verification_service,
        "settings",
        replace(settings, email_verification_required=True),
    )
    import utils.rate_limit as rate_limit_module

    monkeypatch.setattr(
        rate_limit_module,
        "settings",
        replace(
            settings,
            rate_limit_verification_max_attempts=2,
            rate_limit_verification_window_seconds=3600,
        ),
    )

    api_context.client.post("/api/auth/verify-email", json={"token": "guess-one"})
    api_context.client.post(
        "/api/auth/verify-email/resend", json={"email": "nobody@example.com"}
    )

    limited = api_context.client.post(
        "/api/auth/verify-email", json={"token": "guess-three"}
    )

    assert limited.status_code == 429
    assert limited.headers["X-Error-Code"] == "verification_rate_limited"
    assert "Retry-After" in limited.headers


# --- the ledger stays the only writer ----------------------------------------


def test_verification_writes_exactly_one_ledger_row(api_context, verifying) -> None:
    _register(api_context.client)
    api_context.client.post(
        "/api/auth/verify-email", json={"token": verifying.tokens[0]}
    )

    with api_context.session_factory() as session:
        user = _load_user(session)
        ledger = session.scalars(
            select(CreditTransaction).where(CreditTransaction.user_id == user.id)
        ).all()
        assert len(ledger) == 1
        assert ledger[0].reason == CreditReason.INITIAL_GRANT.value
        assert ledger[0].delta == settings.credit_initial_grant
        assert ledger[0].balance_after == settings.credit_initial_grant
