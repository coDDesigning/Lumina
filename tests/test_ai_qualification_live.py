"""Opt-in live qualification test suite for hosted AI providers.

Exercised against real API endpoints when RUN_LIVE_AI_QUALIFICATION=true
and corresponding provider API keys (GEMINI_API_KEY, OPENAI_API_KEY,
ANTHROPIC_API_KEY, NVIDIA_API_KEY)
are configured in the environment.

Skipped unconditionally in default unit test runs to prevent network flakiness,
latency, and unexpected API cost in CI.
"""

import os
import pytest

from services.text_generation import (
    ClaudeTextGenerationProvider,
    GeminiTextGenerationProvider,
    OpenAITextGenerationProvider,
    NvidiaNimTextGenerationProvider,
)

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_LIVE_AI_QUALIFICATION", "").lower() not in {"true", "1", "yes"},
    reason="Opt-in live AI qualification requires RUN_LIVE_AI_QUALIFICATION=true and credentials",
)


class TestLiveGeminiProvider:
    @pytest.fixture(autouse=True)
    def check_key(self):
        if not os.getenv("GEMINI_API_KEY"):
            pytest.skip("GEMINI_API_KEY is not set")

    def test_live_gemini_text_generation(self):
        provider = GeminiTextGenerationProvider()
        text, meta = provider.generate_text_with_metadata(
            "Respond with the exact word: QUALIFIED"
        )
        assert "QUALIFIED" in text.upper()
        assert meta.provider == "gemini"
        assert meta.model is not None
        assert meta.total_tokens is not None and meta.total_tokens > 0
        assert meta.latency_ms > 0

    def test_live_gemini_json_generation(self):
        provider = GeminiTextGenerationProvider()
        data, meta = provider.generate_json_with_metadata(
            'Return a JSON object with keys "status" equal to "ok" and "provider" equal to "gemini".'
        )
        assert isinstance(data, dict)
        assert data.get("status") == "ok"
        assert meta.provider == "gemini"
        assert meta.total_tokens is not None and meta.total_tokens > 0


class TestLiveOpenAIProvider:
    @pytest.fixture(autouse=True)
    def check_key(self):
        if not os.getenv("OPENAI_API_KEY"):
            pytest.skip("OPENAI_API_KEY is not set")

    def test_live_openai_text_generation(self):
        provider = OpenAITextGenerationProvider()
        text, meta = provider.generate_text_with_metadata(
            "Respond with the exact word: QUALIFIED"
        )
        assert "QUALIFIED" in text.upper()
        assert meta.provider == "openai"
        assert meta.model is not None
        assert meta.total_tokens is not None and meta.total_tokens > 0
        assert meta.latency_ms > 0

    def test_live_openai_json_generation(self):
        provider = OpenAITextGenerationProvider()
        data, meta = provider.generate_json_with_metadata(
            'Return a JSON object with keys "status" equal to "ok" and "provider" equal to "openai".'
        )
        assert isinstance(data, dict)
        assert data.get("status") == "ok"
        assert meta.provider == "openai"
        assert meta.total_tokens is not None and meta.total_tokens > 0


class TestLiveClaudeProvider:
    @pytest.fixture(autouse=True)
    def check_key(self):
        if not os.getenv("ANTHROPIC_API_KEY"):
            pytest.skip("ANTHROPIC_API_KEY is not set")

    def test_live_claude_text_generation(self):
        provider = ClaudeTextGenerationProvider()
        text, meta = provider.generate_text_with_metadata(
            "Respond with the exact word: QUALIFIED"
        )
        assert "QUALIFIED" in text.upper()
        assert meta.provider == "claude"
        assert meta.model is not None
        assert meta.total_tokens is not None and meta.total_tokens > 0
        assert meta.latency_ms > 0

    def test_live_claude_json_generation(self):
        provider = ClaudeTextGenerationProvider()
        data, meta = provider.generate_json_with_metadata(
            'Return a JSON object with keys "status" equal to "ok" and "provider" equal to "claude".'
        )
        assert isinstance(data, dict)
        assert data.get("status") == "ok"
        assert meta.provider == "claude"
        assert meta.total_tokens is not None and meta.total_tokens > 0


class TestLiveNvidiaNimProvider:
    MODELS = (
        "deepseek-ai/deepseek-v4-flash-0731",
        "deepseek-ai/deepseek-v4-pro-0813",
        "nvidia/nemotron-3-ultra-550b-a55b",
        "nvidia/nemotron-3.5-lightning-30b-a3b",
    )

    @pytest.fixture(autouse=True)
    def check_key(self):
        if not os.getenv("NVIDIA_API_KEY"):
            pytest.skip("NVIDIA_API_KEY is not set")

    @pytest.mark.parametrize("model", MODELS)
    def test_live_nvidia_nim_turkish_text_generation(self, model: str):
        provider = NvidiaNimTextGenerationProvider(model=model)
        text, meta = provider.generate_text_with_metadata(
            'Yalnızca şu Türkçe cümleyi yaz: "Türkçe yeterlilik başarılı."'
        )
        assert "Türkçe yeterlilik başarılı." in text
        assert meta.provider == "nvidia_nim"
        assert meta.model == model
        assert meta.total_tokens is not None and meta.total_tokens > 0
        assert meta.latency_ms > 0

    @pytest.mark.parametrize("model", MODELS)
    def test_live_nvidia_nim_json_generation(self, model: str):
        provider = NvidiaNimTextGenerationProvider(model=model)
        data, meta = provider.generate_json_with_metadata(
            'Yalnızca bir JSON nesnesi döndür. "status" alanı "ok", '
            '"language" alanı "tr" ve "message" alanı '
            '"Türkçe doğrulandı" olsun.'
        )
        assert data == {
            "status": "ok",
            "language": "tr",
            "message": "Türkçe doğrulandı",
        }
        assert meta.provider == "nvidia_nim"
        assert meta.model == model
        assert meta.total_tokens is not None and meta.total_tokens > 0
