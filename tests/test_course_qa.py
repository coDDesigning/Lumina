import routes.course_qa as course_qa_route
from backend.app.models import (
    AiUsageLog,
    Course,
    DocumentChunk,
    Role,
    UploadedDocument,
    User,
)
from schemas.ai_usage import GenerationType
from services.course_qa import (
    CourseQAError,
    CourseQAService,
    NoReadyCourseMaterialError,
)
from services.text_generation import (
    GenerationMetadata,
    TextGenerationConnectionError,
    TextGenerationError,
    TextGenerationRateLimitError,
    TextGenerationTimeoutError,
)
from utils.ai_errors import NO_READY_MATERIAL_MESSAGE


def _add_ready_document(
    db_session,
    *,
    user: User,
    course: Course,
    file_hash: str,
    text: str,
) -> None:
    document = UploadedDocument(
        original_file_name="qa-notes.txt",
        file_type="txt",
        mime_type="text/plain",
        file_size=10,
        file_hash=file_hash,
        uploader=user,
        course=course,
        storage_provider="local:test",
        storage_key=f"{file_hash}.txt",
        status="ready",
    )
    db_session.add(document)
    db_session.flush()

    db_session.add(
        DocumentChunk(
            document=document,
            course=course,
            chunk_index=0,
            page_number=None,
            text=text,
        )
    )
    db_session.commit()


