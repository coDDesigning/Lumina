import json
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import select

import routes.study_guide as study_guide_route
import services.study_guide as study_guide_service
import services.text_generation as text_generation
from backend.app.models import Course, DocumentChunk, GeneratedOutput, UploadedDocument
from schemas.ai_usage import ErrorCategory
from schemas.study_guide import SummaryFormat
from services.study_guide import StudyGuideGenerationError, StudyGuideService
from services.text_generation import (
    GenerationMetadata,
    TextGenerationConnectionError,
    TextGenerationError,
    TextGenerationRateLimitError,
    TextGenerationTimeoutError,
)
from utils.ai_errors import PUBLIC_MESSAGES, AiErrorCode

STUDY_GUIDE_REQUEST = {
    "summary_format": "comprehensive",
    "topic_focus": "All Topics",
}

VALID_STUDY_GUIDE = {
    "title": "Example Guide",
    "summary": "Example summary",
    "key_points": [],
    "important_terms": [],
    "common_mistakes": [],
    "exam_tips": {"lecture_based": [], "ai_suggestions": []},
    "difficulty": {"level": "Easy", "reason": "Introductory material"},
    "estimated_study_time": "20 minutes",
    "prerequisites": [],
    "learning_objectives": [],
    "coverage": {"status": "Complete", "estimated_completeness": 100},
    "confidence_notes": "",
}

STUB_METADATA = GenerationMetadata(provider="ollama", model="qwen3:8b", latency_ms=5)


class CountingProvider:
    """Records every call so tests can prove the provider was never reached."""

    def __init__(self, result=None, error=None):
        self._result = result if result is not None else dict(VALID_STUDY_GUIDE)
        self._error = error
        self.calls = 0

    def generate_json_with_metadata(self, prompt: str):
        self.calls += 1
        self.prompt = prompt
        if self._error is not None:
            raise self._error
        return self._result, STUB_METADATA


def _install_provider(monkeypatch, provider: CountingProvider) -> CountingProvider:
    monkeypatch.setattr(
        study_guide_route,
        "get_text_generation_provider",
        lambda: provider,
    )
    return provider


def _add_ready_material(session, course_id: int, texts, *, file_hash: str) -> None:
    course = session.get(Course, course_id)
    assert course is not None
    document = UploadedDocument(
        original_file_name=f"{file_hash[:6]}.txt",
        file_type="txt",
        mime_type="text/plain",
        file_size=10,
        file_hash=file_hash,
        user_id=course.owner_id,
        course=course,
        storage_provider="local:test",
        storage_key=f"{file_hash[:6]}.txt",
        status="ready",
    )
    session.add(document)
    session.flush()
    for index, text in enumerate(texts):
        session.add(
            DocumentChunk(
                document=document,
                course=course,
                chunk_index=index,
                page_number=None,
                text=text,
            )
        )
    session.commit()


def _persisted_outputs(session_factory, course_id: int):
    with session_factory() as session:
        return session.scalars(
            select(GeneratedOutput).where(GeneratedOutput.course_id == course_id)
        ).all()


def test_get_course_material_uses_ready_document_chunks(
    db_session,
    model_graph,
) -> None:
    ready_document = UploadedDocument(
        original_file_name="ready.txt",
        file_type="txt",
        mime_type="text/plain",
        file_size=10,
        file_hash="a" * 64,
        uploader=model_graph.user,
        course=model_graph.course,
        storage_provider="local:test",
        storage_key="ready.txt",
        status="ready",
    )
    uploaded_document = UploadedDocument(
        original_file_name="uploaded.txt",
        file_type="txt",
        mime_type="text/plain",
        file_size=10,
        file_hash="b" * 64,
        uploader=model_graph.user,
        course=model_graph.course,
        storage_provider="local:test",
        storage_key="uploaded.txt",
        status="uploaded",
    )

    db_session.add_all(
        [
            DocumentChunk(
                document=ready_document,
                course=model_graph.course,
                chunk_index=1,
                page_number=None,
                text="Second chunk",
            ),
            DocumentChunk(
                document=ready_document,
                course=model_graph.course,
                chunk_index=0,
                page_number=None,
                text="First chunk",
            ),
            DocumentChunk(
                document=uploaded_document,
                course=model_graph.course,
                chunk_index=0,
                page_number=None,
                text="Should not be included",
            ),
        ]
    )
    db_session.commit()

    material = StudyGuideService.get_course_material(
        db_session,
        model_graph.course.id,
    )

    assert material.text == "First chunk\n\nSecond chunk"
    assert material.chunks_used == 2
    assert material.chunks_available == 2
    assert material.truncated is False


