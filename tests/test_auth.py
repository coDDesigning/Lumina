"""Authentication and password policy tests."""

import pytest

from utils.password_policy import (
    COMMON_PASSWORDS,
    PasswordPolicyError,
    validate_password,
)

WELL_KNOWN_WEAK_PASSWORDS = [
    "password123",
    "qwerty123",
    "admin1234",
    "welcome123",
]


@pytest.mark.parametrize("weak_password", WELL_KNOWN_WEAK_PASSWORDS)
def test_commonly_used_passwords_are_rejected(weak_password: str) -> None:
    """Assert well-known weak and breached passwords fail validation logic."""
    with pytest.raises(PasswordPolicyError):
        validate_password(weak_password)


def test_common_passwords_corpus_is_comprehensive() -> None:
    """Assert common passwords set is robust and not just a tiny handful of entries."""
    assert len(COMMON_PASSWORDS) >= 10000
    for weak in ("password123", "qwerty123", "admin1234", "welcome123"):
        assert weak.lower() in COMMON_PASSWORDS


@pytest.mark.parametrize(
    "weak_password", ["password123", "qwerty123", "admin1234", "welcome123"]
)
def test_registration_rejects_commonly_used_passwords(
    api_context, weak_password: str
) -> None:
    """Assert registering with a commonly used password returns 422."""
    response = api_context.client.post(
        "/api/auth/register",
        json={
            "name": "Weak Password User",
            "email": f"weak-pass-{weak_password}@example.com",
            "password": weak_password,
        },
    )
    assert response.status_code == 422