def test_get_course_material_uses_ready_chunks(
    db_session,
    model_graph,
) -> None:
    ready_document = UploadedDocument(
        original_file_name="ready.txt",
        file_type="txt",
        mime_type="text/plain",
        file_size=10,
        file_hash="1" * 64,
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
        file_hash="2" * 64,
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
                chunk_index=0,
                page_number=None,
                text="First ready chunk",
            ),
            DocumentChunk(
                document=ready_document,
                course=model_graph.course,
                chunk_index=1,
                page_number=None,
                text="Second ready chunk",
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

    material = CourseQAService.get_course_material(
        db_session,
        model_graph.course.id,
    )

    assert material.text == "First ready chunk\n\nSecond ready chunk"
    assert material.chunks_used == 2
    assert material.chunks_available == 2
    assert material.truncated is False


def test_build_prompt_inserts_material_and_question() -> None:
    prompt = CourseQAService.build_prompt(
        "Photosynthesis converts light energy into chemical energy.",
        "What does photosynthesis do?",
    )

    assert "{{COURSE_MATERIAL}}" not in prompt
    assert "{{QUESTION}}" not in prompt
    assert "Photosynthesis converts light energy into chemical energy." in prompt
    assert "What does photosynthesis do?" in prompt


def test_generate_returns_answer_and_logs_telemetry(
    db_session,
    model_graph,
) -> None:
    _add_ready_document(
        db_session,
        user=model_graph.user,
        course=model_graph.course,
        file_hash="3" * 64,
        text="The cell membrane controls the movement of substances in and out of cells.",
    )

    class FakeProvider:
        def generate_text_with_metadata(self, prompt: str):
            assert "cell membrane" in prompt
            assert "What is the cell membrane?" in prompt
            return (
                "The cell membrane controls movement in and out of cells.",
                GenerationMetadata(
                    provider="gemini",
                    model="gemini-2.5-flash",
                    prompt_tokens=100,
                    completion_tokens=20,
                    total_tokens=120,
                    latency_ms=250,
                ),
            )

    result = CourseQAService.generate(
        db_session,
        model_graph.course.id,
        "What is the cell membrane?",
        FakeProvider(),
        user_id=model_graph.user.id,
    )

    assert (
        result.response.answer
        == "The cell membrane controls movement in and out of cells."
    )
    assert result.material.truncated is False
    assert result.material.chunks_used == 1
    assert result.model_used == "gemini:gemini-2.5-flash"

    # Verify telemetry log
    log = (
        db_session.query(AiUsageLog)
        .filter_by(
            user_id=model_graph.user.id,
            course_id=model_graph.course.id,
            generation_type=GenerationType.COURSE_QA.value,
        )
        .first()
    )
    assert log is not None
    assert log.success is True
    assert log.provider == "gemini"
    assert log.model == "gemini-2.5-flash"
    assert log.prompt_tokens == 100
    assert log.completion_tokens == 20


def test_generate_rejects_missing_material_and_logs_failure(
    db_session,
    model_graph,
) -> None:
    class UncalledProvider:
        def generate_text(self, prompt: str) -> str:
            raise AssertionError("Provider should not be called")

    try:
        CourseQAService.generate(
            db_session,
            model_graph.course.id,
            "What is thermodynamics?",
            UncalledProvider(),
            user_id=model_graph.user.id,
        )
    except NoReadyCourseMaterialError as exc:
        assert NO_READY_MATERIAL_MESSAGE in str(exc)
    else:
        raise AssertionError("Expected NoReadyCourseMaterialError")

    # Verify failure telemetry
    log = (
        db_session.query(AiUsageLog)
        .filter_by(
            user_id=model_graph.user.id,
            course_id=model_graph.course.id,
            generation_type=GenerationType.COURSE_QA.value,
        )
        .first()
    )
    assert log is not None
    assert log.success is False
    assert log.error_category == "no_ready_material"


def test_generate_wraps_provider_error(
    db_session,
    model_graph,
) -> None:
    _add_ready_document(
        db_session,
        user=model_graph.user,
        course=model_graph.course,
        file_hash="4" * 64,
        text="Sample material",
    )

    class FailingProvider:
        def generate_text(self, prompt: str) -> str:
            raise TextGenerationError("Upstream failure")

    try:
        CourseQAService.generate(
            db_session,
            model_graph.course.id,
            "Explain sample material",
            FailingProvider(),
            user_id=model_graph.user.id,
        )
    except CourseQAError as exc:
        assert "Text generation provider failed." in str(exc)
    else:
        raise AssertionError("Expected CourseQAError")


def test_course_qa_api_success(
    upload_api,
    monkeypatch,
) -> None:
    with upload_api.session_factory() as session:
        user = session.get(User, upload_api.user_id)
        course = session.get(Course, upload_api.course_id)
        assert user is not None and course is not None

        _add_ready_document(
            session,
            user=user,
            course=course,
            file_hash="5" * 64,
            text="Mitochondria are the powerhouse of the cell.",
        )

    class FakeProvider:
        def generate_text_with_metadata(self, prompt: str):
            assert "Mitochondria" in prompt
            assert "What is mitochondria?" in prompt
            return (
                "Mitochondria generate cellular energy (ATP).",
                GenerationMetadata(provider="ollama", model="llama3.1", latency_ms=100),
            )

    monkeypatch.setattr(
        course_qa_route,
        "get_text_generation_provider",
        lambda: FakeProvider(),
    )

    response = upload_api.client.post(
        f"/api/courses/{upload_api.course_id}/qa",
        json={"question": "What is mitochondria?"},
        headers=upload_api.authorization,
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["message"] == "Course Q&A answer generated successfully"
    assert payload["data"]["answer"] == "Mitochondria generate cellular energy (ATP)."
    assert payload["data"]["context_truncated"] is False
    assert payload["data"]["chunks_used"] == 1
    assert payload["data"]["chunks_available"] == 1


def test_course_qa_api_unauthorized_returns_404(
    upload_api,
    db_session,
) -> None:
    role = db_session.query(Role).filter_by(name="user").first()
    other_user = User(
        name="Other User",
        email="other@example.com",
        password_hash="hash",
        role=role,
    )
    other_course = Course(
        owner=other_user,
        title="Other Course",
    )
    db_session.add_all([other_user, other_course])
    db_session.commit()

    # User trying to access other_user's course must get 404 (not 403)
    response = upload_api.client.post(
        f"/api/courses/{other_course.id}/qa",
        json={"question": "What is in other course?"},
        headers=upload_api.authorization,
    )
    assert response.status_code == 404


def test_course_qa_api_requires_auth(
    api_context,
) -> None:
    response = api_context.client.post(
        "/api/courses/1/qa",
        json={"question": "Unauthenticated question"},
    )
    assert response.status_code == 401


def test_course_qa_cross_course_isolation(
    upload_api,
    monkeypatch,
) -> None:
    with upload_api.session_factory() as session:
        user = session.get(User, upload_api.user_id)
        course1 = session.get(Course, upload_api.course_id)
        assert user is not None and course1 is not None

        # Course 1 material
        _add_ready_document(
            session,
            user=user,
            course=course1,
            file_hash="6" * 64,
            text="Course 1 Secret Physics Formula E=mc^2",
        )

        # Course 2 material for the same user
        course2 = Course(owner=user, title="Course 2 Chemistry")
        session.add(course2)
        session.flush()

        _add_ready_document(
            session,
            user=user,
            course=course2,
            file_hash="7" * 64,
            text="Course 2 Chemistry Formula H2O",
        )
        course2_id = course2.id

    captured_prompts = []

    class InspectingProvider:
        def generate_text_with_metadata(self, prompt: str):
            captured_prompts.append(prompt)
            return "Answer", GenerationMetadata(
                provider="ollama", model="llama3.1", latency_ms=10
            )

    monkeypatch.setattr(
        course_qa_route,
        "get_text_generation_provider",
        lambda: InspectingProvider(),
    )

    # Query Course 2
    response = upload_api.client.post(
        f"/api/courses/{course2_id}/qa",
        json={"question": "What is the formula?"},
        headers=upload_api.authorization,
    )
    assert response.status_code == 200
    assert len(captured_prompts) == 1
    # Course 2 material MUST be in prompt
    assert "Course 2 Chemistry Formula H2O" in captured_prompts[0]
    # Course 1 material MUST NOT leak into Course 2
    assert "Course 1 Secret Physics Formula E=mc^2" not in captured_prompts[0]


def test_course_qa_provider_error_status_codes(
    upload_api,
    monkeypatch,
) -> None:
    with upload_api.session_factory() as session:
        user = session.get(User, upload_api.user_id)
        course = session.get(Course, upload_api.course_id)
        _add_ready_document(
            session,
            user=user,
            course=course,
            file_hash="8" * 64,
            text="Ready material",
        )

    # Test 503 Provider Unavailable
    class UnreachableProvider:
        def generate_text(self, prompt: str) -> str:
            raise TextGenerationConnectionError("Cannot reach provider")

    monkeypatch.setattr(
        course_qa_route,
        "get_text_generation_provider",
        lambda: UnreachableProvider(),
    )
    res = upload_api.client.post(
        f"/api/courses/{upload_api.course_id}/qa",
        json={"question": "test"},
        headers=upload_api.authorization,
    )
    assert res.status_code == 503

    # Test 504 Provider Timeout
    class TimeoutProvider:
        def generate_text(self, prompt: str) -> str:
            raise TextGenerationTimeoutError("Timeout")

    monkeypatch.setattr(
        course_qa_route,
        "get_text_generation_provider",
        lambda: TimeoutProvider(),
    )
    res = upload_api.client.post(
        f"/api/courses/{upload_api.course_id}/qa",
        json={"question": "test"},
        headers=upload_api.authorization,
    )
    assert res.status_code == 504

    # Test 429 Provider Rate Limited
    class RateLimitedProvider:
        def generate_text(self, prompt: str) -> str:
            raise TextGenerationRateLimitError("Rate limit")

    monkeypatch.setattr(
        course_qa_route,
        "get_text_generation_provider",
        lambda: RateLimitedProvider(),
    )
    res = upload_api.client.post(
        f"/api/courses/{upload_api.course_id}/qa",
        json={"question": "test"},
        headers=upload_api.authorization,
    )
    assert res.status_code == 429