def test_build_prompt_inserts_course_material() -> None:
    prompt = StudyGuideService.build_prompt(
        "Example course material",
        SummaryFormat.COMPREHENSIVE,
        "All Topics",
    )

    assert "{{TEXT}}" not in prompt
    assert "Example course material" in prompt


def test_generate_returns_validated_study_guide(
    db_session,
    model_graph,
) -> None:
    document = UploadedDocument(
        original_file_name="guide.txt",
        file_type="txt",
        mime_type="text/plain",
        file_size=10,
        file_hash="c" * 64,
        uploader=model_graph.user,
        course=model_graph.course,
        storage_provider="local:test",
        storage_key="guide.txt",
        status="ready",
    )
    db_session.add(
        DocumentChunk(
            document=document,
            course=model_graph.course,
            chunk_index=0,
            page_number=None,
            text="Example lecture material",
        )
    )
    db_session.commit()

    class FakeProvider:
        def generate_json(self, prompt: str) -> dict[str, object]:
            assert "Example lecture material" in prompt
            return dict(VALID_STUDY_GUIDE)

    generation = StudyGuideService.generate(
        db_session,
        model_graph.course.id,
        SummaryFormat.COMPREHENSIVE,
        "All Topics",
        FakeProvider(),
    )

    assert generation.study_guide.title == "Example Guide"
    assert generation.study_guide.coverage.estimated_completeness == 100
    assert generation.material.truncated is False
    assert generation.material.chunks_used == 1
    assert generation.model_used.startswith("ollama:")


def test_generate_rejects_missing_ready_course_material(
    db_session,
    model_graph,
) -> None:
    class FakeProvider:
        def generate_json(self, prompt: str) -> dict[str, object]:
            raise AssertionError("Provider should not be called")

    with pytest.raises(StudyGuideGenerationError) as raised:
        StudyGuideService.generate(
            db_session,
            model_graph.course.id,
            SummaryFormat.COMPREHENSIVE,
            "All Topics",
            FakeProvider(),
        )

    assert "No processed course material" in str(raised.value)


def test_generate_wraps_text_generation_error(
    db_session,
    model_graph,
) -> None:
    document = UploadedDocument(
        original_file_name="provider-error.txt",
        file_type="txt",
        mime_type="text/plain",
        file_size=10,
        file_hash="d" * 64,
        uploader=model_graph.user,
        course=model_graph.course,
        storage_provider="local:test",
        storage_key="provider-error.txt",
        status="ready",
    )
    db_session.add(
        DocumentChunk(
            document=document,
            course=model_graph.course,
            chunk_index=0,
            page_number=None,
            text="Example lecture material",
        )
    )
    db_session.commit()

    class FakeProvider:
        def generate_json(self, prompt: str) -> dict[str, object]:
            from services.text_generation import TextGenerationError

            raise TextGenerationError("Provider failed")

    with pytest.raises(StudyGuideGenerationError) as raised:
        StudyGuideService.generate(
            db_session,
            model_graph.course.id,
            SummaryFormat.COMPREHENSIVE,
            "All Topics",
            FakeProvider(),
        )

    assert "Text generation provider failed." in str(raised.value)


def test_generate_rejects_invalid_study_guide_structure(
    db_session,
    model_graph,
) -> None:
    document = UploadedDocument(
        original_file_name="invalid-guide.txt",
        file_type="txt",
        mime_type="text/plain",
        file_size=10,
        file_hash="e" * 64,
        uploader=model_graph.user,
        course=model_graph.course,
        storage_provider="local:test",
        storage_key="invalid-guide.txt",
        status="ready",
    )
    db_session.add(
        DocumentChunk(
            document=document,
            course=model_graph.course,
            chunk_index=0,
            page_number=None,
            text="Example lecture material",
        )
    )
    db_session.commit()

    class FakeProvider:
        def generate_json(self, prompt: str) -> dict[str, object]:
            return {"title": "Incomplete guide"}

    with pytest.raises(StudyGuideGenerationError) as raised:
        StudyGuideService.generate(
            db_session,
            model_graph.course.id,
            SummaryFormat.COMPREHENSIVE,
            "All Topics",
            FakeProvider(),
        )

    assert "invalid structure" in str(raised.value)


