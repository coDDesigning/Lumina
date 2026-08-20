from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from main import app
import services.text_generation as text_generation
from services.text_generation import (
    get_available_models,
    resolve_effective_model,
)


def test_list_available_models(authz_api):
    client = authz_api.client
    headers = authz_api.authorization_a

    response = client.get("/api/models", headers=headers)
    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert isinstance(payload["data"], list)
    assert len(payload["data"]) >= 1
    default_model = next((m for m in payload["data"] if m["is_default"]), None)
    assert default_model is not None
    assert "id" in default_model
    assert "provider" in default_model
    assert "model" in default_model
    assert "display_name" in default_model


def test_list_models_unauthenticated():
    client = TestClient(app)
    response = client.get("/api/models")
    assert response.status_code == 401


def test_resolve_effective_model_precedence(monkeypatch: pytest.MonkeyPatch):
    fake_settings = SimpleNamespace(
        ai_provider="gemini",
        ai_fallback_providers="ollama",
        gemini_api_key="fake-key",
        ollama_base_url="http://localhost:11434",
        ollama_model="llama3.1",
    )
    monkeypatch.setattr(text_generation, "settings", fake_settings)

    catalog = get_available_models()
    assert len(catalog) == 2
    gemini_id = next(m["id"] for m in catalog if m["provider"] == "gemini")
    ollama_id = next(m["id"] for m in catalog if m["provider"] == "ollama")

    # 1. Explicit override takes highest precedence
    assert (
        resolve_effective_model(
            request_model=ollama_id,
            user_preferred_model=gemini_id,
        )
        == ollama_id
    )

    # 2. User preferred model takes precedence over deployment default
    assert (
        resolve_effective_model(
            request_model=None,
            user_preferred_model=ollama_id,
        )
        == ollama_id
    )

    # 3. Invalid override falls back to user preference
    assert (
        resolve_effective_model(
            request_model="nonexistent:model",
            user_preferred_model=ollama_id,
        )
        == ollama_id
    )

    # 4. No override or preference falls back to deployment default
    assert (
        resolve_effective_model(
            request_model=None,
            user_preferred_model=None,
        )
        == gemini_id
    )


def test_update_preferred_model_valid_and_invalid(authz_api):
    client = authz_api.client
    headers = authz_api.authorization_a

    available_models = get_available_models()
    valid_model_id = available_models[0]["id"]

    # Valid model update
    res = client.put(
        f"/api/users/me/model?model_name={valid_model_id}", headers=headers
    )
    assert res.status_code == 200
    assert res.json()["data"]["preferred_model"] == valid_model_id

    # Invalid model update rejected with 400
    res_bad = client.put(
        "/api/users/me/model?model_name=unsupported_model_123", headers=headers
    )
    assert res_bad.status_code == 400
