"""Tests for Bring Your Own Key (BYOK) API endpoints and dynamic provider resolution."""

from types import SimpleNamespace
import pytest
from sqlalchemy import select

from backend.app.config import settings
from backend.app.models import Role as RoleModel, User
from schemas.user import mask_api_key
from services.text_generation import (
    GeminiTextGenerationProvider,
    OpenAITextGenerationProvider,
    PersonalKeyAuthError,
    TextGenerationAuthError,
    get_available_models,
    get_text_generation_provider,
    resolve_user_api_key,
)
from utils.ai_errors import ai_generation_http_exception
from utils.crypto import decrypt_value, encrypt_value


def _register_and_login(
    client, email="byok_student@example.com", password="Strong-password-123!"
):
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


def _create_admin_and_login(
    api_context, email="byok_admin@example.com", password="Strong-password-123!"
):
    api_context.client.post(
        "/api/auth/register",
        json={"name": "BYOK Admin", "email": email, "password": password},
    )
    with api_context.session_factory() as session:
        admin_role = session.scalar(
            select(RoleModel).where(RoleModel.name == "admin")
        )
        assert admin_role is not None
        user = session.scalar(select(User).where(User.email == email))
        assert user is not None
        user.role_id = admin_role.id
        session.commit()

    login_res = api_context.client.post(
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


def test_api_keys_forbidden_for_non_admins(api_context) -> None:
    # First create an admin so the next registration is guaranteed to be a regular user
    _create_admin_and_login(api_context, email="setup_admin@example.com")

    headers = _register_and_login(
        api_context.client,
        email="student_forbidden@example.com",
        password="Strong-password-123!",
    )

    get_res = api_context.client.get("/api/users/me/api-keys", headers=headers)
    assert get_res.status_code == 403
    assert "Admin access required" in get_res.json()["detail"]

    put_res = api_context.client.put(
        "/api/users/me/api-keys",
        headers=headers,
        json={"gemini_api_key": "AIzaSy-forbidden-test"},
    )
    assert put_res.status_code == 403
    assert "Admin access required" in put_res.json()["detail"]


def test_get_and_put_api_keys_workflow(api_context) -> None:
    headers = _create_admin_and_login(
        api_context,
        email="byok_admin_workflow@example.com",
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
            select(User).where(User.email == "byok_admin_workflow@example.com")
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
            select(User).where(User.email == "byok_admin_workflow@example.com")
        )
        assert user is not None
        assert user.encrypted_gemini_api_key is None
        assert decrypt_value(user.encrypted_openai_api_key) == new_openai
        assert decrypt_value(user.encrypted_anthropic_api_key) == raw_anthropic


def test_resolve_user_api_key_and_dynamic_provider(api_context) -> None:
    raw_openai = "sk-proj-custom-key-12345"
    raw_gemini = "AIzaSy-custom-gemini-67890"

    with api_context.session_factory() as session:
        role = session.scalar(select(RoleModel).where(RoleModel.name == "admin"))
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


def test_dynamic_ai_models_and_routing_for_admin_keys(api_context, monkeypatch) -> None:
    fake_settings = SimpleNamespace(
        ai_provider="ollama",
        ai_fallback_providers="",
        ai_model_catalog={
            "ollama": [
                {
                    "model": "llama3.1",
                    "json_mode": True,
                    "context_window": 8192,
                    "vision": False,
                }
            ],
            "gemini": [
                {
                    "model": "gemini-3.6-flash",
                    "json_mode": True,
                    "context_window": 1048576,
                    "vision": True,
                }
            ],
            "openai": [
                {
                    "model": "gpt-4o-mini",
                    "json_mode": True,
                    "context_window": 128000,
                    "vision": True,
                }
            ],
        },
        ai_generation_max_attempts=3,
        ai_generation_backoff_base_seconds=1.0,
        ai_generation_backoff_max_seconds=10.0,
        ai_generation_max_concurrency=10,
        ai_generation_overall_timeout_seconds=110,
        ai_generation_timeout_seconds=60,
        gemini_api_key="",
        ollama_model="llama3.1",
        ollama_base_url="http://localhost:11434",
        ollama_temperature=0.2,
        ollama_top_p=0.9,
        ollama_num_ctx=8192,
        ollama_num_predict=4096,
        ollama_repeat_penalty=1.1,
    )
    import services.text_generation as tg
    monkeypatch.setattr(tg, "settings", fake_settings)

    headers = _create_admin_and_login(
        api_context,
        email="dynamic_models_admin@example.com",
        password="Strong-password-123!",
    )

    # 1. Before configuring any personal keys: GET /api/models returns only ollama
    models_res = api_context.client.get("/api/models", headers=headers)
    assert models_res.status_code == 200
    initial_models = models_res.json()["data"]
    initial_ids = [m["id"] for m in initial_models]
    assert initial_ids == ["ollama:llama3.1"]

    # 2. Configure personal Gemini API key
    raw_gemini = "AIzaSy-dynamic-test-key-1234"
    put_keys_res = api_context.client.put(
        "/api/users/me/api-keys",
        headers=headers,
        json={"gemini_api_key": raw_gemini},
    )
    assert put_keys_res.status_code == 200

    # 3. GET /api/models now dynamically injects Google Gemini
    models_res2 = api_context.client.get("/api/models", headers=headers)
    assert models_res2.status_code == 200
    updated_models = models_res2.json()["data"]
    updated_ids = [m["id"] for m in updated_models]
    assert "ollama:llama3.1" in updated_ids
    assert "gemini:gemini-3.6-flash" in updated_ids

    # 4. Admin updates preferred model to Gemini
    put_model_res = api_context.client.put(
        "/api/users/me/model?model_name=gemini:gemini-3.6-flash",
        headers=headers,
    )
    assert put_model_res.status_code == 200
    assert put_model_res.json()["data"]["preferred_model"] == "gemini:gemini-3.6-flash"

    # 5. Verify provider construction routes to Gemini provider with decrypted personal key
    with api_context.session_factory() as session:
        user = session.scalar(
            select(User).where(User.email == "dynamic_models_admin@example.com")
        )
        assert user is not None
        provider = tg.get_text_generation_provider(
            effective_model=user.preferred_model,
            user=user,
        )
        assert provider is not None
        # Primary provider is GeminiTextGenerationProvider with personal key
        primary_provider = provider.providers[0]
        assert isinstance(primary_provider, GeminiTextGenerationProvider)
        assert primary_provider._model == "gemini-3.6-flash"
        assert primary_provider._client is not None
        assert resolve_user_api_key("gemini", user=user) == raw_gemini


def test_put_api_keys_strict_format_validation(api_context) -> None:
    headers = _create_admin_and_login(
        api_context,
        email="format_validation_admin@example.com",
        password="Strong-password-123!",
    )

    # 1. Invalid OpenAI key (missing sk- prefix)
    res_openai = api_context.client.put(
        "/api/users/me/api-keys",
        headers=headers,
        json={"openai_api_key": "invalid-openai-key-without-prefix"},
    )
    assert res_openai.status_code == 422
    assert "OpenAI API key must start with 'sk-'." in str(res_openai.json())

    # 2. Invalid Anthropic key (missing sk-ant- prefix)
    res_anthropic = api_context.client.put(
        "/api/users/me/api-keys",
        headers=headers,
        json={"anthropic_api_key": "sk-invalid-anthropic-key"},
    )
    assert res_anthropic.status_code == 422
    assert "Anthropic API key must start with 'sk-ant-'." in str(res_anthropic.json())

    # 3. Invalid Gemini key (special characters/whitespace)
    res_gemini = api_context.client.put(
        "/api/users/me/api-keys",
        headers=headers,
        json={"gemini_api_key": "invalid key with spaces!"},
    )
    assert res_gemini.status_code == 422
    assert "Gemini API key contains invalid characters." in str(res_gemini.json())


def test_personal_key_auth_error_disables_silent_fallback(api_context, monkeypatch) -> None:
    fake_settings = SimpleNamespace(
        ai_provider="ollama",
        ai_fallback_providers="",
        ai_model_catalog={
            "ollama": [
                {
                    "model": "llama3.1",
                    "json_mode": True,
                    "context_window": 8192,
                    "vision": False,
                }
            ],
            "openai": [
                {
                    "model": "gpt-4o-mini",
                    "json_mode": True,
                    "context_window": 128000,
                    "vision": True,
                }
            ],
        },
        ai_generation_max_attempts=3,
        ai_generation_backoff_base_seconds=1.0,
        ai_generation_backoff_max_seconds=10.0,
        ai_generation_max_concurrency=10,
        ai_generation_overall_timeout_seconds=110,
        ai_generation_timeout_seconds=60,
        openai_api_key="",
        ollama_model="llama3.1",
        ollama_base_url="http://localhost:11434",
        ollama_temperature=0.2,
        ollama_top_p=0.9,
        ollama_num_ctx=8192,
        ollama_num_predict=4096,
        ollama_repeat_penalty=1.1,
    )
    import services.text_generation as tg
    monkeypatch.setattr(tg, "settings", fake_settings)

    headers = _create_admin_and_login(
        api_context,
        email="auth_fallback_admin@example.com",
        password="Strong-password-123!",
    )

    # Configure an OpenAI key
    raw_openai = "sk-proj-invalid-or-expired-key"
    put_keys_res = api_context.client.put(
        "/api/users/me/api-keys",
        headers=headers,
        json={"openai_api_key": raw_openai},
    )
    assert put_keys_res.status_code == 200

    with api_context.session_factory() as session:
        user = session.scalar(
            select(User).where(User.email == "auth_fallback_admin@example.com")
        )
        assert user is not None

        # Build provider for OpenAI
        provider = tg.get_text_generation_provider(
            effective_model="openai:gpt-4o-mini",
            user=user,
        )
        assert provider is not None

        # Mock OpenAI provider generate_text to fail with 401 authentication error
        openai_provider = provider.providers[0]
        assert getattr(openai_provider, "is_personal_key", False) is True

        def failing_generate_text(prompt: str):
            raise TextGenerationAuthError("OpenAI authentication failed.")

        monkeypatch.setattr(openai_provider, "generate_text", failing_generate_text)
        monkeypatch.setattr(openai_provider, "generate_text_with_metadata", failing_generate_text)

        # Assert PersonalKeyAuthError is raised immediately and did NOT silently fallback to Ollama
        with pytest.raises(PersonalKeyAuthError) as exc_info:
            provider.generate_text("Test prompt")

        assert "Your personal OpenAI API key is invalid or expired." in str(exc_info.value)

        # Verify ai_generation_http_exception creates HTTP 401 with X-Error-Code: personal_key_invalid
        http_exc = ai_generation_http_exception(exc_info.value, feature="study_guide")
        assert http_exc.status_code == 401
        assert http_exc.detail == "Your personal OpenAI API key is invalid or expired."
        assert http_exc.headers.get("X-Error-Code") == "personal_key_invalid"

