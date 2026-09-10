# tests/test_image_understanding.py
"""Tests for image understanding providers, configuration, and extraction wiring."""

import base64
import hashlib
import io
import json
import logging
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pymupdf
import pytest
from google.genai import errors as genai_errors
from sqlalchemy import select

from backend.app.config import (
    AI_PROVIDER_GEMINI,
    AI_PROVIDER_OLLAMA,
    IMAGE_PROVIDER_NONE,
)
from backend.app.models import AiUsageLog
import services.image_understanding as image_understanding
from schemas.ai_usage import GenerationType
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
from schemas.prompt_context import EducationLevel, MaterialKind, PromptContext
from schemas.prompt_template import PromptTemplateNotFoundError
from services.image_understanding import (
    GeminiImageUnderstandingProvider,
    ImageUnderstandingUsage,
    OllamaImageUnderstandingProvider,
    _render_image_description_prompt,
    configured_image_understanding_identity,
    get_image_understanding_provider,
)
from services.processing_jobs import ClaimedJob
from workers.document_processor import _record_image_usage

# Valid 1x1 minimal PNG image bytes
VALID_PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06"
    b"\x00\x00\x00\x1f\x15c4\x00\x00\x00\rIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02"
    b"\xfe\xdc\xccY\xe7\x00\x00\x00\x00IEND\xaeB`\x82"
)

OLLAMA_VISION_SETTINGS = SimpleNamespace(
    ai_vision_model="ollama:llama3.2-vision",
    gemini_api_key=None,
    ollama_base_url="http://ollama.test:11434",
    image_understanding_timeout_seconds=30,
    image_understanding_max_bytes=10 * 1024 * 1024,
    ollama_temperature=0.2,
    ollama_top_p=0.9,
    ollama_num_ctx=8192,
    ollama_num_predict=4096,
    ollama_repeat_penalty=1.1,
)

GEMINI_VISION_SETTINGS = SimpleNamespace(
    ai_vision_model="gemini:gemini-2.5-flash",
    gemini_api_key="test-key",
    ollama_base_url="http://ollama.test:11434",
    image_understanding_timeout_seconds=30,
    image_understanding_max_bytes=10 * 1024 * 1024,
)