def test_save_generated_output_persists_study_guide(
    db_session,
    model_graph,
) -> None:
    class FakeProvider:
        def generate_json(self, prompt: str) -> dict[str, object]:
            return dict(VALID_STUDY_GUIDE) | {"title": "Saved Guide"}

    document = UploadedDocument(
        original_file_name="persist.txt",
        file_type="txt",
        mime_type="text/plain",
        file_size=10,
        file_hash="f" * 64,
        uploader=model_graph.user,
        course=model_graph.course,
        storage_provider="local:test",
        storage_key="persist.txt",
        status="ready",
    )
    db_session.add(
        DocumentChunk(
            document=document,
            course=model_graph.course,
            chunk_index=0,
            page_number=None,
            text="Persisted lecture material",
        )
    )
    db_session.commit()

    generation = StudyGuideService.generate(
        db_session,
        model_graph.course.id,
        SummaryFormat.COMPREHENSIVE,
        "All Topics",
        FakeProvider(),
    )

    generated_output = StudyGuideService.save_generated_output(
        db_session,
        model_graph.course.id,
        generation.study_guide,
        user_id=model_graph.user.id,
        model_used=generation.model_used,
    )

    assert generated_output.id is not None
    assert generated_output.output_type == "study_guide"
    assert '"title":"Saved Guide"' in generated_output.content
    assert generated_output.user_id == model_graph.user.id
    assert generated_output.model_used == generation.model_used


def test_generate_bounds_the_prompt_to_the_configured_budget(
    db_session,
    model_graph,
    monkeypatch,
) -> None:
    document = UploadedDocument(
        original_file_name="bounded.txt",
        file_type="txt",
        mime_type="text/plain",
        file_size=10,
        file_hash="7" * 64,
        uploader=model_graph.user,
        course=model_graph.course,
        storage_provider="local:test",
        storage_key="bounded.txt",
        status="ready",
    )
    for index in range(5):
        db_session.add(
            DocumentChunk(
                document=document,
                course=model_graph.course,
                chunk_index=index,
                page_number=None,
                text=f"chunk-{index} " + "x" * 40,
            )
        )
    db_session.commit()

    monkeypatch.setattr(
        study_guide_service,
        "settings",
        SimpleNamespace(study_guide_material_max_chars=100),
    )

    captured: list[str] = []

    class FakeProvider:
        def generate_json(self, prompt: str) -> dict[str, object]:
            captured.append(prompt)
            return dict(VALID_STUDY_GUIDE)

    generation = StudyGuideService.generate(
        db_session,
        model_graph.course.id,
        SummaryFormat.COMPREHENSIVE,
        "All Topics",
        FakeProvider(),
    )

    assert generation.material.truncated is True
    assert generation.material.chunks_used == 2
    assert generation.material.chunks_available == 5
    assert len(generation.material.text) <= 100
    assert "chunk-0" in captured[0]
    assert "chunk-4" not in captured[0]


def test_study_guide_endpoint_persists_attribution_and_reports_context(
    upload_api,
    monkeypatch,
) -> None:
    with upload_api.session_factory() as session:
        _add_ready_material(
            session,
            upload_api.course_id,
            ["API study guide lecture material"],
            file_hash="1" * 64,
        )

    provider = _install_provider(monkeypatch, CountingProvider())

    response = upload_api.client.post(
        f"/api/courses/{upload_api.course_id}/study-guide",
        json=STUDY_GUIDE_REQUEST,
        headers=upload_api.authorization,
    )

    assert response.status_code == 200, response.text
    payload = response.json()

    assert payload["success"] is True
    assert payload["data"]["study_guide"]["title"] == "Example Guide"
    assert payload["data"]["context_truncated"] is False
    assert payload["data"]["chunks_used"] == 1
    assert payload["data"]["chunks_available"] == 1
    assert provider.calls == 1

    persisted = _persisted_outputs(upload_api.session_factory, upload_api.course_id)
    assert len(persisted) == 1
    assert persisted[0].user_id == upload_api.user_id
    assert persisted[0].model_used == "ollama:qwen3:8b"


