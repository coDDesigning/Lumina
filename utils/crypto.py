"""Cryptographic utilities for symmetric encryption and decryption of secrets at rest.

Used for storing sensitive credentials such as Bring Your Own Key (BYOK) API keys.
"""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from backend.app.config import settings


def derive_fernet_key(secret: str) -> bytes:
    """Derive a URL-safe base64-encoded 32-byte key suitable for Fernet.

    If the provided secret is already a valid 32-byte URL-safe base64 key,
    it is returned directly. Otherwise, SHA-256 is used to derive 32 bytes
    and encode them into URL-safe base64.
    """
    if not secret:
        raise ValueError("Encryption secret cannot be empty.")

    try:
        raw = base64.urlsafe_b64decode(secret.encode("utf-8"))
        if len(raw) == 32:
            return secret.encode("utf-8")
    except Exception:
        pass

    digest = hashlib.sha256(secret.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def get_fernet(secret: str | None = None) -> Fernet:
    """Instantiate a Fernet cipher instance using the active encryption key."""
    if secret is not None:
        key = derive_fernet_key(secret)
    elif settings.encryption_key:
        key = derive_fernet_key(settings.encryption_key)
    else:
        key = derive_fernet_key(settings.jwt_secret_key)
    return Fernet(key)


def encrypt_value(plain_text: str | None, secret: str | None = None) -> str | None:
    """Encrypts a plaintext string into a Fernet ciphertext string.

    Returns None if plain_text is None, or empty string if plain_text is empty.
    """
    if plain_text is None:
        return None
    if not plain_text:
        return ""
    fernet = get_fernet(secret)
    return fernet.encrypt(plain_text.encode("utf-8")).decode("utf-8")


def decrypt_value(cipher_text: str | None, secret: str | None = None) -> str | None:
    """Decrypts a Fernet ciphertext string back to plaintext.

    Returns None if cipher_text is None, or empty string if cipher_text is empty.
    Raises ValueError if ciphertext is corrupted or the key is invalid.
    """
    if cipher_text is None:
        return None
    if not cipher_text:
        return ""
    fernet = get_fernet(secret)
    try:
        return fernet.decrypt(cipher_text.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError("Invalid or corrupted ciphertext.") from exc
