"""Tests for cryptographic utilities and encrypted BYOK API key persistence."""

import pytest
from sqlalchemy import select

from backend.app.models import Role as RoleModel
from backend.app.models import User
from utils.crypto import (
    decrypt_value,
    derive_fernet_key,
    encrypt_value,
)


def test_derive_fernet_key_rejects_empty_secret() -> None:
    with pytest.raises(ValueError, match="cannot be empty"):
        derive_fernet_key("")


def test_derive_fernet_key_accepts_valid_base64_or_derives_sha256() -> None:
    key1 = derive_fernet_key("my-super-secret-passphrase-12345")
    assert len(key1) == 44  # base64 encoded 32 bytes is 44 chars
    # Deterministic derivation
    assert derive_fernet_key("my-super-secret-passphrase-12345") == key1


def test_encrypt_and_decrypt_round_trip() -> None:
    plaintext = "sk-proj-test-openai-api-key-1234567890"
    encrypted = encrypt_value(plaintext)
    assert encrypted is not None
    assert encrypted != plaintext
    assert decrypt_value(encrypted) == plaintext


def test_encrypt_and_decrypt_with_custom_secret() -> None:
    secret = "custom-dedicated-encryption-key-abcdef"
    plaintext = "AIzaSy-gemini-custom-api-key-xyz"
    encrypted = encrypt_value(plaintext, secret=secret)
    assert encrypted != plaintext
    assert decrypt_value(encrypted, secret=secret) == plaintext

    # Wrong secret fails decryption
    with pytest.raises(ValueError, match="Invalid or corrupted ciphertext"):
        decrypt_value(encrypted, secret="wrong-secret-key-1234567890")


def test_encrypt_and_decrypt_handles_none_and_empty() -> None:
    assert encrypt_value(None) is None
    assert decrypt_value(None) is None
    assert encrypt_value("") == ""
    assert decrypt_value("") == ""


def test_decrypt_invalid_ciphertext_raises_value_error() -> None:
    with pytest.raises(ValueError, match="Invalid or corrupted ciphertext"):
        decrypt_value("not-a-valid-fernet-token")


def test_user_model_persists_encrypted_api_keys(api_context) -> None:
    with api_context.session_factory() as session:
        role = session.scalar(select(RoleModel).where(RoleModel.name == "user"))
        assert role is not None

        raw_openai = "sk-proj-test-openai-123"
        raw_gemini = "AIzaSy-test-gemini-456"
        raw_anthropic = "sk-ant-test-anthropic-789"

        user = User(
            name="BYOK User",
            email="byok-user@example.com",
            password_hash="dummy_hash",
            role_id=role.id,
            encrypted_openai_api_key=encrypt_value(raw_openai),
            encrypted_gemini_api_key=encrypt_value(raw_gemini),
            encrypted_anthropic_api_key=encrypt_value(raw_anthropic),
        )
        session.add(user)
        session.commit()

        # Read back from database
        persisted = session.scalar(
            select(User).where(User.email == "byok-user@example.com")
        )
        assert persisted is not None
        assert persisted.encrypted_openai_api_key is not None
        assert persisted.encrypted_gemini_api_key is not None
        assert persisted.encrypted_anthropic_api_key is not None

        assert decrypt_value(persisted.encrypted_openai_api_key) == raw_openai
        assert decrypt_value(persisted.encrypted_gemini_api_key) == raw_gemini
        assert decrypt_value(persisted.encrypted_anthropic_api_key) == raw_anthropic