DISABLED_VISION_SETTINGS = SimpleNamespace(
    ai_vision_model=None,
    gemini_api_key=None,
    ollama_base_url="http://ollama.test:11434",
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


def _gemini_vision_provider(monkeypatch, responses, recorder, *, usage_callback=None):
    monkeypatch.setattr(image_understanding, "settings", GEMINI_VISION_SETTINGS)
    monkeypatch.setattr(
        image_understanding.genai,
        "Client",
        lambda **kwargs: _FakeGeminiClient(responses, recorder),
    )
    return GeminiImageUnderstandingProvider(usage_callback=usage_callback)


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


def test_gemini_vision_reports_each_provider_call_without_content(monkeypatch) -> None:
    events: list[ImageUnderstandingUsage] = []
    response = SimpleNamespace(
        text="A bar chart.",
        usage_metadata=SimpleNamespace(
            prompt_token_count=12,
            candidates_token_count=4,
            total_token_count=16,
        ),
    )
    provider = _gemini_vision_provider(
        monkeypatch, [response], [], usage_callback=events.append
    )

    provider.describe_visual(
        VALID_PNG_BYTES,
        page_number=1,
        visual_index=0,
        suggested_type=VisualType.CHART,
    )

    assert len(events) == 1
    assert events[0].provider == "gemini"
    assert events[0].model == "gemini-2.5-flash"
    assert events[0].prompt_tokens == 12
    assert events[0].completion_tokens == 4
    assert events[0].total_tokens == 16
    assert events[0].success is True


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
    prompt = captured[0]["prompt"]
    assert "{{" not in prompt
    assert "diagram" in prompt
    assert "unspecified" in prompt
    assert "Computer Science" not in prompt
    assert "university" not in prompt


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
            **{
                **OLLAMA_VISION_SETTINGS.__dict__,
                "ai_vision_model": "openai:gpt-5.6-terra",
            }
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


def test_worker_attributes_image_usage_to_the_document_owner(authz_api) -> None:
    job = ClaimedJob(
        id=123,
        document_id=uuid4(),
        course_id=authz_api.a_course_id,
        claim_token="claim",
        attempt_count=1,
        max_attempts=3,
        storage_provider="local",
        storage_key="course/document.txt",
        file_hash="a" * 64,
        file_type="txt",
        file_size=10,
        user_id=authz_api.user_a_id,
    )
    usage = ImageUnderstandingUsage(
        provider="gemini",
        model="gemini-2.5-flash",
        success=True,
        error_category=None,
        prompt_tokens=12,
        completion_tokens=4,
        total_tokens=16,
        latency_ms=25,
    )

    _record_image_usage(authz_api.session_factory, job, usage)

    with authz_api.session_factory() as session:
        row = session.scalars(select(AiUsageLog)).one()
    assert row.user_id == authz_api.user_a_id
    assert row.course_id == authz_api.a_course_id
    assert row.generation_type == GenerationType.IMAGE_UNDERSTANDING.value
    assert row.provider == "gemini"
    assert row.model == "gemini-2.5-flash"
    assert row.prompt_tokens == 12
    assert row.completion_tokens == 4
    assert row.total_tokens == 16
    assert row.success is True


# ── Extraction Wiring & Worker Integration Tests ───────────────────────


def test_extract_document_uses_configured_image_provider(monkeypatch) -> None:
    """Proves extract_document resolves and invokes get_image_understanding_provider."""
    called_factory = False
    factory_kwargs: dict = {}

    class TrackingDisabledProvider:
        enabled = False

        def describe_visual(self, *args, **kwargs):
            return None

    def fake_get_provider(**kwargs):
        nonlocal called_factory
        called_factory = True
        factory_kwargs.update(kwargs)
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
    assert factory_kwargs == {"prompt_context": None, "usage_callback": None}
    assert len(result.pages) == 1
    assert result.pages[0].text == "Simple course text for extraction."


def test_extract_document_binds_the_prompt_context_to_the_provider() -> None:
    factory_kwargs: dict = {}

    class TrackingDisabledProvider:
        enabled = False

        def describe_visual(self, *args, **kwargs):
            return None

    def fake_get_provider(**kwargs):
        factory_kwargs.update(kwargs)
        return TrackingDisabledProvider()

    context = PromptContext(
        education_level=EducationLevel.HIGH_SCHOOL,
        course_title="AP Biology",
        subject_area="Biology",
        material_kind=MaterialKind.TEXTBOOK,
    )

    txt_content = b"Simple course text for extraction."
    file_hash = hashlib.sha256(txt_content).hexdigest()

    with pytest.MonkeyPatch.context() as patcher:
        patcher.setattr(
            "services.document_extraction.get_image_understanding_provider",
            fake_get_provider,
        )
        extract_document(
            _MockStorage(txt_content),
            storage_provider="local",
            storage_key="test-key",
            expected_hash=file_hash,
            expected_size=len(txt_content),
            file_type="txt",
            prompt_context=context,
        )

    assert factory_kwargs["prompt_context"] is context


def test_a_prompt_context_survives_spawn_pickling() -> None:
    import pickle

    context = PromptContext(
        education_level=EducationLevel.GRADUATE,
        course_title="Advanced Macroeconomics",
        subject_area="Economics",
        material_kind=MaterialKind.SLIDES,
    )

    assert pickle.loads(pickle.dumps(context)) == context


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


def test_vision_prompt_carries_the_resolved_learner_context(monkeypatch) -> None:
    captured: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        return httpx.Response(200, json={"response": "A labelled cell diagram."})

    provider = _ollama_vision_provider(monkeypatch, handler)
    provider._prompt_context = PromptContext(
        education_level=EducationLevel.HIGH_SCHOOL,
        course_title="AP Biology",
        subject_area="Biology",
        material_kind=MaterialKind.TEXTBOOK,
    )

    provider.describe_visual(
        VALID_PNG_BYTES,
        page_number=1,
        visual_index=0,
        suggested_type=VisualType.DIAGRAM,
    )

    prompt = captured[0]["prompt"]
    assert "high_school" in prompt
    assert "AP Biology" in prompt
    assert "Biology" in prompt
    assert "textbook" in prompt
    assert "{{" not in prompt


def test_gemini_and_ollama_send_an_identical_vision_prompt() -> None:
    context = PromptContext(
        education_level=EducationLevel.GRADUATE,
        course_title="Advanced Macroeconomics",
        subject_area="Economics",
        material_kind=MaterialKind.SLIDES,
    )

    rendered = _render_image_description_prompt(context, VisualType.CHART)

    assert "graduate" in rendered
    assert "Advanced Macroeconomics" in rendered
    assert "chart" in rendered
    assert "{{" not in rendered


def test_vision_prompt_failure_is_a_per_visual_error(monkeypatch) -> None:
    def explode(*args, **kwargs):
        raise PromptTemplateNotFoundError("template missing")

    monkeypatch.setattr(
        "services.image_understanding.PromptLoader.render",
        explode,
    )

    with pytest.raises(VisualAnalysisError):
        _render_image_description_prompt(PromptContext(), VisualType.DIAGRAM)


def test_an_unusable_ollama_response_is_logged_with_diagnostics(
    monkeypatch, caplog
) -> None:
    """SCRUM-206: this provider has no session and no user, so the log is the trace."""
    canary = "CONFIDENTIAL_SLIDE_TEXT_445566"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=f"Sorry, I cannot read that. {canary}")

    provider = _ollama_vision_provider(monkeypatch, handler)

    with caplog.at_level(logging.WARNING, logger="services.image_understanding"):
        with pytest.raises(VisualAnalysisError, match="invalid JSON"):
            provider.describe_visual(
                VALID_PNG_BYTES,
                page_number=1,
                visual_index=0,
                suggested_type=VisualType.DIAGRAM,
            )

    failures = [
        record
        for record in caplog.records
        if getattr(record, "event", None) == "ai_generation_failed"
    ]
    assert len(failures) == 1
    assert failures[0].generation_type == "image_understanding"
    assert failures[0].error_category == "invalid_structure"
    assert failures[0].ai_response_type == "str"
    assert failures[0].ai_response_bytes > 0
    assert canary not in json.dumps(
        {key: str(value) for key, value in vars(failures[0]).items()}, default=str
    )


