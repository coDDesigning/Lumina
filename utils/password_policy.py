"""The single definition of what makes a password acceptable.

Every flow that accepts a new password -- registration, an authenticated
change, and any future reset -- validates through :func:`validate_password`,
so the three can never drift apart. Nothing else may impose its own length or
composition rule.

The policy requires a minimum length (default 8), at least one uppercase
letter (A-Z), and at least one special character, capped at bcrypt's 72 bytes.

:func:`policy_description` is the one sentence a client shows a user, and it is
rendered from the configured minimum so the message can never describe a rule
the server does not enforce. See docs/authentication.md.
"""

import re
import string
import unicodedata
from collections.abc import Iterable

from backend.app.config import settings

# bcrypt hashes at most 72 bytes and silently ignores the rest, so a longer
# password is not more secure than its first 72 bytes and accepting one would
# mean storing a claim we cannot check.
MAX_PASSWORD_BYTES = 72

# The passwords credential-stuffing lists open with. This is deliberately a
# short, embedded set rather than a corpus: it costs nothing, stops the guesses
# that actually get tried first, and does not pretend to be a breach database.
COMMON_PASSWORDS = frozenset(
    {
        "123123123123",
        "123456789012",
        "1234567890ab",
        "111111111111",
        "abcdefghijkl",
        "qwertyuiopas",
        "qwertyuiop12",
        "password1234",
        "passwordpassword",
        "letmeinletmein",
        "iloveyouiloveyou",
        "adminadminadmin",
        "welcomewelcome",
        "trustno1trustno1",
        "changemechangeme",
        "secretsecret",
        "superman1234",
        "monkeymonkey",
        "dragondragon",
        "sunshinesunshine",
        "princessprincess",
        "footballfootball",
        "baseballbaseball",
        "starwarsstarwars",
        "whateverwhatever",
        "qazwsxedcrfv",
        "zaq12wsxcde3",
        "p@ssw0rdp@ssw0rd",
        "correcthorsebatterystaple",
    }
)

_SEQUENCES = ("abcdefghijklmnopqrstuvwxyz", "0123456789", "qwertyuiop", "asdfghjkl")
_SPECIAL_CHARACTERS = set(string.punctuation)


class PasswordPolicyError(ValueError):
    """A candidate password does not satisfy the policy."""


def minimum_length() -> int:
    return settings.password_min_length


def policy_description() -> str:
    """The rule, phrased for a user, derived from the rule actually enforced."""
    return (
        f"Passwords must be at least {minimum_length()} characters, with at least "
        "one uppercase letter and one special character."
    )


def _normalize(password: str) -> str:
    return unicodedata.normalize("NFKC", password).casefold()


def _is_repeated_character(candidate: str) -> bool:
    return len(set(candidate)) == 1


def _is_sequential(candidate: str) -> bool:
    for sequence in _SEQUENCES:
        reversed_sequence = sequence[::-1]
        if candidate in sequence or candidate in reversed_sequence:
            return True
    return False


def validate_password(password: str, *, identifiers: Iterable[str] = ()) -> str:
    """Return ``password`` unchanged, or raise :class:`PasswordPolicyError`."""
    if "\x00" in password:
        raise PasswordPolicyError("Passwords cannot contain NUL characters.")
    if len(password) < minimum_length():
        raise PasswordPolicyError(policy_description())
    if len(password.encode("utf-8")) > MAX_PASSWORD_BYTES:
        raise PasswordPolicyError(policy_description())
    if not re.search(r"[A-Z]", password):
        raise PasswordPolicyError(policy_description())
    if not any(c in _SPECIAL_CHARACTERS for c in password):
        raise PasswordPolicyError(policy_description())

    normalized = _normalize(password)
    if normalized.strip() == "":
        raise PasswordPolicyError(policy_description())
    if normalized in COMMON_PASSWORDS:
        raise PasswordPolicyError(policy_description())
    if _is_repeated_character(normalized) or _is_sequential(normalized):
        raise PasswordPolicyError(policy_description())

    return password
