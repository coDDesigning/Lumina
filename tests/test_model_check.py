import json
import logging
from dataclasses import replace
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

import services.image_understanding as image_understanding
import services.model_check as model_check
import services.text_generation as text_generation
import utils.deps as deps
from backend.app.models import AiUsageLog, User
from main import app
from services.text_generation import UnavailableModelError


def _ollama_settings(
    *, base_url: str = "http://ollama.test:11434", vision: bool = False
):
    return SimpleNamespace(
        ai_available_vendors=("ollama",),
        ai_default_model="ollama:llama3.1",
        ai_model_catalog={
            "ollama": [
                {
                    "model": "llama3.1",
                    "json_mode": True,
                    "context_window": 8192,
                    "vision": vision,
                }
            ]
        },
        gemini_api_key=None,
        openai_api_key=None,
        anthropic_api_key=None,
        ollama_base_url=base_url,
        ollama_model="llama3.1",
        ai_generation_timeout_seconds=42,
        ollama_temperature=0.2,
        ollama_top_p=0.9,
        ollama_num_ctx=8192,
        ollama_num_predict=4096,
        ollama_repeat_penalty=1.1,
        ollama_think=False,
    )


_GEMINI_NO_KEY_SETTINGS = SimpleNamespace(
    ai_available_vendors=("gemini",),
    ai_default_model="gemini:gemini-x",
    ai_model_catalog={
        "gemini": [
            {
                "model": "gemini-x",
                "json_mode": True,
                "context_window": 32768,
                "vision": False,
            }
        ]
    },
    gemini_api_key=None,
    openai_api_key=None,
    anthropic_api_key=None,
    ollama_base_url="http://127.0.0.1:11434",
)

_UNAVAILABLE_VENDOR_SETTINGS = SimpleNamespace(
    ai_available_vendors=("ollama",),
    ai_default_model="ollama:llama3.1",
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
                "model": "gpt-5.6-terra",
                "json_mode": True,
                "context_window": 1_048_576,
                "vision": True,
            }
        ],
    },
    gemini_api_key=None,
    openai_api_key=None,
    anthropic_api_key=None,
    ollama_base_url="http://127.0.0.1:11434",
)


def _patch_settings(monkeypatch, fake_settings) -> None:
    monkeypatch.setattr(text_generation, "settings", fake_settings)
    monkeypatch.setattr(model_check, "settings", fake_settings)


def _ollama_handler(
    *,
    tags_json=None,
    tags_status: int = 200,
    tags_text: str | None = None,
    tags_exc=None,
    generate_json_body=None,
    generate_status: int = 200,
    generate_exc=None,
    show_capabilities=None,
    recorder: list[str] | None = None,
):
    def handler(request: httpx.Request) -> httpx.Response:
        if recorder is not None:
            recorder.append(request.url.path)
        if request.url.path == "/api/tags":
            if tags_exc is not None:
                raise tags_exc("mock failure", request=request)
            if tags_text is not None:
                return httpx.Response(tags_status, text=tags_text)
            return httpx.Response(
                tags_status, json=tags_json if tags_json is not None else {"models": []}
            )
        if request.url.path == "/api/generate":
            if generate_exc is not None:
                raise generate_exc("mock failure", request=request)
            return httpx.Response(
                generate_status,
                json=generate_json_body if generate_json_body is not None else {},
            )
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"capabilities": show_capabilities or []})
        return httpx.Response(404)

    return handler


def _install_ollama_transport(monkeypatch, handler) -> httpx.Client:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(model_check, "_get_shared_http_client", lambda: client)
    monkeypatch.setattr(text_generation, "_get_shared_http_client", lambda: client)
    monkeypatch.setattr(image_understanding, "_get_shared_http_client", lambda: client)
    monkeypatch.setattr(image_understanding, "_VISION_CAPABILITY", {})
    return client


def _ollama_tags_success(model: str = "llama3.1") -> dict:
    return {"models": [{"name": f"{model}:latest", "model": f"{model}:latest"}]}


def _ollama_generate_success(payload: dict | None = None) -> dict:
    return {
        "model": "llama3.1",
        "response": json.dumps(payload or {"status": "ok"}),
        "done": True,
        "prompt_eval_count": 12,
        "eval_count": 4,
    }


def _post_test(client, headers, model_id):
    return client.post(
        "/api/models/test",
        json={"model_id": model_id},
        headers=headers,
    )


