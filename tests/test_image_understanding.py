# tests/test_image_understanding.py
"""Tests for image understanding providers, configuration, and extraction wiring."""

import base64
import hashlib
import io
import json
from types import SimpleNamespace

import httpx
import pymupdf
import pytest
from google.genai import errors as genai_errors

from backend.app.config import (
    AI_PROVIDER_GEMINI,
    AI_PROVIDER_OLLAMA,
    IMAGE_PROVIDER_NONE,
)
import services.image_understanding as image_understanding
from services.document_extraction import DocumentProcessingError, extract_document
from services.document_pipeline import (
    DisabledImageUnderstandingProvider,
    PageVisualAnalysisStatus,
    PipelineStage,
    TemporaryVisualServiceError,
    VisualAnalysisError,
    VisualAnalysisStatus,
    VisualDescription,
    VisualType,
)
from services.image_understanding import (
    GeminiImageUnderstandingProvider,
    OllamaImageUnderstandingProvider,
    configured_image_understanding_identity,
    get_image_understanding_provider,
)

# Valid 1x1 minimal PNG image bytes
VALID_PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06"
    b"\x00\x00\x00\x1f\x15c4\x00\x00\x00\rIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02"
    b"\xfe\xdc\xccY\xe7\x00\x00\x00\x00IEND\xaeB`\x82"
)

OLLAMA_VISION_SETTINGS = SimpleNamespace(
    image_provider="ollama",
    gemini_api_key=None,
    ollama_base_url="http://ollama.test:11434",
    ollama_image_model="llama3.2-vision",
    gemini_image_model="gemini-2.5-flash",
    image_understanding_timeout_seconds=30,
    image_understanding_max_bytes=10 * 1024 * 1024,
)

GEMINI_VISION_SETTINGS = SimpleNamespace(
    image_provider="gemini",
    gemini_api_key="test-key",
    ollama_base_url="http://ollama.test:11434",
    ollama_image_model="llama3.2-vision",
    gemini_image_model="gemini-2.5-flash",
    image_understanding_timeout_seconds=30,
    image_understanding_max_bytes=10 * 1024 * 1024,
)

DISABLED_VISION_SETTINGS = SimpleNamespace(
    image_provider="none",
    gemini_api_key=None,
    ollama_base_url="http://ollama.test:11434",
    ollama_image_model="llama3.2-vision",
    gemini_image_model="gemini-2.5-flash",
    image_understanding_timeout_seconds=30,
    image_understanding_max_bytes=10 * 1024 * 1024,
)


class _FakeGeminiModels:
    def __init__(self, responses, recorder):
        self._responses = responses
        self._recorder = recorder

    def generate_content(self, *, model, contents):
        self._recorder.append((model, contents))
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class _FakeGeminiClient:
    def __init__(self, responses, recorder):
        self.models = _FakeGeminiModels(responses, recorder)


def _gemini_vision_provider(monkeypatch, responses, recorder):
    monkeypatch.setattr(image_understanding, "settings", GEMINI_VISION_SETTINGS)
    monkeypatch.setattr(
        image_understanding.genai,
        "Client",
        lambda **kwargs: _FakeGeminiClient(responses, recorder),
    )
    return GeminiImageUnderstandingProvider()


def _ollama_vision_provider(monkeypatch, handler):
    monkeypatch.setattr(image_understanding, "settings", OLLAMA_VISION_SETTINGS)
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return OllamaImageUnderstandingProvider(client=client)


def _pdf_with_image(
    *page_texts: str,
    image_pages: set[int] | None = None,
    width: float = 595,
    height: float = 842,
) -> bytes:
    pdf = pymupdf.open()
    for page_num, text in enumerate(page_texts, start=1):
        page = pdf.new_page(width=width, height=height)
        if text:
            page.insert_text((36, 36), text)
        if image_pages and page_num in image_pages:
            pixel = pymupdf.Pixmap(
                pymupdf.csRGB,
                pymupdf.IRect(0, 0, 2, 2),
                False,
            )
            page.insert_image(
                pymupdf.Rect(50, 50, 150, 150),
                pixmap=pixel,
            )
    content = pdf.tobytes()
    pdf.close()
    return content


class _MockStorage:
    def __init__(self, content: bytes, provider: str = "local") -> None:
        self._content = content
        self.provider = provider

    def open(self, key: str):
        return io.BytesIO(self._content)


