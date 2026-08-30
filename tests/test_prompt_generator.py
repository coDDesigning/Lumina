import routes.prompt_generator as prompt_generator_route
from sqlalchemy import select

from backend.app.models import AiUsageLog
from schemas.ai_usage import ErrorCategory, GenerationType
from services.prompt_generator import (
    PromptGenerationError,
    PromptGeneratorService,
)
from services.text_generation import GenerationMetadata, TextGenerationError


def _valid_prompt_payload() -> dict[str, object]:
    return {
        "generated_prompt": (
            "Create a structured study activity based on the user's request."
        )
    }


def test_build_prompt_inserts_user_request() -> None:
    prompt = PromptGeneratorService.build_prompt("Generate difficult exam questions.")

    assert "{{TEXT}}" not in prompt
    assert "Generate difficult exam questions." in prompt


def test_generate_returns_validated_prompt() -> None:
    class FakeProvider:
        def generate_json(self, prompt: str) -> dict[str, object]:
            assert "Generate difficult exam questions." in prompt
            return _valid_prompt_payload()

    result = PromptGeneratorService.generate(
        "Generate difficult exam questions.",
        FakeProvider(),
    )

    assert result.generated_prompt == (
        "Create a structured study activity based on the user's request."
    )


def test_generate_wraps_text_generation_error() -> None:
    class FakeProvider:
        def generate_json(self, prompt: str) -> dict[str, object]:
            raise TextGenerationError("Provider failed")

    try:
        PromptGeneratorService.generate(
            "Generate a quiz prompt.",
            FakeProvider(),
        )
    except PromptGenerationError as exc:
        assert "Text generation provider failed." in str(exc)
    else:
        raise AssertionError("Expected PromptGenerationError")


def test_generate_rejects_invalid_prompt_structure() -> None:
    class FakeProvider:
        def generate_json(self, prompt: str) -> dict[str, object]:
            return {}

    try:
        PromptGeneratorService.generate(
            "Generate a summary prompt.",
            FakeProvider(),
        )
    except PromptGenerationError as exc:
        assert "invalid structure" in str(exc)
    else:
        raise AssertionError("Expected PromptGenerationError")


def test_prompt_generator_endpoint_returns_generated_prompt(
    upload_api,
    monkeypatch,
) -> None:
    class FakeProvider:
        def generate_json(self, prompt: str) -> dict[str, object]:
            assert "Generate a concise study guide." in prompt
            return {
                "generated_prompt": (
                    "Create a concise study guide with key concepts and examples."
                )
            }

    monkeypatch.setattr(
        prompt_generator_route,
        "get_text_generation_provider",
        lambda *args, **kwargs: FakeProvider(),
    )

    response = upload_api.client.post(
        "/api/prompt-generator",
        json={
            "description": "Generate a concise study guide.",
        },
        headers=upload_api.authorization,
    )

    assert response.status_code == 200

    payload = response.json()

    assert payload["success"] is True
    assert payload["message"] == "Prompt generated successfully"
    assert payload["data"]["generated_prompt"] == (
        "Create a concise study guide with key concepts and examples."
    )
    with upload_api.session_factory() as session:
        usage = session.scalar(
            select(AiUsageLog).where(
                AiUsageLog.user_id == upload_api.user_id,
                AiUsageLog.generation_type == GenerationType.PROMPT_GENERATOR.value,
            )
        )
        assert usage is not None
        assert usage.success is True


def test_prompt_generator_endpoint_persists_failure_usage(
    upload_api,
    monkeypatch,
) -> None:
    class FailingProvider:
        def generate_json(self, prompt: str) -> dict[str, object]:
            raise TextGenerationError("Provider failed")

    monkeypatch.setattr(
        prompt_generator_route,
        "get_text_generation_provider",
        lambda *args, **kwargs: FailingProvider(),
    )
    monkeypatch.setattr(
        prompt_generator_route,
        "resolve_effective_model",
        lambda *args, **kwargs: "gemini:test-model",
    )

    response = upload_api.client.post(
        "/api/prompt-generator",
        json={"description": "Generate a concise study guide."},
        headers=upload_api.authorization,
    )

    assert response.status_code == 500
    with upload_api.session_factory() as session:
        usage = session.scalar(
            select(AiUsageLog).where(
                AiUsageLog.user_id == upload_api.user_id,
                AiUsageLog.generation_type == GenerationType.PROMPT_GENERATOR.value,
            )
        )
        assert usage is not None
        assert usage.success is False
        assert usage.error_category == ErrorCategory.PROVIDER_ERROR.value
        assert f"{usage.provider}:{usage.model}" == "gemini:test-model"