def test_study_guide_endpoint_passes_the_requested_format_and_topic_focus(
    upload_api,
    monkeypatch,
) -> None:
    with upload_api.session_factory() as session:
        _add_ready_material(
            session,
            upload_api.course_id,
            ["Parameterized lecture material"],
            file_hash="7" * 64,
        )

    provider = _install_provider(monkeypatch, CountingProvider())

    response = upload_api.client.post(
        f"/api/courses/{upload_api.course_id}/study-guide",
        json={"summary_format": "exam_tips", "topic_focus": "Working Memory"},
        headers=upload_api.authorization,
    )

    assert response.status_code == 200, response.text
    assert provider.calls == 1
    assert "{{SUMMARY_FORMAT}}" not in provider.prompt
    assert "{{TOPIC_FOCUS}}" not in provider.prompt
    assert "Requested summary format: exam_tips." in provider.prompt
    assert "Working Memory" in provider.prompt
    assert (
        study_guide_service.SUMMARY_FORMAT_DIRECTIVES[SummaryFormat.EXAM_TIPS]
        in provider.prompt
    )


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(None, id="missing_body"),
        pytest.param({"topic_focus": "All Topics"}, id="missing_summary_format"),
        pytest.param(
            {"summary_format": "comprehensive"},
            id="missing_topic_focus",
        ),
        pytest.param(
            {"summary_format": "not_a_format", "topic_focus": "All Topics"},
            id="unknown_summary_format",
        ),
        pytest.param(
            {"summary_format": "comprehensive", "topic_focus": ""},
            id="empty_topic_focus",
        ),
        pytest.param(
            {"summary_format": "comprehensive", "topic_focus": "x" * 201},
            id="overlong_topic_focus",
        ),
    ],
)
def test_study_guide_endpoint_rejects_an_invalid_request(
    upload_api,
    monkeypatch,
    body,
) -> None:
    with upload_api.session_factory() as session:
        _add_ready_material(
            session,
            upload_api.course_id,
            ["Validation lecture material"],
            file_hash="8" * 64,
        )

    provider = _install_provider(monkeypatch, CountingProvider())

    response = upload_api.client.post(
        f"/api/courses/{upload_api.course_id}/study-guide",
        json=body,
        headers=upload_api.authorization,
    )

    assert response.status_code == 422, response.text
    assert provider.calls == 0
    assert _persisted_outputs(upload_api.session_factory, upload_api.course_id) == []


def test_study_guide_endpoint_accepts_the_longest_allowed_topic_focus(
    upload_api,
    monkeypatch,
) -> None:
    with upload_api.session_factory() as session:
        _add_ready_material(
            session,
            upload_api.course_id,
            ["Boundary lecture material"],
            file_hash="9" * 64,
        )

    provider = _install_provider(monkeypatch, CountingProvider())

    response = upload_api.client.post(
        f"/api/courses/{upload_api.course_id}/study-guide",
        json={"summary_format": "overview", "topic_focus": "z" * 200},
        headers=upload_api.authorization,
    )

    assert response.status_code == 200, response.text
    assert provider.calls == 1


def test_study_guide_endpoint_reports_truncated_context(
    upload_api,
    monkeypatch,
) -> None:
    with upload_api.session_factory() as session:
        _add_ready_material(
            session,
            upload_api.course_id,
            [f"chunk-{index} " + "y" * 40 for index in range(5)],
            file_hash="2" * 64,
        )

    monkeypatch.setattr(
        study_guide_service,
        "settings",
        SimpleNamespace(study_guide_material_max_chars=100),
    )
    provider = _install_provider(monkeypatch, CountingProvider())

    response = upload_api.client.post(
        f"/api/courses/{upload_api.course_id}/study-guide",
        json=STUDY_GUIDE_REQUEST,
        headers=upload_api.authorization,
    )

    assert response.status_code == 200, response.text
    payload = response.json()

    assert payload["data"]["context_truncated"] is True
    assert payload["data"]["chunks_used"] == 2
    assert payload["data"]["chunks_available"] == 5
    assert provider.calls == 1


