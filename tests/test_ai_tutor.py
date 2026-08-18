import routes.ai_tutor as ai_tutor_route

from backend.app.models import (
    Course,
    DocumentChunk,
    UploadedDocument,
    User,
)
from services.ai_tutor import (
    AiTutorError,
    AiTutorService,
    NoReadyCourseMaterialError,
)
from services.text_generation import TextGenerationError


def _add_ready_document(
    db_session,
    model_graph,
    *,
    file_hash: str,
    text: str,
) -> None:
    document = UploadedDocument(
        original_file_name="ai-tutor.txt",
        file_type="txt",
        mime_type="text/plain",
        file_size=10,
        file_hash=file_hash,
        uploader=model_graph.user,
        course=model_graph.course,
        storage_provider="local:test",
        storage_key=f"{file_hash}.txt",
        status="ready",
    )

    db_session.add(
        DocumentChunk(
            document=document,
            course=model_graph.course,
            chunk_index=0,
            page_number=None,
            text=text,
        )
    )
    db_session.commit()


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

    material = AiTutorService.get_course_material(
        db_session,
        model_graph.course.id,
    )

    assert material.text == "First chunk\n\nSecond chunk"
    assert material.chunks_used == 2
    assert material.chunks_available == 2
    assert material.truncated is False


def test_build_prompt_inserts_course_material_and_question() -> None:
    prompt = AiTutorService.build_prompt(
        "Example lecture material",
        "What is an operating system?",
    )

    assert "{{COURSE_MATERIAL}}" not in prompt
    assert "{{QUESTION}}" not in prompt
    assert "Example lecture material" in prompt
    assert "What is an operating system?" in prompt


def test_generate_returns_tutor_response(
    db_session,
    model_graph,
) -> None:
    _add_ready_document(
        db_session,
        model_graph,
        file_hash="c" * 64,
        text="An operating system manages computer hardware and software resources.",
    )

    class FakeProvider:
        def generate_text(self, prompt: str) -> str:
            assert "operating system" in prompt
            assert "What does an operating system do?" in prompt
            return "An operating system manages hardware and software resources."

    result = AiTutorService.generate(
        db_session,
        model_graph.course.id,
        "What does an operating system do?",
        FakeProvider(),
    )

    assert result.response.answer == (
        "An operating system manages hardware and software resources."
    )
    assert result.material.truncated is False
    assert result.model_used.startswith("ollama:")


def test_generate_rejects_missing_ready_course_material(
    db_session,
    model_graph,
) -> None:
    class FakeProvider:
        def generate_text(self, prompt: str) -> str:
            raise AssertionError("Provider should not be called")

    try:
        AiTutorService.generate(
            db_session,
            model_graph.course.id,
            "Explain this topic.",
            FakeProvider(),
        )
    except NoReadyCourseMaterialError as exc:
        assert "No processed course material" in str(exc)
    else:
        raise AssertionError("Expected NoReadyCourseMaterialError")


def test_generate_wraps_text_generation_error(
    db_session,
    model_graph,
) -> None:
    _add_ready_document(
        db_session,
        model_graph,
        file_hash="d" * 64,
        text="Example lecture material",
    )

    class FakeProvider:
        def generate_text(self, prompt: str) -> str:
            raise TextGenerationError("Provider failed")

    try:
        AiTutorService.generate(
            db_session,
            model_graph.course.id,
            "Explain the material.",
            FakeProvider(),
        )
    except AiTutorError as exc:
        assert "Text generation provider failed." in str(exc)
    else:
        raise AssertionError("Expected AiTutorError")


def test_ai_tutor_endpoint_returns_generated_response(
    upload_api,
    monkeypatch,
) -> None:
    with upload_api.session_factory() as session:
        user = session.get(User, upload_api.user_id)
        course = session.get(Course, upload_api.course_id)

        assert user is not None
        assert course is not None

        document = UploadedDocument(
            original_file_name="api-ai-tutor.txt",
            file_type="txt",
            mime_type="text/plain",
            file_size=10,
            file_hash="9" * 64,
            uploader=user,
            course=course,
            storage_provider="local:test",
            storage_key="api-ai-tutor.txt",
            status="ready",
        )
        session.add(document)
        session.flush()

        session.add(
            DocumentChunk(
                document=document,
                course=course,
                chunk_index=0,
                page_number=None,
                text="API tutor lecture material",
            )
        )
        session.commit()

    class FakeProvider:
        def generate_text(self, prompt: str) -> str:
            assert "API tutor lecture material" in prompt
            assert "Explain the topic." in prompt
            return "Here is a clear explanation of the topic."

    monkeypatch.setattr(
        ai_tutor_route,
        "get_text_generation_provider",
        lambda: FakeProvider(),
    )

    response = upload_api.client.post(
        f"/api/courses/{upload_api.course_id}/ai-tutor",
        json={"question": "Explain the topic."},
        headers=upload_api.authorization,
    )

    assert response.status_code == 200

    payload = response.json()

    assert payload["success"] is True
    assert payload["message"] == "AI tutor response generated successfully"
    assert payload["data"]["answer"] == ("Here is a clear explanation of the topic.")
    assert payload["data"]["context_truncated"] is False
    assert payload["data"]["chunks_used"] == 1
    assert payload["data"]["chunks_available"] == 1


def test_ai_tutor_endpoint_requires_authentication(
    api_context,
) -> None:
    response = api_context.client.post(
        "/api/courses/1/ai-tutor",
        json={"question": "Explain the topic."},
    )

    assert response.status_code == 401