def test_prompt_generator_endpoint_logs_unexpected_provider_failure(
    upload_api,
    monkeypatch,
) -> None:
    class FailingProvider:
        def generate_json(self, prompt: str) -> dict[str, object]:
            raise RuntimeError("Unexpected provider adapter failure")

    monkeypatch.setattr(
        prompt_generator_route,
        "get_text_generation_provider",
        lambda *args, **kwargs: FailingProvider(),
    )
    monkeypatch.setattr(
        prompt_generator_route,
        "resolve_effective_model",
        lambda *args, **kwargs: "gemini:test-model",
    )

    response = upload_api.client.post(
        "/api/prompt-generator",
        json={"description": "Generate a concise study guide."},
        headers=upload_api.authorization,
    )

    assert response.status_code == 500
    with upload_api.session_factory() as session:
        usage = session.scalar(
            select(AiUsageLog).where(
                AiUsageLog.user_id == upload_api.user_id,
                AiUsageLog.generation_type == GenerationType.PROMPT_GENERATOR.value,
            )
        )
        assert usage is not None
        assert usage.success is False
        assert usage.error_category == ErrorCategory.UNKNOWN_ERROR.value
        assert f"{usage.provider}:{usage.model}" == "gemini:test-model"


def test_prompt_generator_invalid_fallback_output_uses_actual_model(
    upload_api,
    monkeypatch,
) -> None:
    class InvalidFallbackProvider:
        def generate_json_with_metadata(
            self, prompt: str
        ) -> tuple[dict[str, object], GenerationMetadata]:
            return {}, GenerationMetadata(provider="gemini", model="fallback-model")

    monkeypatch.setattr(
        prompt_generator_route,
        "get_text_generation_provider",
        lambda *args, **kwargs: InvalidFallbackProvider(),
    )
    monkeypatch.setattr(
        prompt_generator_route,
        "resolve_effective_model",
        lambda *args, **kwargs: "ollama:requested-model",
    )

    response = upload_api.client.post(
        "/api/prompt-generator",
        json={"description": "Generate a concise study guide."},
        headers=upload_api.authorization,
    )

    assert response.status_code == 500
    with upload_api.session_factory() as session:
        usage = session.scalar(
            select(AiUsageLog).where(
                AiUsageLog.user_id == upload_api.user_id,
                AiUsageLog.generation_type == GenerationType.PROMPT_GENERATOR.value,
            )
        )
        assert usage is not None
        assert f"{usage.provider}:{usage.model}" == "gemini:fallback-model"


def test_prompt_generator_endpoint_requires_authentication(
    api_context,
) -> None:
    response = api_context.client.post(
        "/api/prompt-generator",
        json={
            "description": "Generate a quiz prompt.",
        },
    )

    assert response.status_code == 401


def test_prompt_generator_endpoint_rejects_unavailable_model(
    upload_api,
) -> None:
    from utils.ai_errors import ERROR_CODE_HEADER, PUBLIC_MESSAGES, AiErrorCode

    response = upload_api.client.post(
        "/api/prompt-generator",
        json={
            "description": "Generate a prompt.",
            "model": "nonexistent:model",
        },
        headers=upload_api.authorization,
    )

    assert response.status_code == 400
    assert (
        response.headers.get(ERROR_CODE_HEADER) == AiErrorCode.UNAVAILABLE_MODEL.value
    )
    assert response.json()["detail"] == PUBLIC_MESSAGES[AiErrorCode.UNAVAILABLE_MODEL]


def test_prompt_generator_endpoint_rejects_json_incompatible_model(
    upload_api, monkeypatch
) -> None:
    from types import SimpleNamespace
    import services.text_generation as text_gen
    from utils.ai_errors import ERROR_CODE_HEADER, PUBLIC_MESSAGES, AiErrorCode

    fake_settings = SimpleNamespace(
        ai_available_vendors=("ollama",),
        ai_default_model="ollama:text-only",
        ai_model_catalog={
            "ollama": [
                {
                    "model": "text-only",
                    "json_mode": False,
                    "context_window": 8192,
                    "vision": False,
                }
            ]
        },
    )
    monkeypatch.setattr(text_gen, "settings", fake_settings)

    response = upload_api.client.post(
        "/api/prompt-generator",
        json={
            "description": "Generate a prompt.",
            "model": "ollama:text-only",
        },
        headers=upload_api.authorization,
    )

    assert response.status_code == 400
    assert (
        response.headers.get(ERROR_CODE_HEADER) == AiErrorCode.INCOMPATIBLE_MODEL.value
    )
    assert response.json()["detail"] == PUBLIC_MESSAGES[AiErrorCode.INCOMPATIBLE_MODEL]
