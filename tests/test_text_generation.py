from types import SimpleNamespace

import services.text_generation as text_generation


def test_gemini_provider_parses_json_response(monkeypatch) -> None:
    class FakeModels:
        def generate_content(self, **kwargs):
            return SimpleNamespace(text='{"title": "Test Guide"}')

    class FakeClient:
        def __init__(self, api_key: str) -> None:
            self.models = FakeModels()

    monkeypatch.setattr(
        text_generation,
        "settings",
        SimpleNamespace(gemini_api_key="test-key"),
    )
    monkeypatch.setattr(text_generation.genai, "Client", FakeClient)

    provider = text_generation.GeminiTextGenerationProvider()

    result = provider.generate_json("Test prompt")

    assert result == {"title": "Test Guide"}


def test_gemini_provider_rejects_empty_response(monkeypatch) -> None:
    class FakeModels:
        def generate_content(self, **kwargs):
            return SimpleNamespace(text="")

    class FakeClient:
        def __init__(self, api_key: str) -> None:
            self.models = FakeModels()

    monkeypatch.setattr(
        text_generation,
        "settings",
        SimpleNamespace(gemini_api_key="test-key"),
    )
    monkeypatch.setattr(text_generation.genai, "Client", FakeClient)

    provider = text_generation.GeminiTextGenerationProvider()

    try:
        provider.generate_json("Test prompt")
    except text_generation.TextGenerationError as exc:
        assert "empty response" in str(exc)
    else:
        raise AssertionError("Expected TextGenerationError")


def test_get_text_generation_provider_returns_gemini(monkeypatch) -> None:
    monkeypatch.setattr(
        text_generation,
        "settings",
        SimpleNamespace(
            ai_provider="gemini",
            gemini_api_key="test-key",
        ),
    )

    class FakeGeminiProvider:
        pass

    monkeypatch.setattr(
        text_generation,
        "GeminiTextGenerationProvider",
        FakeGeminiProvider,
    )

    provider = text_generation.get_text_generation_provider()

    assert isinstance(provider, FakeGeminiProvider)


def test_get_text_generation_provider_rejects_unimplemented_provider(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        text_generation,
        "settings",
        SimpleNamespace(
            ai_provider="ollama",
            gemini_api_key=None,
        ),
    )

    try:
        text_generation.get_text_generation_provider()
    except text_generation.TextGenerationError as exc:
        assert "not implemented" in str(exc)
    else:
        raise AssertionError("Expected TextGenerationError")


def test_gemini_provider_returns_text_response(monkeypatch) -> None:
    class FakeModels:
        def generate_content(self, **kwargs):
            return SimpleNamespace(text="Generated tutor response")

    class FakeClient:
        def __init__(self, api_key: str) -> None:
            self.models = FakeModels()

    monkeypatch.setattr(
        text_generation,
        "settings",
        SimpleNamespace(gemini_api_key="test-key"),
    )
    monkeypatch.setattr(text_generation.genai, "Client", FakeClient)

    provider = text_generation.GeminiTextGenerationProvider()

    result = provider.generate_text("Test prompt")

    assert result == "Generated tutor response"


def test_gemini_provider_rejects_empty_text_response(monkeypatch) -> None:
    class FakeModels:
        def generate_content(self, **kwargs):
            return SimpleNamespace(text="")

    class FakeClient:
        def __init__(self, api_key: str) -> None:
            self.models = FakeModels()

    monkeypatch.setattr(
        text_generation,
        "settings",
        SimpleNamespace(gemini_api_key="test-key"),
    )
    monkeypatch.setattr(text_generation.genai, "Client", FakeClient)

    provider = text_generation.GeminiTextGenerationProvider()

    try:
        provider.generate_text("Test prompt")
    except text_generation.TextGenerationError as exc:
        assert "empty response" in str(exc)
    else:
        raise AssertionError("Expected TextGenerationError")