# ── Gemini Provider Unit Tests ─────────────────────────────────────────


def test_gemini_vision_success(monkeypatch) -> None:
    recorder: list[tuple[str, list[object]]] = []
    responses = [SimpleNamespace(text="A bar chart showing revenue growth.")]
    provider = _gemini_vision_provider(monkeypatch, responses, recorder)

    result = provider.describe_visual(
        VALID_PNG_BYTES,
        page_number=1,
        visual_index=0,
        suggested_type=VisualType.CHART,
    )

    assert result is not None
    assert isinstance(result, VisualDescription)
    assert result.visual_type == VisualType.CHART
    assert result.description == "A bar chart showing revenue growth."
    assert len(recorder) == 1
    assert recorder[0][0] == "gemini-2.5-flash"


def test_gemini_vision_rate_limit_is_temporary(monkeypatch) -> None:
    recorder: list[tuple[str, list[object]]] = []
    provider = _gemini_vision_provider(
        monkeypatch,
        [genai_errors.APIError(429, "Too Many Requests")],
        recorder,
    )

    with pytest.raises(TemporaryVisualServiceError, match="rate limit"):
        provider.describe_visual(
            VALID_PNG_BYTES,
            page_number=1,
            visual_index=0,
            suggested_type=VisualType.DIAGRAM,
        )


def test_gemini_vision_server_error_is_temporary(monkeypatch) -> None:
    recorder: list[tuple[str, list[object]]] = []
    provider = _gemini_vision_provider(
        monkeypatch,
        [genai_errors.APIError(503, "Service Unavailable")],
        recorder,
    )

    with pytest.raises(TemporaryVisualServiceError, match="unavailable"):
        provider.describe_visual(
            VALID_PNG_BYTES,
            page_number=1,
            visual_index=0,
            suggested_type=VisualType.DIAGRAM,
        )


def test_gemini_vision_client_error_is_visual_analysis_error(monkeypatch) -> None:
    recorder: list[tuple[str, list[object]]] = []
    provider = _gemini_vision_provider(
        monkeypatch,
        [genai_errors.APIError(400, "Invalid image payload")],
        recorder,
    )

    with pytest.raises(VisualAnalysisError, match="failed"):
        provider.describe_visual(
            VALID_PNG_BYTES,
            page_number=1,
            visual_index=0,
            suggested_type=VisualType.DIAGRAM,
        )


def test_gemini_vision_timeout_is_temporary(monkeypatch) -> None:
    recorder: list[tuple[str, list[object]]] = []
    provider = _gemini_vision_provider(
        monkeypatch,
        [TimeoutError("timed out")],
        recorder,
    )

    with pytest.raises(TemporaryVisualServiceError, match="timed out"):
        provider.describe_visual(
            VALID_PNG_BYTES,
            page_number=1,
            visual_index=0,
            suggested_type=VisualType.DIAGRAM,
        )


def test_gemini_vision_missing_api_key(monkeypatch) -> None:
    monkeypatch.setattr(
        image_understanding,
        "settings",
        SimpleNamespace(**{**GEMINI_VISION_SETTINGS.__dict__, "gemini_api_key": None}),
    )

    with pytest.raises(VisualAnalysisError, match="GEMINI_API_KEY"):
        GeminiImageUnderstandingProvider()


def test_gemini_vision_empty_response_returns_none(monkeypatch) -> None:
    recorder: list[tuple[str, list[object]]] = []
    provider = _gemini_vision_provider(
        monkeypatch,
        [SimpleNamespace(text="")],
        recorder,
    )

    result = provider.describe_visual(
        VALID_PNG_BYTES,
        page_number=1,
        visual_index=0,
        suggested_type=VisualType.FIGURE,
    )
    assert result is None


def test_gemini_vision_rejects_invalid_png_signature(monkeypatch) -> None:
    provider = _gemini_vision_provider(monkeypatch, [], [])

    with pytest.raises(VisualAnalysisError, match="PNG signature"):
        provider.describe_visual(
            b"NOT_A_PNG_IMAGE",
            page_number=1,
            visual_index=0,
            suggested_type=VisualType.FIGURE,
        )


def test_gemini_vision_rejects_oversized_image(monkeypatch) -> None:
    provider = _gemini_vision_provider(monkeypatch, [], [])
    provider._max_bytes = 10

    with pytest.raises(VisualAnalysisError, match="exceeds maximum allowed size"):
        provider.describe_visual(
            VALID_PNG_BYTES,
            page_number=1,
            visual_index=0,
            suggested_type=VisualType.FIGURE,
        )


