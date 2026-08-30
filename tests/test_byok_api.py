"""Tests for Bring Your Own Key (BYOK) API endpoints and dynamic provider resolution."""

import pytest
from sqlalchemy import select

from backend.app.config import settings
from backend.app.models import Role as RoleModel, User
from schemas.user import mask_api_key
from services.text_generation import (
    GeminiTextGenerationProvider,
    OpenAITextGenerationProvider,
    get_text_generation_provider,
    resolve_user_api_key,
)
from utils.crypto import decrypt_value, encrypt_value


def _register_and_login(client, email="byok_student@example.com", password="Strong-password-123!"):
    client.post(
        "/api/auth/register",
        json={"name": "BYOK Student", "email": email, "password": password},
    )
    login_res = client.post(
        "/api/auth/login",
        data={"username": email, "password": password},
    )
    token = login_res.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_mask_api_key_helper() -> None:
    assert mask_api_key(None) is None
    assert mask_api_key("") is None
    assert mask_api_key("   ") is None
    assert mask_api_key("12345678") == "12...****"
    assert mask_api_key("sk-proj-1234567890abcdef") == "sk-pro...****"
    assert mask_api_key("AIzaSy-test-gemini-key-123") == "AIzaSy...****"


def test_get_api_keys_unauthenticated(api_context) -> None:
    response = api_context.client.get("/api/users/me/api-keys")
    assert response.status_code == 401


def test_get_and_put_api_keys_workflow(api_context) -> None:
    headers = _register_and_login(
        api_context.client,
        email="byok_student@example.com",
        password="Strong-password-123!",
    )

    # Initially no keys configured
    get_res = api_context.client.get("/api/users/me/api-keys", headers=headers)
    assert get_res.status_code == 200
    data = get_res.json()["data"]
    assert data["openai_api_key"] is None
    assert data["gemini_api_key"] is None
    assert data["anthropic_api_key"] is None
    assert data["has_openai_key"] is False
    assert data["has_gemini_key"] is False
    assert data["has_anthropic_key"] is False

    # Set personal API keys
    raw_openai = "sk-proj-test-openai-1234567890"
    raw_gemini = "AIzaSy-test-gemini-0987654321"
    raw_anthropic = "sk-ant-test-anthropic-11223344"

    put_res = api_context.client.put(
        "/api/users/me/api-keys",
        headers=headers,
        json={
            "openai_api_key": raw_openai,
            "gemini_api_key": raw_gemini,
            "anthropic_api_key": raw_anthropic,
        },
    )
    assert put_res.status_code == 200
    put_data = put_res.json()["data"]
    assert put_data["has_openai_key"] is True
    assert put_data["has_gemini_key"] is True
    assert put_data["has_anthropic_key"] is True
    assert put_data["openai_api_key"] == mask_api_key(raw_openai)
    assert put_data["gemini_api_key"] == mask_api_key(raw_gemini)
    assert put_data["anthropic_api_key"] == mask_api_key(raw_anthropic)

    # Verify keys are stored encrypted at rest in DB
    with api_context.session_factory() as session:
        user = session.scalar(
            select(User).where(User.email == "byok_student@example.com")
        )
        assert user is not None
        assert user.encrypted_openai_api_key is not None
        assert user.encrypted_openai_api_key != raw_openai
        assert decrypt_value(user.encrypted_openai_api_key) == raw_openai

        assert user.encrypted_gemini_api_key is not None
        assert user.encrypted_gemini_api_key != raw_gemini
        assert decrypt_value(user.encrypted_gemini_api_key) == raw_gemini

        assert user.encrypted_anthropic_api_key is not None
        assert user.encrypted_anthropic_api_key != raw_anthropic
        assert decrypt_value(user.encrypted_anthropic_api_key) == raw_anthropic

    # GET endpoint returns masked keys
    get_res2 = api_context.client.get("/api/users/me/api-keys", headers=headers)
    assert get_res2.status_code == 200
    data2 = get_res2.json()["data"]
    assert data2["has_openai_key"] is True
    assert data2["has_gemini_key"] is True
    assert data2["has_anthropic_key"] is True
    assert data2["openai_api_key"] == mask_api_key(raw_openai)

    # Clear one key and update another
    new_openai = "sk-proj-new-openai-99999999"
    put_res2 = api_context.client.put(
        "/api/users/me/api-keys",
        headers=headers,
        json={
            "openai_api_key": new_openai,
            "gemini_api_key": "",  # clear gemini
        },
    )
    assert put_res2.status_code == 200
    put_data2 = put_res2.json()["data"]
    assert put_data2["has_openai_key"] is True
    assert put_data2["has_gemini_key"] is False
    assert put_data2["has_anthropic_key"] is True  # preserved unchanged
    assert put_data2["gemini_api_key"] is None
    assert put_data2["openai_api_key"] == mask_api_key(new_openai)
    assert put_data2["anthropic_api_key"] == mask_api_key(raw_anthropic)

    with api_context.session_factory() as session:
        user = session.scalar(
            select(User).where(User.email == "byok_student@example.com")
        )
        assert user is not None
        assert user.encrypted_gemini_api_key is None
        assert decrypt_value(user.encrypted_openai_api_key) == new_openai
        assert decrypt_value(user.encrypted_anthropic_api_key) == raw_anthropic


def test_resolve_user_api_key_and_dynamic_provider(api_context) -> None:
    raw_openai = "sk-proj-custom-key-12345"
    raw_gemini = "AIzaSy-custom-gemini-67890"

    with api_context.session_factory() as session:
        role = session.scalar(select(RoleModel).where(RoleModel.name == "user"))
        assert role is not None
        user = User(
            name="Key Tester",
            email="key_tester@example.com",
            password_hash="hash",
            role_id=role.id,
            encrypted_openai_api_key=encrypt_value(raw_openai),
            encrypted_gemini_api_key=encrypt_value(raw_gemini),
        )
        session.add(user)
        session.commit()

        # Decrypts personal key for gemini and openai
        assert resolve_user_api_key("gemini", user=user) == raw_gemini
        assert resolve_user_api_key("openai", user=user) == raw_openai
        # Anthropic not configured on user -> returns None (will fallback to global env)
        assert resolve_user_api_key("claude", user=user) is None
        assert resolve_user_api_key("anthropic", user=user) is None

        # Instantiating provider with user uses decrypted key
        provider = get_text_generation_provider(
            effective_model="gemini:gemini-3.6-flash",
            user=user,
        )
        assert provider is not None

        # Explicit key override takes highest priority
        assert (
            resolve_user_api_key(
                "gemini", user=user, explicit_key="AIzaSy-explicit-override"
            )
            == "AIzaSy-explicit-override"
        )

        # Corrupted key falls back to None gracefully
        user.encrypted_gemini_api_key = "corrupted-ciphertext-123"
        assert resolve_user_api_key("gemini", user=user) is None