def test_model_test_route_requires_authentication() -> None:
    client = TestClient(app)
    response = client.post("/api/models/test", json={"model_id": None})
    assert response.status_code == 401


def test_model_test_rejects_an_overlong_model_id(authz_api) -> None:
    response = _post_test(authz_api.client, authz_api.authorization_a, "x" * 201)
    assert response.status_code == 422


def test_a_successful_ollama_check_reports_ok_and_latency(
    authz_api, monkeypatch
) -> None:
    _patch_settings(monkeypatch, _ollama_settings())
    _install_ollama_transport(
        monkeypatch,
        _ollama_handler(
            tags_json=_ollama_tags_success(),
            generate_json_body=_ollama_generate_success(),
        ),
    )

    response = _post_test(
        authz_api.client, authz_api.authorization_a, "ollama:llama3.1"
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["ok"] is True
    assert data["provider"] == "ollama"
    assert data["model_id"] == "ollama:llama3.1"
    assert data["error_code"] is None
    assert data["latency_ms"] is not None
    assert data["latency_ms"] >= 0
    assert data["supports_vision"] is None
    assert data["base_url"] is None
    assert data["base_url_fallback"] is None


def test_a_successful_check_leaves_credits_and_usage_logs_untouched(
    authz_api, monkeypatch
) -> None:
    _patch_settings(monkeypatch, _ollama_settings())
    _install_ollama_transport(
        monkeypatch,
        _ollama_handler(
            tags_json=_ollama_tags_success(),
            generate_json_body=_ollama_generate_success(),
        ),
    )

    with authz_api.session_factory() as session:
        credits_before = session.get(User, authz_api.user_a_id).credits
        usage_before = session.scalar(select(func.count()).select_from(AiUsageLog))

    response = _post_test(
        authz_api.client, authz_api.authorization_a, "ollama:llama3.1"
    )
    assert response.status_code == 200
    assert response.json()["data"]["ok"] is True

    with authz_api.session_factory() as session:
        credits_after = session.get(User, authz_api.user_a_id).credits
        usage_after = session.scalar(select(func.count()).select_from(AiUsageLog))

    assert credits_after == credits_before
    assert usage_after == usage_before


def test_connection_refused_is_reported_as_unreachable(authz_api, monkeypatch) -> None:
    _patch_settings(monkeypatch, _ollama_settings())
    _install_ollama_transport(monkeypatch, _ollama_handler(tags_exc=httpx.ConnectError))

    response = _post_test(
        authz_api.client, authz_api.authorization_a, "ollama:llama3.1"
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["ok"] is False
    assert data["error_code"] == "unreachable"


def test_the_admin_unreachable_message_names_the_configured_address(
    authz_api, monkeypatch
) -> None:
    _patch_settings(monkeypatch, _ollama_settings())
    _install_ollama_transport(monkeypatch, _ollama_handler(tags_exc=httpx.ConnectError))

    response = _post_test(
        authz_api.client, authz_api.authorization_admin, "ollama:llama3.1"
    )

    data = response.json()["data"]
    assert data["error_code"] == "unreachable"
    assert "http://ollama.test:11434" in data["message"]
    assert "OLLAMA_BASE_URL" in data["message"]


def test_the_non_admin_unreachable_message_omits_the_address(
    authz_api, monkeypatch
) -> None:
    _patch_settings(monkeypatch, _ollama_settings())
    _install_ollama_transport(monkeypatch, _ollama_handler(tags_exc=httpx.ConnectError))

    response = _post_test(
        authz_api.client, authz_api.authorization_a, "ollama:llama3.1"
    )

    data = response.json()["data"]
    assert data["error_code"] == "unreachable"
    assert "http://ollama.test:11434" not in data["message"]


def test_a_slow_ollama_server_is_reported_as_timeout(authz_api, monkeypatch) -> None:
    _patch_settings(monkeypatch, _ollama_settings())
    _install_ollama_transport(monkeypatch, _ollama_handler(tags_exc=httpx.ReadTimeout))

    response = _post_test(
        authz_api.client, authz_api.authorization_a, "ollama:llama3.1"
    )

    data = response.json()["data"]
    assert data["ok"] is False
    assert data["error_code"] == "timeout"


def test_ollama_401_on_tags_is_reported_as_auth(authz_api, monkeypatch) -> None:
    _patch_settings(monkeypatch, _ollama_settings())
    _install_ollama_transport(
        monkeypatch, _ollama_handler(tags_status=401, tags_json={})
    )

    response = _post_test(
        authz_api.client, authz_api.authorization_a, "ollama:llama3.1"
    )

    data = response.json()["data"]
    assert data["ok"] is False
    assert data["error_code"] == "auth"


def test_a_non_json_tags_response_is_reported_as_bad_response(
    authz_api, monkeypatch
) -> None:
    _patch_settings(monkeypatch, _ollama_settings())
    _install_ollama_transport(monkeypatch, _ollama_handler(tags_text="not json"))

    response = _post_test(
        authz_api.client, authz_api.authorization_a, "ollama:llama3.1"
    )

    data = response.json()["data"]
    assert data["ok"] is False
    assert data["error_code"] == "bad_response"


def test_a_model_missing_from_tags_is_model_not_found_and_skips_generate(
    authz_api, monkeypatch
) -> None:
    _patch_settings(monkeypatch, _ollama_settings())
    recorder: list[str] = []
    _install_ollama_transport(
        monkeypatch,
        _ollama_handler(
            tags_json={"models": [{"name": "other-model:latest"}]}, recorder=recorder
        ),
    )

    response = _post_test(
        authz_api.client, authz_api.authorization_a, "ollama:llama3.1"
    )

    data = response.json()["data"]
    assert data["ok"] is False
    assert data["error_code"] == "model_not_found"
    assert "ollama pull llama3.1" in data["message"]
    assert recorder == ["/api/tags"]


def test_an_unknown_model_id_is_reported_as_unavailable(authz_api, monkeypatch) -> None:
    _patch_settings(monkeypatch, _ollama_settings())

    response = _post_test(
        authz_api.client, authz_api.authorization_a, "nonexistent:model"
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["ok"] is False
    assert data["error_code"] == "unavailable"


def test_a_cloud_vendor_without_a_key_is_reported_as_unavailable(
    authz_api, monkeypatch
) -> None:
    _patch_settings(monkeypatch, _UNAVAILABLE_VENDOR_SETTINGS)

    response = _post_test(
        authz_api.client, authz_api.authorization_a, "openai:gpt-5.6-terra"
    )

    data = response.json()["data"]
    assert data["ok"] is False
    assert data["error_code"] == "unavailable"


def test_a_missing_provider_api_key_is_reported_as_auth(authz_api, monkeypatch) -> None:
    _patch_settings(monkeypatch, _GEMINI_NO_KEY_SETTINGS)

    response = _post_test(
        authz_api.client, authz_api.authorization_a, "gemini:gemini-x"
    )

    data = response.json()["data"]
    assert data["ok"] is False
    assert data["error_code"] == "auth"


def test_ollama_rate_limit_on_generate_is_reported_as_rate_limited(
    authz_api, monkeypatch
) -> None:
    _patch_settings(monkeypatch, _ollama_settings())
    _install_ollama_transport(
        monkeypatch,
        _ollama_handler(
            tags_json=_ollama_tags_success(), generate_status=429, generate_json_body={}
        ),
    )

    response = _post_test(
        authz_api.client, authz_api.authorization_a, "ollama:llama3.1"
    )

    data = response.json()["data"]
    assert data["ok"] is False
    assert data["error_code"] == "rate_limited"


def test_an_ollama_server_error_on_generate_is_reported_as_bad_response(
    authz_api, monkeypatch
) -> None:
    _patch_settings(monkeypatch, _ollama_settings())
    _install_ollama_transport(
        monkeypatch,
        _ollama_handler(
            tags_json=_ollama_tags_success(), generate_status=500, generate_json_body={}
        ),
    )

    response = _post_test(
        authz_api.client, authz_api.authorization_a, "ollama:llama3.1"
    )

    data = response.json()["data"]
    assert data["ok"] is False
    assert data["error_code"] == "bad_response"


def test_the_ollama_address_and_fallback_are_admin_only(authz_api, monkeypatch) -> None:
    _patch_settings(monkeypatch, _ollama_settings())
    _install_ollama_transport(
        monkeypatch,
        _ollama_handler(
            tags_json=_ollama_tags_success(),
            generate_json_body=_ollama_generate_success(),
        ),
    )

    non_admin = _post_test(
        authz_api.client, authz_api.authorization_a, "ollama:llama3.1"
    ).json()["data"]
    assert non_admin["base_url"] is None
    assert non_admin["base_url_fallback"] is None

    admin = _post_test(
        authz_api.client, authz_api.authorization_admin, "ollama:llama3.1"
    ).json()["data"]
    assert admin["base_url"] == "http://ollama.test:11434"
    assert admin["base_url_fallback"] is False


def test_an_invalid_configured_ollama_url_is_reported_as_a_fallback(
    authz_api, monkeypatch
) -> None:
    _patch_settings(monkeypatch, _ollama_settings(base_url="not-a-valid-url"))
    _install_ollama_transport(
        monkeypatch,
        _ollama_handler(
            tags_json=_ollama_tags_success(),
            generate_json_body=_ollama_generate_success(),
        ),
    )

    response = _post_test(
        authz_api.client, authz_api.authorization_admin, "ollama:llama3.1"
    )

    data = response.json()["data"]
    assert data["ok"] is True
    assert data["base_url"] == "http://127.0.0.1:11434"
    assert data["base_url_fallback"] is True


def test_a_vision_capable_model_reports_supports_vision_true(
    authz_api, monkeypatch
) -> None:
    _patch_settings(monkeypatch, _ollama_settings(vision=True))
    _install_ollama_transport(
        monkeypatch,
        _ollama_handler(
            tags_json=_ollama_tags_success(),
            generate_json_body=_ollama_generate_success(),
            show_capabilities=["completion", "vision"],
        ),
    )

    response = _post_test(
        authz_api.client, authz_api.authorization_a, "ollama:llama3.1"
    )

    data = response.json()["data"]
    assert data["ok"] is True
    assert data["supports_vision"] is True


def test_a_text_only_model_reports_supports_vision_false(
    authz_api, monkeypatch
) -> None:
    _patch_settings(monkeypatch, _ollama_settings(vision=True))
    _install_ollama_transport(
        monkeypatch,
        _ollama_handler(
            tags_json=_ollama_tags_success(),
            generate_json_body=_ollama_generate_success(),
            show_capabilities=["completion"],
        ),
    )

    response = _post_test(
        authz_api.client, authz_api.authorization_a, "ollama:llama3.1"
    )

    data = response.json()["data"]
    assert data["ok"] is True
    assert data["supports_vision"] is False


def test_resolve_ollama_base_url_with_fallback_reports_a_valid_configured_url() -> None:
    from services.ollama import resolve_ollama_base_url_with_fallback

    assert resolve_ollama_base_url_with_fallback("http://example.test:11434") == (
        "http://example.test:11434",
        False,
    )


def test_resolve_ollama_base_url_with_fallback_reports_an_invalid_configured_url() -> (
    None
):
    from services.ollama import resolve_ollama_base_url_with_fallback

    assert resolve_ollama_base_url_with_fallback("not-a-valid-url") == (
        "http://127.0.0.1:11434",
        True,
    )


def test_resolve_ollama_base_url_with_fallback_does_not_flag_an_unset_url(
    monkeypatch,
) -> None:
    from services import ollama

    monkeypatch.setattr(ollama, "settings", SimpleNamespace(ollama_base_url=None))
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)

    assert ollama.resolve_ollama_base_url_with_fallback() == (
        "http://127.0.0.1:11434",
        False,
    )


def test_resolve_ollama_base_url_returns_the_first_element() -> None:
    from services import ollama

    assert ollama.resolve_ollama_base_url("not-a-valid-url") == "http://127.0.0.1:11434"
    assert (
        ollama.resolve_ollama_base_url("http://example.test:11434")
        == "http://example.test:11434"
    )


def test_get_single_text_generation_provider_builds_the_raw_provider(
    monkeypatch,
) -> None:
    _patch_settings(monkeypatch, _ollama_settings())

    provider = text_generation.get_single_text_generation_provider(
        "ollama:llama3.1", user=None, timeout_seconds=30.0
    )

    assert isinstance(provider, text_generation.OllamaTextGenerationProvider)
    assert not isinstance(provider, text_generation.ReliableTextGenerationProvider)
    assert provider._model == "llama3.1"
    assert provider._timeout_seconds == 30.0


def test_get_single_text_generation_provider_rejects_an_unresolvable_model(
    monkeypatch,
) -> None:
    _patch_settings(monkeypatch, _ollama_settings())

    with pytest.raises(UnavailableModelError):
        text_generation.get_single_text_generation_provider(
            "openai:does-not-exist", user=None, timeout_seconds=30.0
        )


def test_instantiate_provider_forwards_timeout_seconds_only_when_given(
    monkeypatch,
) -> None:
    _patch_settings(monkeypatch, _ollama_settings())

    with_timeout = text_generation._instantiate_provider(
        "ollama", "llama3.1", timeout_seconds=12.0
    )
    without_timeout = text_generation._instantiate_provider("ollama", "llama3.1")

    assert with_timeout._timeout_seconds == 12.0
    assert without_timeout._timeout_seconds == 42


def test_a_404_from_generate_never_tries_a_second_vendor(
    authz_api, monkeypatch
) -> None:
    fake_settings = _ollama_settings()
    fake_settings.ai_available_vendors = ("ollama", "gemini")
    fake_settings.ai_model_catalog["gemini"] = [
        {
            "model": "gemini-x",
            "json_mode": True,
            "context_window": 32768,
            "vision": False,
        }
    ]
    fake_settings.gemini_api_key = "fake-gemini-key"
    _patch_settings(monkeypatch, fake_settings)

    def _gemini_must_not_be_built(*args, **kwargs):
        raise AssertionError("Gemini must never be constructed in the check path")

    monkeypatch.setattr(
        text_generation.GeminiTextGenerationProvider,
        "__init__",
        _gemini_must_not_be_built,
    )

    recorder: list[str] = []
    _install_ollama_transport(
        monkeypatch,
        _ollama_handler(
            tags_json=_ollama_tags_success(),
            generate_status=404,
            generate_json_body={},
            recorder=recorder,
        ),
    )

    response = _post_test(
        authz_api.client, authz_api.authorization_a, "ollama:llama3.1"
    )

    data = response.json()["data"]
    assert data["ok"] is False
    assert data["error_code"] == "bad_response"
    assert recorder.count("/api/generate") == 1


def test_get_single_text_generation_provider_disables_openai_sdk_retries(
    monkeypatch,
) -> None:
    fake_settings = SimpleNamespace(
        ai_available_vendors=("openai",),
        ai_default_model="openai:gpt-x",
        ai_model_catalog={
            "openai": [
                {
                    "model": "gpt-x",
                    "json_mode": True,
                    "context_window": 128000,
                    "vision": False,
                }
            ]
        },
        gemini_api_key=None,
        openai_api_key="fake-openai-key",
        anthropic_api_key=None,
        ai_generation_timeout_seconds=42,
    )
    _patch_settings(monkeypatch, fake_settings)

    provider = text_generation.get_single_text_generation_provider(
        "openai:gpt-x", user=None, timeout_seconds=30.0
    )

    assert isinstance(provider, text_generation.OpenAITextGenerationProvider)
    assert provider._client.max_retries == 0


def test_get_single_text_generation_provider_disables_claude_sdk_retries(
    monkeypatch,
) -> None:
    fake_settings = SimpleNamespace(
        ai_available_vendors=("claude",),
        ai_default_model="claude:claude-x",
        ai_model_catalog={
            "claude": [
                {
                    "model": "claude-x",
                    "json_mode": True,
                    "context_window": 200000,
                    "vision": False,
                }
            ]
        },
        gemini_api_key=None,
        openai_api_key=None,
        anthropic_api_key="fake-anthropic-key",
        ai_generation_timeout_seconds=42,
    )
    _patch_settings(monkeypatch, fake_settings)

    provider = text_generation.get_single_text_generation_provider(
        "claude:claude-x", user=None, timeout_seconds=30.0
    )

    assert isinstance(provider, text_generation.ClaudeTextGenerationProvider)
    assert provider._client.max_retries == 0


def test_get_text_generation_provider_keeps_the_sdk_default_retry_count(
    monkeypatch,
) -> None:
    import openai

    fake_settings = SimpleNamespace(
        ai_available_vendors=("openai",),
        ai_default_model="openai:gpt-x",
        gemini_api_key=None,
        openai_api_key="fake-openai-key",
        anthropic_api_key=None,
        ai_generation_timeout_seconds=42,
        ai_generation_max_attempts=3,
        ai_generation_backoff_base_seconds=0.01,
        ai_generation_backoff_max_seconds=0.1,
        ai_generation_max_concurrency=10,
        ai_generation_overall_timeout_seconds=110,
    )
    monkeypatch.setattr(text_generation, "settings", fake_settings)

    provider = text_generation.get_text_generation_provider(
        effective_model="openai:gpt-x"
    )

    raw = provider.providers[0]
    assert isinstance(raw, text_generation.OpenAITextGenerationProvider)
    assert raw._client.max_retries == openai.OpenAI(api_key="probe").max_retries


def test_instantiate_provider_drops_unsupported_kwargs_but_keeps_api_key(
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}

    class MinimalProvider:
        def __init__(self, model=None, api_key=None):
            captured["model"] = model
            captured["api_key"] = api_key

    monkeypatch.setitem(
        text_generation.ProviderRegistry._registry,
        "minimal_test_provider",
        {
            "constructor": MinimalProvider,
            "default_model": "default-model",
            "requires_key": True,
            "vendor": "Test",
            "description": "test",
            "is_local": False,
        },
    )

    text_generation._instantiate_provider(
        "minimal_test_provider",
        "model-x",
        api_key="key123",
        timeout_seconds=30.0,
        max_retries=0,
    )

    assert captured == {"model": "model-x", "api_key": "key123"}


def test_ollama_userinfo_never_reaches_the_response_base_url(
    authz_api, monkeypatch
) -> None:
    _patch_settings(
        monkeypatch, _ollama_settings(base_url="http://admin:s3cr3t@ollama.test:11434")
    )
    _install_ollama_transport(
        monkeypatch,
        _ollama_handler(
            tags_json=_ollama_tags_success(),
            generate_json_body=_ollama_generate_success(),
        ),
    )

    response = _post_test(
        authz_api.client, authz_api.authorization_admin, "ollama:llama3.1"
    )

    data = response.json()["data"]
    assert data["ok"] is True
    assert data["base_url"] == "http://ollama.test:11434"
    assert "s3cr3t" not in json.dumps(data)
    assert "admin:" not in json.dumps(data)


def test_the_admin_unreachable_message_never_leaks_userinfo(
    authz_api, monkeypatch
) -> None:
    _patch_settings(
        monkeypatch, _ollama_settings(base_url="http://admin:s3cr3t@ollama.test:11434")
    )
    _install_ollama_transport(monkeypatch, _ollama_handler(tags_exc=httpx.ConnectError))

    response = _post_test(
        authz_api.client, authz_api.authorization_admin, "ollama:llama3.1"
    )

    data = response.json()["data"]
    assert data["error_code"] == "unreachable"
    assert "s3cr3t" not in data["message"]
    assert "ollama.test:11434" in data["message"]


def test_the_log_record_omits_provider_and_model_for_an_unresolved_model_id(
    authz_api, monkeypatch, caplog
) -> None:
    _patch_settings(monkeypatch, _ollama_settings())
    caplog.set_level(logging.INFO, logger="services.model_check")

    secret_looking_id = "super-secret-personal-note-that-is-not-a-model"
    response = _post_test(
        authz_api.client, authz_api.authorization_a, secret_looking_id
    )

    assert response.status_code == 200
    assert response.json()["data"]["error_code"] == "unavailable"

    records = [
        record
        for record in caplog.records
        if getattr(record, "event", None) == "ai_model_check"
    ]
    assert len(records) == 1
    assert getattr(records[0], "provider", None) is None
    assert getattr(records[0], "model", None) is None


def test_the_log_record_carries_provider_and_model_for_a_resolved_check(
    authz_api, monkeypatch, caplog
) -> None:
    _patch_settings(monkeypatch, _ollama_settings())
    _install_ollama_transport(
        monkeypatch,
        _ollama_handler(
            tags_json=_ollama_tags_success(),
            generate_json_body=_ollama_generate_success(),
        ),
    )
    caplog.set_level(logging.INFO, logger="services.model_check")

    response = _post_test(
        authz_api.client, authz_api.authorization_a, "ollama:llama3.1"
    )
    assert response.json()["data"]["ok"] is True

    records = [
        record
        for record in caplog.records
        if getattr(record, "event", None) == "ai_model_check"
    ]
    assert len(records) == 1
    assert records[0].provider == "ollama"
    assert records[0].model == "llama3.1"


def test_an_unverified_user_is_blocked_when_verification_is_required(
    authz_api, monkeypatch
) -> None:
    monkeypatch.setattr(
        deps, "settings", replace(deps.settings, email_verification_required=True)
    )

    response = _post_test(
        authz_api.client, authz_api.authorization_a, "ollama:llama3.1"
    )

    assert response.status_code == 403
    assert response.headers["X-Error-Code"] == "email_verification_required"
