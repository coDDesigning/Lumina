import json
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import select

import services.text_generation as text_generation
from backend.app.models import DocumentChunk, GeneratedOutput, UploadedDocument
from services.study_guide import StudyGuideGenerationError, StudyGuideService


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

    assert material == "First chunk\n\nSecond chunk"


def test_build_prompt_inserts_course_material() -> None:
    prompt = StudyGuideService.build_prompt("Example course material")

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
            return {
                "title": "Example Guide",
                "summary": "Example summary",
                "key_points": [],
                "important_terms": [],
                "common_mistakes": [],
                "exam_tips": {
                    "lecture_based": [],
                    "ai_suggestions": [],
                },
                "difficulty": {
                    "level": "Easy",
                    "reason": "Introductory material",
                },
                "estimated_study_time": "20 minutes",
                "prerequisites": [],
                "learning_objectives": [],
                "coverage": {
                    "status": "Complete",
                    "estimated_completeness": 100,
                },
                "confidence_notes": "",
            }

    result = StudyGuideService.generate(
        db_session,
        model_graph.course.id,
        FakeProvider(),
    )

    assert result.title == "Example Guide"
    assert result.coverage.estimated_completeness == 100


def test_generate_rejects_missing_ready_course_material(
    db_session,
    model_graph,
) -> None:
    class FakeProvider:
        def generate_json(self, prompt: str) -> dict[str, object]:
            raise AssertionError("Provider should not be called")

    try:
        StudyGuideService.generate(
            db_session,
            model_graph.course.id,
            FakeProvider(),
        )
    except Exception as exc:
        assert "No ready course material is available." in str(exc)
    else:
        raise AssertionError("Expected study guide generation to fail")


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

    try:
        StudyGuideService.generate(
            db_session,
            model_graph.course.id,
            FakeProvider(),
        )
    except Exception as exc:
        assert "Text generation provider failed." in str(exc)
    else:
        raise AssertionError("Expected study guide generation to fail")


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

    try:
        StudyGuideService.generate(
            db_session,
            model_graph.course.id,
            FakeProvider(),
        )
    except Exception as exc:
        assert "invalid structure" in str(exc)
    else:
        raise AssertionError("Expected study guide generation to fail")


def test_save_generated_output_persists_study_guide(
    db_session,
    model_graph,
) -> None:
    class FakeProvider:
        def generate_json(self, prompt: str) -> dict[str, object]:
            return {
                "title": "Saved Guide",
                "summary": "Saved summary",
                "key_points": [],
                "important_terms": [],
                "common_mistakes": [],
                "exam_tips": {
                    "lecture_based": [],
                    "ai_suggestions": [],
                },
                "difficulty": {
                    "level": "Easy",
                    "reason": "Simple material",
                },
                "estimated_study_time": "15 minutes",
                "prerequisites": [],
                "learning_objectives": [],
                "coverage": {
                    "status": "Complete",
                    "estimated_completeness": 100,
                },
                "confidence_notes": "",
            }

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

    study_guide = StudyGuideService.generate(
        db_session,
        model_graph.course.id,
        FakeProvider(),
    )

    generated_output = StudyGuideService.save_generated_output(
        db_session,
        model_graph.course.id,
        study_guide,
    )

    assert generated_output.id is not None
    assert generated_output.output_type == "study_guide"
    assert '"title":"Saved Guide"' in generated_output.content


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
    valid_guide = {
        "title": "Ollama Guide",
        "summary": "Generated by a local model",
        "key_points": [],
        "important_terms": [],
        "common_mistakes": [],
        "exam_tips": {"lecture_based": [], "ai_suggestions": []},
        "difficulty": {"level": "Easy", "reason": "Simple material"},
        "estimated_study_time": "15 minutes",
        "prerequisites": [],
        "learning_objectives": [],
        "coverage": {"status": "Complete", "estimated_completeness": 100},
        "confidence_notes": "",
    }
    provider = _ollama_provider_returning(
        monkeypatch,
        f"```json\n{json.dumps(valid_guide)}\n```",
    )

    study_guide = StudyGuideService.generate(
        db_session,
        model_graph.course.id,
        provider,
    )
    generated_output = StudyGuideService.save_generated_output(
        db_session,
        model_graph.course.id,
        study_guide,
    )

    assert study_guide.title == "Ollama Guide"
    assert generated_output.id is not None
