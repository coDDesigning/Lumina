"""One password policy, and the three places it has to hold.

The unit tests pin the rules. The endpoint tests exist for a different reason:
the acceptance criterion is that registration and change enforce the *same*
policy, so each rejection is asserted at both boundaries rather than only in
the module they share.
"""

from dataclasses import replace

import pytest
from sqlalchemy import select

import routes.auth as auth_route
import services.credits as credits_service
import services.user as user_service
import utils.password_policy as password_policy
from backend.app.config import settings
from backend.app.models import Role as RoleModel
from backend.app.models import User
from utils.password_policy import (
    MAX_PASSWORD_BYTES,
    PasswordPolicyError,
    policy_description,
    validate_password,
)

GOOD_PASSWORD = "analytical-engine-1843"
IDENTIFIERS = ("Ada Lovelace", "ada@example.com")


# --- the rules ---------------------------------------------------------------


def test_a_long_passphrase_is_accepted_without_character_classes() -> None:
    assert validate_password("correct battery paperclip", identifiers=IDENTIFIERS)


def test_an_eight_character_password_is_at_the_floor() -> None:
    assert validate_password("br8kfast", identifiers=IDENTIFIERS)


@pytest.mark.parametrize(
    ("password", "why"),
    [
        ("short1", "shorter than the minimum"),
        ("a" * (MAX_PASSWORD_BYTES + 1), "past the bytes bcrypt hashes"),
        ("é" * 40, "past the bytes bcrypt hashes once encoded"),
        ("aaaaaaaaaaaaaa", "one repeated character"),
        ("abcdefghijklm", "a run of the alphabet"),
        ("qwertyuiop", "a keyboard row"),
        ("password1234", "a password credential stuffing opens with"),
        ("            ", "entirely whitespace"),
    ],
)
def test_rejected_passwords(password: str, why: str) -> None:
    with pytest.raises(PasswordPolicyError):
        validate_password(password, identifiers=IDENTIFIERS)


def test_a_nul_character_is_named_rather_than_described_as_weak() -> None:
    with pytest.raises(PasswordPolicyError, match="NUL"):
        validate_password("fine-enough\x00password", identifiers=IDENTIFIERS)


@pytest.mark.parametrize(
    "password",
    [
        "lovelace-is-my-password",
        "LOVELACE-is-not-a-secret",
        "ada@example.com-and-more",
        "Ādalovelace-passphrase",
    ],
)
def test_a_password_built_from_the_account_is_rejected(password: str) -> None:
    with pytest.raises(PasswordPolicyError):
        validate_password(password, identifiers=IDENTIFIERS)


def test_short_identifier_fragments_do_not_reject_unrelated_passwords() -> None:
    """A two-letter name appears inside ordinary words; four is the floor."""
    assert validate_password(
        "delightful-libraries-everywhere", identifiers=("Li Na", "li@example.com")
    )


def test_the_described_policy_is_the_enforced_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        password_policy, "settings", replace(settings, password_min_length=20)
    )

    assert "at least 20 characters" in policy_description()
    with pytest.raises(PasswordPolicyError):
        validate_password("kangaroo-typewriter", identifiers=())
    assert validate_password("kangaroo-typewriter-hymn", identifiers=())


# --- the rule a client is told is the rule that is enforced -------------------


def test_the_endpoint_reports_the_configured_minimum(
    api_context, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        password_policy, "settings", replace(settings, password_min_length=14)
    )

    payload = api_context.client.get("/api/auth/password-policy").json()

    assert payload["minimum_length"] == 14
    assert payload["maximum_bytes"] == MAX_PASSWORD_BYTES


