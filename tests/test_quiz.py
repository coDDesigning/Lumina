import routes.quiz as quiz_route
from sqlalchemy import select

from backend.app.models import (
    Course,
    DocumentChunk,
    QuizQuestion,
    UploadedDocument,
    User,
)
from services.quiz import (
    NoReadyCourseMaterialError,
    QuizGenerationError,
    QuizService,
)
from services.text_generation import (
    TextGenerationConnectionError,
    TextGenerationError,
    TextGenerationTimeoutError,
)


def _valid_quiz_payload() -> dict[str, object]:
    return {
        "title": "Example Quiz",
        "questions": [
            {
                "question_number": index,
                "topic": f"Topic {index}",
                "question": f"Question {index}?",
                "options": [
                    "Option A",
                    "Option B",
                    "Option C",
                    "Option D",
                ],
                "correct_option_index": 0,
                "explanation": "Option A is correct.",
            }
            for index in range(1, 11)
        ],
    }


def _add_ready_document(
    db_session,
    model_graph,
    *,
    file_hash: str,
    text: str,
) -> None:
    document = UploadedDocument(
        original_file_name="quiz.txt",
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

    material = QuizService.get_course_material(
        db_session,
        model_graph.course.id,
    )

    assert material == "First chunk\n\nSecond chunk"


def test_build_prompt_inserts_course_material() -> None:
    prompt = QuizService.build_prompt("Example lecture material")

    assert "{{TEXT}}" not in prompt
    assert "Example lecture material" in prompt


def test_generate_returns_validated_quiz(
    db_session,
    model_graph,
) -> None:
    _add_ready_document(
        db_session,
        model_graph,
        file_hash="c" * 64,
        text="Example lecture material",
    )

    class FakeProvider:
        def generate_json(self, prompt: str) -> dict[str, object]:
            assert "Example lecture material" in prompt
            return _valid_quiz_payload()

    result = QuizService.generate(
        db_session,
        model_graph.course.id,
        FakeProvider(),
    )

    assert result.title == "Example Quiz"
    assert len(result.questions) == 10
    assert result.questions[0].question_number == 1
    assert result.questions[0].topic == "Topic 1"
    assert result.questions[0].correct_option_index == 0


def test_generate_rejects_missing_ready_course_material(
    db_session,
    model_graph,
) -> None:
    class FakeProvider:
        def generate_json(self, prompt: str) -> dict[str, object]:
            raise AssertionError("Provider should not be called")

    try:
        QuizService.generate(
            db_session,
            model_graph.course.id,
            FakeProvider(),
        )
    except NoReadyCourseMaterialError as exc:
        assert "No ready course material" in str(exc)
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
        def generate_json(self, prompt: str) -> dict[str, object]:
            raise TextGenerationError("Provider failed")

    try:
        QuizService.generate(
            db_session,
            model_graph.course.id,
            FakeProvider(),
        )
    except QuizGenerationError as exc:
        assert "Text generation provider failed." in str(exc)
    else:
        raise AssertionError("Expected QuizGenerationError")


def test_generate_rejects_invalid_quiz_structure(
    db_session,
    model_graph,
) -> None:
    _add_ready_document(
        db_session,
        model_graph,
        file_hash="e" * 64,
        text="Example lecture material",
    )

    class FakeProvider:
        def generate_json(self, prompt: str) -> dict[str, object]:
            return {
                "title": "Invalid Quiz",
                "questions": [],
            }

    try:
        QuizService.generate(
            db_session,
            model_graph.course.id,
            FakeProvider(),
        )
    except QuizGenerationError as exc:
        assert "invalid structure" in str(exc)
    else:
        raise AssertionError("Expected QuizGenerationError")


def test_save_generated_quiz_persists_questions(
    db_session,
    model_graph,
) -> None:
    _add_ready_document(
        db_session,
        model_graph,
        file_hash="f" * 64,
        text="Persisted lecture material",
    )

    class FakeProvider:
        def generate_json(self, prompt: str) -> dict[str, object]:
            return _valid_quiz_payload()

    quiz_data = QuizService.generate(
        db_session,
        model_graph.course.id,
        FakeProvider(),
    )

    quiz = QuizService.save_generated_quiz(
        db_session,
        model_graph.course.id,
        quiz_data,
    )

    questions = db_session.scalars(
        select(QuizQuestion)
        .where(QuizQuestion.quiz_id == quiz.id)
        .order_by(QuizQuestion.question_index)
    ).all()

    assert quiz.id is not None
    assert quiz.title == "Example Quiz"
    assert len(questions) == 10

    assert questions[0].question_index == 0
    assert questions[0].question_text == "Question 1?"
    assert questions[0].options == [
        "Option A",
        "Option B",
        "Option C",
        "Option D",
    ]
    assert questions[0].correct_option_index == 0


def test_generate_quiz_endpoint_returns_generated_quiz(
    upload_api,
    monkeypatch,
) -> None:
    with upload_api.session_factory() as session:
        user = session.get(User, upload_api.user_id)
        course = session.get(Course, upload_api.course_id)

        assert user is not None
        assert course is not None

        document = UploadedDocument(
            original_file_name="api-quiz.txt",
            file_type="txt",
            mime_type="text/plain",
            file_size=10,
            file_hash="9" * 64,
            uploader=user,
            course=course,
            storage_provider="local:test",
            storage_key="api-quiz.txt",
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
                text="API quiz lecture material",
            )
        )
        session.commit()

    class FakeProvider:
        def generate_json(self, prompt: str) -> dict[str, object]:
            assert "API quiz lecture material" in prompt
            return _valid_quiz_payload()

    monkeypatch.setattr(
        quiz_route,
        "get_text_generation_provider",
        lambda: FakeProvider(),
    )

    response = upload_api.client.post(
        f"/api/courses/{upload_api.course_id}/quiz",
        headers=upload_api.authorization,
    )

    assert response.status_code == 200

    payload = response.json()

    assert payload["success"] is True
    assert payload["message"] == "Quiz generated successfully"
    assert payload["data"]["title"] == "Example Quiz"
    assert len(payload["data"]["questions"]) == 10


def test_quiz_endpoint_reports_unreachable_provider_as_unavailable(
    upload_api,
    monkeypatch,
) -> None:
    def unavailable() -> None:
        raise TextGenerationConnectionError("Ollama could not be reached.")

    monkeypatch.setattr(quiz_route, "get_text_generation_provider", unavailable)

    response = upload_api.client.post(
        f"/api/courses/{upload_api.course_id}/quiz",
        headers=upload_api.authorization,
    )

    assert response.status_code == 503
    assert "could not be reached" in response.json()["detail"]


def test_quiz_endpoint_reports_provider_timeout_as_unavailable(
    upload_api,
    monkeypatch,
) -> None:
    def timed_out() -> None:
        raise TextGenerationTimeoutError("Ollama did not respond in time.")

    monkeypatch.setattr(quiz_route, "get_text_generation_provider", timed_out)

    response = upload_api.client.post(
        f"/api/courses/{upload_api.course_id}/quiz",
        headers=upload_api.authorization,
    )

    assert response.status_code == 503


def test_quiz_endpoint_still_reports_malformed_output_as_server_error(
    upload_api,
    monkeypatch,
) -> None:
    def unusable() -> None:
        raise TextGenerationError("Ollama returned invalid JSON.")

    monkeypatch.setattr(quiz_route, "get_text_generation_provider", unusable)

    response = upload_api.client.post(
        f"/api/courses/{upload_api.course_id}/quiz",
        headers=upload_api.authorization,
    )

    assert response.status_code == 500