def test_study_guide_endpoint_requires_authentication(
    api_context,
    monkeypatch,
) -> None:
    provider = _install_provider(monkeypatch, CountingProvider())

    response = api_context.client.post(
        "/api/courses/1/study-guide",
        json=STUDY_GUIDE_REQUEST,
    )

    assert response.status_code == 401
    assert provider.calls == 0


def test_study_guide_endpoint_hides_a_missing_course(
    upload_api,
    monkeypatch,
) -> None:
    provider = _install_provider(monkeypatch, CountingProvider())

    response = upload_api.client.post(
        "/api/courses/999999/study-guide",
        json=STUDY_GUIDE_REQUEST,
        headers=upload_api.authorization,
    )

    assert response.status_code == 404
    assert provider.calls == 0


def test_study_guide_endpoint_hides_a_soft_deleted_course(
    upload_api,
    monkeypatch,
) -> None:
    provider = _install_provider(monkeypatch, CountingProvider())

    response = upload_api.client.post(
        f"/api/courses/{upload_api.deleted_course_id}/study-guide",
        json=STUDY_GUIDE_REQUEST,
        headers=upload_api.authorization,
    )

    assert response.status_code == 404
    assert provider.calls == 0


def test_study_guide_endpoint_hides_another_owners_course(
    authz_api,
    monkeypatch,
) -> None:
    with authz_api.session_factory() as session:
        _add_ready_material(
            session,
            authz_api.a_course_id,
            ["Owner A private lecture material"],
            file_hash="3" * 64,
        )

    provider = _install_provider(monkeypatch, CountingProvider())

    response = authz_api.client.post(
        f"/api/courses/{authz_api.a_course_id}/study-guide",
        json=STUDY_GUIDE_REQUEST,
        headers=authz_api.authorization_b,
    )

    assert response.status_code == 404
    assert provider.calls == 0
    assert _persisted_outputs(authz_api.session_factory, authz_api.a_course_id) == []


def test_study_guide_endpoint_rejects_a_course_without_ready_material(
    upload_api,
    monkeypatch,
) -> None:
    provider = _install_provider(monkeypatch, CountingProvider())

    response = upload_api.client.post(
        f"/api/courses/{upload_api.course_id}/study-guide",
        json=STUDY_GUIDE_REQUEST,
        headers=upload_api.authorization,
    )

    assert response.status_code == 400
    assert response.json()["detail"] == PUBLIC_MESSAGES[AiErrorCode.NO_READY_MATERIAL]
    assert provider.calls == 0
    assert _persisted_outputs(upload_api.session_factory, upload_api.course_id) == []


@pytest.mark.parametrize(
    ("error", "expected_status", "expected_code"),
    [
        (
            TextGenerationConnectionError(
                "Ollama could not be reached at http://ollama.internal:11434."
            ),
            503,
            AiErrorCode.PROVIDER_UNAVAILABLE,
        ),
        (
            TextGenerationTimeoutError("Ollama did not respond in 42 seconds."),
            504,
            AiErrorCode.PROVIDER_TIMEOUT,
        ),
        (
            TextGenerationRateLimitError("Ollama rate limit exceeded."),
            429,
            AiErrorCode.PROVIDER_RATE_LIMITED,
        ),
        (
            TextGenerationError(
                "Ollama returned invalid JSON.",
                error_category=ErrorCategory.INVALID_STRUCTURE,
            ),
            500,
            AiErrorCode.INVALID_GENERATED_STRUCTURE,
        ),
    ],
)
def test_study_guide_endpoint_curates_provider_failures(
    upload_api,
    monkeypatch,
    error,
    expected_status,
    expected_code,
) -> None:
    with upload_api.session_factory() as session:
        _add_ready_material(
            session,
            upload_api.course_id,
            ["Curated failure lecture material"],
            file_hash="4" * 64,
        )

    provider = _install_provider(monkeypatch, CountingProvider(error=error))

    response = upload_api.client.post(
        f"/api/courses/{upload_api.course_id}/study-guide",
        json=STUDY_GUIDE_REQUEST,
        headers=upload_api.authorization,
    )

    detail = response.json()["detail"]

    assert response.status_code == expected_status
    assert detail == PUBLIC_MESSAGES[expected_code]
    assert "ollama.internal" not in detail
    assert "11434" not in detail
    assert provider.calls == 1
    assert _persisted_outputs(upload_api.session_factory, upload_api.course_id) == []


