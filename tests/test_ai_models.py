from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from main import app
import services.text_generation as text_generation
from services.text_generation import (
    IncompatibleModelError,
    UnavailableModelError,
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
    assert "cost_hint" in default_model
    assert isinstance(default_model["capabilities"], list)
    assert len(default_model["capabilities"]) >= 1
    assert "description" in default_model
    assert "is_local" in default_model
    assert "supports_json" in default_model
    assert "json_mode" in default_model
    assert "context_window" in default_model
    assert "vision" in default_model


def test_list_models_unauthenticated():
    client = TestClient(app)
    response = client.get("/api/models")
    assert response.status_code == 401


def test_resolve_effective_model_precedence(monkeypatch: pytest.MonkeyPatch):
    fake_settings = SimpleNamespace(
        ai_provider="gemini",
        ai_fallback_providers="ollama",
        ai_model_catalog={
            "gemini": [
                {
                    "model": "gemini-3.6-flash",
                    "json_mode": True,
                    "context_window": 32768,
                    "vision": False,
                }
            ],
            "ollama": [
                {
                    "model": "llama3.1",
                    "json_mode": True,
                    "context_window": 8192,
                    "vision": False,
                }
            ],
        },
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

    # 3. Invalid explicit override is rejected
    with pytest.raises(
        UnavailableModelError, match="Requested AI model is not available"
    ):
        resolve_effective_model(
            request_model="nonexistent:model",
            user_preferred_model=ollama_id,
        )

    # 4. No override or preference falls back to deployment default
    assert (
        resolve_effective_model(
            request_model=None,
            user_preferred_model=None,
        )
        == gemini_id
    )


def test_resolve_effective_model_capability_validation(
    monkeypatch: pytest.MonkeyPatch,
):
    fake_settings = SimpleNamespace(
        ai_provider="gemini",
        ai_fallback_providers="ollama",
        ai_model_catalog={
            "gemini": [
                {
                    "model": "gemini-3.6-flash",
                    "json_mode": True,
                    "context_window": 32768,
                    "vision": False,
                }
            ],
            "ollama": [
                {
                    "model": "llama3.1",
                    "json_mode": True,
                    "context_window": 8192,
                    "vision": False,
                }
            ],
        },
        gemini_api_key="fake-key",
        ollama_base_url="http://localhost:11434",
        ollama_model="llama3.1",
    )
    monkeypatch.setattr(text_generation, "settings", fake_settings)

    catalog = get_available_models()
    gemini_id = next(m["id"] for m in catalog if m["provider"] == "gemini")

    # Supported capability succeeds
    resolved = resolve_effective_model(
        request_model=gemini_id,
        required_capability="study_guide",
    )
    assert resolved == gemini_id

    # Unsupported capability on explicit request raises BadRequestException (400)
    with pytest.raises(text_generation.BadRequestException) as exc_info:
        resolve_effective_model(
            request_model=gemini_id,
            required_capability="unsupported_task_xyz",
        )
    assert "does not support" in str(exc_info.value.detail)


def test_get_text_generation_provider_honors_model(monkeypatch: pytest.MonkeyPatch):
    fake_settings = SimpleNamespace(
        ai_provider="ollama",
        ai_fallback_providers="gemini",
        gemini_api_key="fake-key",
        ollama_base_url="http://localhost:11434",
        ollama_model="llama3.1",
        ai_generation_timeout_seconds=60,
        ai_generation_max_attempts=3,
        ai_generation_backoff_base_seconds=0.01,
        ai_generation_backoff_max_seconds=0.1,
        ai_generation_max_concurrency=10,
    )
    monkeypatch.setattr(text_generation, "settings", fake_settings)

    provider = text_generation.get_text_generation_provider(
        effective_model="ollama:custom-llama"
    )
    assert hasattr(provider, "providers")
    primary = provider.providers[0]
    assert isinstance(primary, text_generation.OllamaTextGenerationProvider)
    assert primary._model == "custom-llama"


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


def test_available_models_distinguishes_multiple_models_for_same_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
                },
                {
                    "model": "qwen3:8b",
                    "json_mode": True,
                    "context_window": 32768,
                    "vision": False,
                },
            ]
        },
    )

    monkeypatch.setattr(text_generation, "settings", fake_settings)

    models = text_generation.get_available_models()

    assert len(models) == 2

    assert models[0]["id"] == "ollama:llama3.1"
    assert models[0]["provider"] == "ollama"
    assert models[0]["model"] == "llama3.1"
    assert models[0]["is_default"] is True
    assert models[0]["json_mode"] is True
    assert models[0]["context_window"] == 8192
    assert models[0]["vision"] is False

    assert models[1]["id"] == "ollama:qwen3:8b"
    assert models[1]["provider"] == "ollama"
    assert models[1]["model"] == "qwen3:8b"
    assert models[1]["is_default"] is False
    assert models[1]["json_mode"] is True
    assert models[1]["context_window"] == 32768
    assert models[1]["vision"] is False


def test_selected_model_is_passed_to_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
                },
                {
                    "model": "qwen3:8b",
                    "json_mode": True,
                    "context_window": 32768,
                    "vision": False,
                },
            ]
        },
        ai_generation_max_attempts=3,
        ai_generation_backoff_base_seconds=1.0,
        ai_generation_backoff_max_seconds=10.0,
        ai_generation_max_concurrency=10,
    )

    monkeypatch.setattr(text_generation, "settings", fake_settings)

    captured: list[tuple[str, str | None]] = []

    class DummyProvider:
        PROVIDER_NAME = "ollama"

        def generate_text(self, prompt: str) -> str:
            return "ok"

        def generate_json(self, prompt: str) -> dict[str, object]:
            return {}

        def generate_text_with_metadata(self, prompt: str):
            raise NotImplementedError

        def generate_json_with_metadata(self, prompt: str):
            raise NotImplementedError

    def fake_instantiate(
        provider_name: str,
        model_name: str | None = None,
    ):
        captured.append((provider_name, model_name))
        return DummyProvider()

    monkeypatch.setattr(
        text_generation,
        "_instantiate_provider",
        fake_instantiate,
    )

    text_generation.get_text_generation_provider("ollama:qwen3:8b")

    assert captured == [("ollama", "qwen3:8b")]


def test_stale_user_preference_falls_back_to_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
            ]
        },
    )

    monkeypatch.setattr(text_generation, "settings", fake_settings)

    assert (
        resolve_effective_model(
            request_model=None,
            user_preferred_model="ollama:removed-model",
        )
        == "ollama:llama3.1"
    )


def test_json_incompatible_model_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_settings = SimpleNamespace(
        ai_provider="ollama",
        ai_fallback_providers="",
        ai_model_catalog={
            "ollama": [
                {
                    "model": "text-only-model",
                    "json_mode": False,
                    "context_window": 8192,
                    "vision": False,
                }
            ]
        },
    )

    monkeypatch.setattr(text_generation, "settings", fake_settings)

    with pytest.raises(
        IncompatibleModelError,
        match="Requested AI model does not support JSON mode",
    ):
        text_generation.get_text_generation_provider(
            "ollama:text-only-model",
            require_json_mode=True,
        )