def test_the_endpoint_states_exactly_what_a_refusal_would_say(
    api_context, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The sign-up form renders this sentence, so it has to be that sentence.

    A form carrying its own copy of the rule is how a screen ends up promising
    eight characters while the server demands twelve.
    """
    monkeypatch.setattr(
        password_policy, "settings", replace(settings, password_min_length=20)
    )

    payload = api_context.client.get("/api/auth/password-policy").json()

    assert payload["description"] == policy_description()
    with pytest.raises(PasswordPolicyError) as refusal:
        validate_password("kangaroo-typewriter", identifiers=())
    assert str(refusal.value) == payload["description"]


def test_the_policy_is_readable_without_a_session(api_context) -> None:
    # The screens that need it -- registration and password reset -- are
    # reached without one.
    assert api_context.client.get("/api/auth/password-policy").status_code == 200


# --- registration and change enforce the same policy -------------------------


@pytest.fixture(autouse=True)
def unverifying_deployment(monkeypatch: pytest.MonkeyPatch, session_factory) -> None:
    """An ordinary metered account, so these tests are only about passwords."""
    patched = replace(
        settings, credit_metering_enabled=True, email_verification_required=False
    )
    for module in (auth_route, user_service, credits_service):
        monkeypatch.setattr(module, "settings", patched)

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


def _register(client, password: str):
    return client.post(
        "/api/auth/register",
        json={
            "name": IDENTIFIERS[0],
            "email": IDENTIFIERS[1],
            "password": password,
        },
    )


def _authorize(client, password: str = GOOD_PASSWORD) -> dict[str, str]:
    response = client.post(
        "/api/auth/login", data={"username": IDENTIFIERS[1], "password": password}
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


WEAK_PASSWORDS = [
    "short1",
    "aaaaaaaaaaaaaa",
    "password1234",
    "lovelace-is-my-password",
]


@pytest.mark.parametrize("password", WEAK_PASSWORDS)
def test_registration_refuses_a_password_the_policy_rejects(
    api_context, password: str
) -> None:
    response = _register(api_context.client, password)

    assert response.status_code == 422
    with api_context.session_factory() as session:
        assert session.scalar(select(User).where(User.email == IDENTIFIERS[1])) is None


@pytest.mark.parametrize("password", WEAK_PASSWORDS)
def test_changing_a_password_refuses_what_registration_would_have(
    api_context, password: str
) -> None:
    assert _register(api_context.client, GOOD_PASSWORD).status_code == 200
    authorization = _authorize(api_context.client)

    response = api_context.client.put(
        "/api/users/me/password",
        json={"current_password": GOOD_PASSWORD, "new_password": password},
        headers=authorization,
    )

    assert response.status_code == 400
    # The user is told the rule rather than made to guess it.
    assert response.json()["detail"] == policy_description()
    # The old password still works, so a rejected change changed nothing.
    _authorize(api_context.client)


def test_a_password_change_needs_the_current_password(api_context) -> None:
    _register(api_context.client, GOOD_PASSWORD)
    authorization = _authorize(api_context.client)

    response = api_context.client.put(
        "/api/users/me/password",
        json={
            "current_password": "not-the-current-password",
            "new_password": "kangaroo-typewriter-hymn",
        },
        headers=authorization,
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Current password is incorrect"
    _authorize(api_context.client)


def test_a_password_change_replaces_the_stored_hash(api_context) -> None:
    _register(api_context.client, GOOD_PASSWORD)
    authorization = _authorize(api_context.client)
    with api_context.session_factory() as session:
        before = session.scalar(
            select(User.password_hash).where(User.email == IDENTIFIERS[1])
        )

    response = api_context.client.put(
        "/api/users/me/password",
        json={
            "current_password": GOOD_PASSWORD,
            "new_password": "kangaroo-typewriter-hymn",
        },
        headers=authorization,
    )

    assert response.status_code == 200
    assert response.json()["success"] is True

    _authorize(api_context.client, "kangaroo-typewriter-hymn")
    stale = api_context.client.post(
        "/api/auth/login",
        data={"username": IDENTIFIERS[1], "password": GOOD_PASSWORD},
    )
    assert stale.status_code == 401

    with api_context.session_factory() as session:
        after = session.scalar(
            select(User.password_hash).where(User.email == IDENTIFIERS[1])
        )
    assert after != before
    # Only the hash is ever stored.
    assert "kangaroo-typewriter-hymn" not in after


def test_a_password_change_requires_a_session(api_context) -> None:
    _register(api_context.client, GOOD_PASSWORD)

    response = api_context.client.put(
        "/api/users/me/password",
        json={
            "current_password": GOOD_PASSWORD,
            "new_password": "kangaroo-typewriter-hymn",
        },
    )

    assert response.status_code == 401