def test_study_guide_endpoint_curates_invalid_generated_structure(
    upload_api,
    monkeypatch,
) -> None:
    with upload_api.session_factory() as session:
        _add_ready_material(
            session,
            upload_api.course_id,
            ["Invalid structure lecture material"],
            file_hash="5" * 64,
        )

    provider = _install_provider(
        monkeypatch,
        CountingProvider(result={"title": "Incomplete guide"}),
    )

    response = upload_api.client.post(
        f"/api/courses/{upload_api.course_id}/study-guide",
        json=STUDY_GUIDE_REQUEST,
        headers=upload_api.authorization,
    )

    assert response.status_code == 500
    assert (
        response.json()["detail"]
        == PUBLIC_MESSAGES[AiErrorCode.INVALID_GENERATED_STRUCTURE]
    )
    assert provider.calls == 1
    assert _persisted_outputs(upload_api.session_factory, upload_api.course_id) == []


def _ollama_provider_returning(monkeypatch, generated: str):
    monkeypatch.setattr(
        text_generation,
        "settings",
        SimpleNamespace(
            ai_provider="ollama",
            ai_fallback_providers="",
            gemini_api_key=None,
            ollama_base_url="http://ollama.test:11434",
            ollama_model="qwen3:8b",
            ai_generation_timeout_seconds=42,
        ),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"model": "qwen3:8b", "response": generated, "done": True},
        )

    return text_generation.OllamaTextGenerationProvider(
        client=httpx.Client(transport=httpx.MockTransport(handler))
    )


def _ready_course_material(db_session, model_graph, file_hash: str) -> None:
    document = UploadedDocument(
        original_file_name="ollama.txt",
        file_type="txt",
        mime_type="text/plain",
        file_size=10,
        file_hash=file_hash,
        uploader=model_graph.user,
        course=model_graph.course,
        storage_provider="local:test",
        storage_key="ollama.txt",
        status="ready",
    )
    db_session.add(
        DocumentChunk(
            document=document,
            course=model_graph.course,
            chunk_index=0,
            page_number=None,
            text="Ollama lecture material",
        )
    )
    db_session.commit()


@pytest.mark.parametrize(
    "generated",
    [
        "Sure! Here's your study guide: 1. Binary Trees 2. Graphs",
        '{"title": "Truncated",',
        '{"random": "value"}',
    ],
)
def test_ollama_output_that_is_not_a_valid_study_guide_is_never_persisted(
    db_session,
    model_graph,
    monkeypatch,
    generated: str,
) -> None:
    _ready_course_material(db_session, model_graph, "1" * 64)
    provider = _ollama_provider_returning(monkeypatch, generated)

    with pytest.raises(StudyGuideGenerationError):
        StudyGuideService.generate(
            db_session,
            model_graph.course.id,
            SummaryFormat.COMPREHENSIVE,
            "All Topics",
            provider,
        )

    db_session.rollback()
    persisted = db_session.scalars(
        select(GeneratedOutput).where(
            GeneratedOutput.course_id == model_graph.course.id
        )
    ).all()

    assert persisted == []


def test_ollama_output_that_is_a_valid_study_guide_persists(
    db_session,
    model_graph,
    monkeypatch,
) -> None:
    _ready_course_material(db_session, model_graph, "2" * 64)
    valid_guide = dict(VALID_STUDY_GUIDE) | {"title": "Ollama Guide"}
    provider = _ollama_provider_returning(
        monkeypatch,
        f"```json\n{json.dumps(valid_guide)}\n```",
    )

    generation = StudyGuideService.generate(
        db_session,
        model_graph.course.id,
        SummaryFormat.COMPREHENSIVE,
        "All Topics",
        provider,
    )
    generated_output = StudyGuideService.save_generated_output(
        db_session,
        model_graph.course.id,
        generation.study_guide,
        user_id=model_graph.user.id,
        model_used=generation.model_used,
    )

    assert generation.study_guide.title == "Ollama Guide"
    assert generation.model_used == "ollama:qwen3:8b"
    assert generated_output.id is not None
    assert generated_output.user_id == model_graph.user.id
    assert generated_output.model_used == "ollama:qwen3:8b"