def test_gemini_vision_strips_null_bytes(monkeypatch) -> None:
    recorder: list[tuple[str, list[object]]] = []
    provider = _gemini_vision_provider(
        monkeypatch,
        [SimpleNamespace(text="Clean\x00 description\x00")],
        recorder,
    )

    result = provider.describe_visual(
        VALID_PNG_BYTES,
        page_number=1,
        visual_index=0,
        suggested_type=VisualType.TABLE,
    )
    assert result is not None
    assert result.description == "Clean description"


# ── Ollama Provider Unit Tests ─────────────────────────────────────────


def test_ollama_vision_success(monkeypatch) -> None:
    captured: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        return httpx.Response(200, json={"response": "A diagram of a neural network."})

    provider = _ollama_vision_provider(monkeypatch, handler)
    result = provider.describe_visual(
        VALID_PNG_BYTES,
        page_number=1,
        visual_index=0,
        suggested_type=VisualType.DIAGRAM,
    )

    assert result is not None
    assert isinstance(result, VisualDescription)
    assert result.visual_type == VisualType.DIAGRAM
    assert result.description == "A diagram of a neural network."
    assert len(captured) == 1
    assert captured[0]["model"] == "llama3.2-vision"
    assert captured[0]["images"] == [base64.b64encode(VALID_PNG_BYTES).decode("utf-8")]
    assert captured[0]["stream"] is False