def test_an_unexpected_ollama_envelope_shape_is_logged(monkeypatch, caplog) -> None:
    """SCRUM-206: valid JSON of the wrong shape has no keys to report, only a type."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=["not", "an", "object"])

    provider = _ollama_vision_provider(monkeypatch, handler)

    with caplog.at_level(logging.WARNING, logger="services.image_understanding"):
        with pytest.raises(VisualAnalysisError, match="unexpected response structure"):
            provider.describe_visual(
                VALID_PNG_BYTES,
                page_number=1,
                visual_index=0,
                suggested_type=VisualType.DIAGRAM,
            )

    failures = [
        record
        for record in caplog.records
        if getattr(record, "event", None) == "ai_generation_failed"
    ]
    assert len(failures) == 1
    assert failures[0].ai_response_type == "str"


def _ollama_show_client(monkeypatch, handler):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(image_understanding, "_get_shared_http_client", lambda: client)
    monkeypatch.setattr(image_understanding, "_VISION_CAPABILITY", {})
    return client


def _capabilities_handler(capabilities, recorder=None):
    def handler(request: httpx.Request) -> httpx.Response:
        if recorder is not None:
            recorder.append(str(request.url))
        return httpx.Response(200, json={"capabilities": capabilities})

    return handler


def test_a_multimodal_ollama_model_is_used_for_visual_analysis(monkeypatch) -> None:
    monkeypatch.setattr(image_understanding, "settings", OLLAMA_VISION_SETTINGS)
    _ollama_show_client(monkeypatch, _capabilities_handler(["completion", "vision"]))

    provider = get_image_understanding_provider()

    assert isinstance(provider, OllamaImageUnderstandingProvider)
    assert provider.enabled is True


def test_a_text_only_ollama_model_disables_visual_analysis(monkeypatch, caplog) -> None:
    monkeypatch.setattr(image_understanding, "settings", OLLAMA_VISION_SETTINGS)
    _ollama_show_client(monkeypatch, _capabilities_handler(["completion", "tools"]))
    caplog.set_level(logging.WARNING)

    provider = get_image_understanding_provider()

    assert isinstance(provider, DisabledImageUnderstandingProvider)
    assert provider.enabled is False
    record = next(
        entry
        for entry in caplog.records
        if getattr(entry, "event", None) == "image_understanding_disabled"
    )
    assert record.model == "llama3.2-vision"
    assert record.provider == AI_PROVIDER_OLLAMA
    assert "does not support" in record.getMessage()


def test_a_text_only_ollama_model_reports_no_configured_identity(monkeypatch) -> None:
    monkeypatch.setattr(image_understanding, "settings", OLLAMA_VISION_SETTINGS)
    _ollama_show_client(monkeypatch, _capabilities_handler(["completion"]))

    assert configured_image_understanding_identity() == (IMAGE_PROVIDER_NONE, None)


def test_an_unreachable_ollama_does_not_disable_visual_analysis(monkeypatch) -> None:
    monkeypatch.setattr(image_understanding, "settings", OLLAMA_VISION_SETTINGS)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("ollama is down", request=request)

    _ollama_show_client(monkeypatch, handler)

    provider = get_image_understanding_provider()

    assert isinstance(provider, OllamaImageUnderstandingProvider)


def test_the_model_capability_is_asked_once(monkeypatch) -> None:
    monkeypatch.setattr(image_understanding, "settings", OLLAMA_VISION_SETTINGS)
    requests: list[str] = []
    _ollama_show_client(
        monkeypatch, _capabilities_handler(["completion", "vision"], requests)
    )

    get_image_understanding_provider()
    get_image_understanding_provider()
    configured_image_understanding_identity()

    assert len(requests) == 1
    assert requests[0].endswith("/api/show")


def test_the_gemini_model_is_never_asked_about_capabilities(monkeypatch) -> None:
    monkeypatch.setattr(image_understanding, "settings", GEMINI_VISION_SETTINGS)
    monkeypatch.setattr(
        image_understanding.genai,
        "Client",
        lambda **kwargs: _FakeGeminiClient([], []),
    )
    requests: list[str] = []
    _ollama_show_client(
        monkeypatch, _capabilities_handler(["completion", "vision"], requests)
    )

    provider = get_image_understanding_provider()

    assert isinstance(provider, GeminiImageUnderstandingProvider)
    assert requests == []


def test_a_visual_request_bounds_the_context_window(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content.decode("utf-8")))
        return httpx.Response(200, json={"response": "A described diagram."})

    provider = _ollama_vision_provider(monkeypatch, handler)
    provider.describe_visual(
        VALID_PNG_BYTES,
        page_number=1,
        visual_index=0,
        suggested_type=VisualType.DIAGRAM,
    )

    options = captured["options"]
    assert options["num_ctx"] == OLLAMA_VISION_SETTINGS.ollama_num_ctx
    assert options["temperature"] == OLLAMA_VISION_SETTINGS.ollama_temperature
    assert options["top_p"] == OLLAMA_VISION_SETTINGS.ollama_top_p
    assert options["num_predict"] == OLLAMA_VISION_SETTINGS.ollama_num_predict
    assert options["repeat_penalty"] == OLLAMA_VISION_SETTINGS.ollama_repeat_penalty