def test_ollama_vision_timeout_is_temporary(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("too slow", request=request)

    provider = _ollama_vision_provider(monkeypatch, handler)

    with pytest.raises(TemporaryVisualServiceError, match="timed out"):
        provider.describe_visual(
            VALID_PNG_BYTES,
            page_number=1,
            visual_index=0,
            suggested_type=VisualType.DIAGRAM,
        )


def test_ollama_vision_connection_error_is_temporary(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    provider = _ollama_vision_provider(monkeypatch, handler)

    with pytest.raises(TemporaryVisualServiceError, match="could not be reached"):
        provider.describe_visual(
            VALID_PNG_BYTES,
            page_number=1,
            visual_index=0,
            suggested_type=VisualType.DIAGRAM,
        )


def test_ollama_vision_rate_limit_is_temporary(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": "Rate limit exceeded"})

    provider = _ollama_vision_provider(monkeypatch, handler)

    with pytest.raises(TemporaryVisualServiceError, match="rate limit"):
        provider.describe_visual(
            VALID_PNG_BYTES,
            page_number=1,
            visual_index=0,
            suggested_type=VisualType.DIAGRAM,
        )


def test_ollama_vision_server_error_is_temporary(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "Internal Server Error"})

    provider = _ollama_vision_provider(monkeypatch, handler)

    with pytest.raises(TemporaryVisualServiceError, match="HTTP 500"):
        provider.describe_visual(
            VALID_PNG_BYTES,
            page_number=1,
            visual_index=0,
            suggested_type=VisualType.DIAGRAM,
        )


def test_ollama_vision_client_error_is_analysis_error(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "Bad Request"})

    provider = _ollama_vision_provider(monkeypatch, handler)

    with pytest.raises(VisualAnalysisError, match="HTTP 400"):
        provider.describe_visual(
            VALID_PNG_BYTES,
            page_number=1,
            visual_index=0,
            suggested_type=VisualType.DIAGRAM,
        )


def test_ollama_vision_invalid_json(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not json")

    provider = _ollama_vision_provider(monkeypatch, handler)

    with pytest.raises(VisualAnalysisError, match="invalid JSON"):
        provider.describe_visual(
            VALID_PNG_BYTES,
            page_number=1,
            visual_index=0,
            suggested_type=VisualType.DIAGRAM,
        )


def test_ollama_vision_empty_response(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"response": ""})

    provider = _ollama_vision_provider(monkeypatch, handler)

    result = provider.describe_visual(
        VALID_PNG_BYTES,
        page_number=1,
        visual_index=0,
        suggested_type=VisualType.DIAGRAM,
    )
    assert result is None


# ── Disabled Provider and Factory Tests ────────────────────────────────


def test_disabled_provider_returns_none() -> None:
    provider = DisabledImageUnderstandingProvider()
    assert provider.enabled is False
    assert (
        provider.describe_visual(
            VALID_PNG_BYTES,
            page_number=1,
            visual_index=0,
            suggested_type=VisualType.FIGURE,
        )
        is None
    )


def test_factory_returns_disabled_when_none(monkeypatch) -> None:
    monkeypatch.setattr(image_understanding, "settings", DISABLED_VISION_SETTINGS)
    provider = get_image_understanding_provider()
    assert isinstance(provider, DisabledImageUnderstandingProvider)
    assert provider.enabled is False


def test_factory_returns_gemini_provider(monkeypatch) -> None:
    monkeypatch.setattr(image_understanding, "settings", GEMINI_VISION_SETTINGS)
    monkeypatch.setattr(
        image_understanding.genai,
        "Client",
        lambda **kwargs: _FakeGeminiClient([], []),
    )
    provider = get_image_understanding_provider()
    assert isinstance(provider, GeminiImageUnderstandingProvider)
    assert provider.enabled is True


def test_factory_returns_ollama_provider(monkeypatch) -> None:
    monkeypatch.setattr(image_understanding, "settings", OLLAMA_VISION_SETTINGS)
    provider = get_image_understanding_provider()
    assert isinstance(provider, OllamaImageUnderstandingProvider)
    assert provider.enabled is True


def test_factory_rejects_unimplemented_provider(monkeypatch) -> None:
    monkeypatch.setattr(
        image_understanding,
        "settings",
        SimpleNamespace(
            **{**OLLAMA_VISION_SETTINGS.__dict__, "image_provider": "openai"}
        ),
    )
    with pytest.raises(ValueError, match="not implemented"):
        get_image_understanding_provider()


def test_configured_identity_reports_provider_and_model(monkeypatch) -> None:
    monkeypatch.setattr(image_understanding, "settings", DISABLED_VISION_SETTINGS)
    assert configured_image_understanding_identity() == (IMAGE_PROVIDER_NONE, None)

    monkeypatch.setattr(image_understanding, "settings", OLLAMA_VISION_SETTINGS)
    assert configured_image_understanding_identity() == (
        AI_PROVIDER_OLLAMA,
        "llama3.2-vision",
    )

    monkeypatch.setattr(image_understanding, "settings", GEMINI_VISION_SETTINGS)
    assert configured_image_understanding_identity() == (
        AI_PROVIDER_GEMINI,
        "gemini-2.5-flash",
    )


# ── Extraction Wiring & Worker Integration Tests ───────────────────────


def test_extract_document_uses_configured_image_provider(monkeypatch) -> None:
    """Proves extract_document resolves and invokes get_image_understanding_provider."""
    called_factory = False

    class TrackingDisabledProvider:
        enabled = False

        def describe_visual(self, *args, **kwargs):
            return None

    def fake_get_provider():
        nonlocal called_factory
        called_factory = True
        return TrackingDisabledProvider()

    monkeypatch.setattr(
        "services.document_extraction.get_image_understanding_provider",
        fake_get_provider,
    )

    txt_content = b"Simple course text for extraction."
    file_hash = hashlib.sha256(txt_content).hexdigest()
    storage = _MockStorage(txt_content)

    result = extract_document(
        storage,
        storage_provider="local",
        storage_key="test-key",
        expected_hash=file_hash,
        expected_size=len(txt_content),
        file_type="txt",
    )

    assert called_factory is True
    assert len(result.pages) == 1
    assert result.pages[0].text == "Simple course text for extraction."


def test_extract_document_with_enabled_provider_processes_pdf_visuals() -> None:
    """Proves PDF with visuals executes UNDERSTANDING_IMAGES stage and populates descriptions."""
    stages: list[PipelineStage] = []

    class RealStubVisionProvider:
        enabled = True

        def describe_visual(
            self,
            visual_png: bytes,
            *,
            page_number: int,
            visual_index: int,
            suggested_type: VisualType,
        ) -> VisualDescription:
            return VisualDescription(
                visual_type=VisualType.DIAGRAM,
                description="Architecture diagram showing components and data flow.",
            )

    pdf_data = _pdf_with_image("Introduction to system architecture.", image_pages={1})
    file_hash = hashlib.sha256(pdf_data).hexdigest()
    storage = _MockStorage(pdf_data)

    result = extract_document(
        storage,
        storage_provider="local",
        storage_key="test-pdf",
        expected_hash=file_hash,
        expected_size=len(pdf_data),
        file_type="pdf",
        stage_callback=stages.append,
        image_provider=RealStubVisionProvider(),
    )

    assert PipelineStage.UNDERSTANDING_IMAGES in stages
    assert len(result.pages) == 1
    page = result.pages[0]
    assert page.visual_analysis_status == PageVisualAnalysisStatus.COMPLETED.value
    assert len(page.visuals) == 1
    assert page.visuals[0].analysis_status == VisualAnalysisStatus.SUCCEEDED.value
    assert (
        page.visuals[0].description
        == "Architecture diagram showing components and data flow."
    )
    assert "[Diagram]" in page.text
    assert "Architecture diagram showing components and data flow." in page.text
    assert any(
        "Architecture diagram showing components and data flow." in chunk.text
        for chunk in result.chunks
    )


def test_extract_document_with_disabled_provider_marks_not_configured() -> None:
    """Proves PDF with visuals under disabled provider remains explicitly not_configured."""
    stages: list[PipelineStage] = []
    pdf_data = _pdf_with_image(
        "Searchable native course text with more than twenty characters.",
        image_pages={1},
    )
    file_hash = hashlib.sha256(pdf_data).hexdigest()
    storage = _MockStorage(pdf_data)

    result = extract_document(
        storage,
        storage_provider="local",
        storage_key="test-pdf",
        expected_hash=file_hash,
        expected_size=len(pdf_data),
        file_type="pdf",
        stage_callback=stages.append,
        image_provider=DisabledImageUnderstandingProvider(),
    )

    assert PipelineStage.UNDERSTANDING_IMAGES not in stages
    page = result.pages[0]
    assert page.visual_analysis_status == PageVisualAnalysisStatus.NOT_CONFIGURED.value
    assert page.visuals[0].analysis_status == VisualAnalysisStatus.NOT_CONFIGURED.value
    assert page.visuals[0].description is None
    assert (
        page.text == "Searchable native course text with more than twenty characters."
    )


def test_extract_document_temporary_error_is_retryable() -> None:
    """Proves temporary visual provider error halts with retryable DocumentProcessingError."""

    class FailingVisionProvider:
        enabled = True

        def describe_visual(self, *args, **kwargs):
            raise TemporaryVisualServiceError("Rate limit exceeded")

    pdf_data = _pdf_with_image(
        "Searchable native course text with more than twenty characters.",
        image_pages={1},
    )
    file_hash = hashlib.sha256(pdf_data).hexdigest()
    storage = _MockStorage(pdf_data)

    with pytest.raises(DocumentProcessingError) as exc_info:
        extract_document(
            storage,
            storage_provider="local",
            storage_key="test-pdf",
            expected_hash=file_hash,
            expected_size=len(pdf_data),
            file_type="pdf",
            image_provider=FailingVisionProvider(),
        )

    assert exc_info.value.code == "IMAGE_UNDERSTANDING_FAILED"
    assert exc_info.value.retryable is True
    assert exc_info.value.failed_stage == PipelineStage.UNDERSTANDING_IMAGES.value


def test_extract_document_visual_analysis_error_is_nonfatal() -> None:
    """Proves per-visual analysis failure is nonfatal and document extraction succeeds."""

    class NonFatalFailingVisionProvider:
        enabled = True

        def describe_visual(self, *args, **kwargs):
            raise VisualAnalysisError("Corrupt visual region")

    pdf_data = _pdf_with_image(
        "Searchable native course text with more than twenty characters.",
        image_pages={1},
    )
    file_hash = hashlib.sha256(pdf_data).hexdigest()
    storage = _MockStorage(pdf_data)

    result = extract_document(
        storage,
        storage_provider="local",
        storage_key="test-pdf",
        expected_hash=file_hash,
        expected_size=len(pdf_data),
        file_type="pdf",
        image_provider=NonFatalFailingVisionProvider(),
    )

    page = result.pages[0]
    assert page.visual_analysis_status == PageVisualAnalysisStatus.FAILED.value
    assert page.visuals[0].analysis_status == VisualAnalysisStatus.FAILED.value
    assert page.visuals[0].error_code == "VISUAL_ANALYSIS_FAILED"
    assert page.visuals[0].description is None
    assert (
        page.text == "Searchable native course text with more than twenty characters."
    )
    assert (
        result.chunks[0].text
        == "Searchable native course text with more than twenty characters."
    )
