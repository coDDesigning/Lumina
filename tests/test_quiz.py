import routes.quiz as quiz_route
from sqlalchemy import select

from backend.app.models import (
    Course,
    DocumentChunk,
    Quiz,
    QuizQuestion,
    UploadedDocument,
    User,
)
from schemas.quiz import QuizDifficulty, QuizQuestionType, QuizRequest
import pytest

from services.quiz import (
    DIFFICULTY_DIRECTIVES,
    QUESTION_TYPE_DIRECTIVES,
    NoReadyCourseMaterialError,
    QuizGenerationError,
    QuizService,
)
from services.text_generation import (
    TextGenerationConnectionError,
    TextGenerationError,
    TextGenerationTimeoutError,
)
from utils.ai_errors import PUBLIC_MESSAGES, AiErrorCode


QUIZ_REQUEST = {
    "question_count": 10,
    "question_type": "multiple_choice",
    "difficulty": "medium",
    "topic_focus": "All Topics",
}


def _quiz_request(**overrides) -> QuizRequest:
    return QuizRequest(
        question_count=overrides.get("question_count", 10),
        question_type=overrides.get("question_type", QuizQuestionType.MULTIPLE_CHOICE),
        difficulty=overrides.get("difficulty", QuizDifficulty.MEDIUM),
        topic_focus=overrides.get("topic_focus", "All Topics"),
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

    assert material.text == "First chunk\n\nSecond chunk"
    assert material.chunks_used == 2
    assert material.chunks_available == 2
    assert material.truncated is False


def test_build_prompt_inserts_course_material() -> None:
    prompt = QuizService.build_prompt("Example lecture material", _quiz_request())

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

    generation = QuizService.generate(
        db_session,
        model_graph.course.id,
        _quiz_request(),
        FakeProvider(),
    )

    result = generation.quiz

    assert result.title == "Example Quiz"
    assert len(result.questions) == 10
    assert result.questions[0].question_number == 1
    assert result.questions[0].topic == "Topic 1"
    assert result.questions[0].correct_option_index == 0
    assert generation.material.truncated is False
    assert generation.model_used.startswith("ollama:")


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
            _quiz_request(),
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
        def generate_json(self, prompt: str) -> dict[str, object]:
            raise TextGenerationError("Provider failed")

    try:
        QuizService.generate(
            db_session,
            model_graph.course.id,
            _quiz_request(),
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
            _quiz_request(),
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
        _quiz_request(),
        FakeProvider(),
    )

    quiz = QuizService.save_generated_quiz(
        db_session,
        model_graph.course.id,
        quiz_data.quiz,
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
        json=QUIZ_REQUEST,
        headers=upload_api.authorization,
    )

    assert response.status_code == 200

    payload = response.json()

    assert payload["success"] is True
    assert payload["message"] == "Quiz generated successfully"
    assert payload["data"]["quiz"]["title"] == "Example Quiz"
    assert len(payload["data"]["quiz"]["questions"]) == 10
    assert payload["data"]["context_truncated"] is False
    assert payload["data"]["chunks_used"] == 1
    assert payload["data"]["chunks_available"] == 1


def test_quiz_endpoint_reports_unreachable_provider_as_unavailable(
    upload_api,
    monkeypatch,
) -> None:
    def unavailable() -> None:
        raise TextGenerationConnectionError("Ollama could not be reached.")

    monkeypatch.setattr(quiz_route, "get_text_generation_provider", unavailable)

    response = upload_api.client.post(
        f"/api/courses/{upload_api.course_id}/quiz",
        json=QUIZ_REQUEST,
        headers=upload_api.authorization,
    )

    assert response.status_code == 503
    assert (
        response.json()["detail"] == PUBLIC_MESSAGES[AiErrorCode.PROVIDER_UNAVAILABLE]
    )
    assert "Ollama" not in response.json()["detail"]


def test_quiz_endpoint_reports_provider_timeout_as_gateway_timeout(
    upload_api,
    monkeypatch,
) -> None:
    def timed_out() -> None:
        raise TextGenerationTimeoutError("Ollama did not respond in time.")

    monkeypatch.setattr(quiz_route, "get_text_generation_provider", timed_out)

    response = upload_api.client.post(
        f"/api/courses/{upload_api.course_id}/quiz",
        json=QUIZ_REQUEST,
        headers=upload_api.authorization,
    )

    assert response.status_code == 504


def test_quiz_endpoint_still_reports_malformed_output_as_server_error(
    upload_api,
    monkeypatch,
) -> None:
    def unusable() -> None:
        raise TextGenerationError("Ollama returned invalid JSON.")

    monkeypatch.setattr(quiz_route, "get_text_generation_provider", unusable)

    response = upload_api.client.post(
        f"/api/courses/{upload_api.course_id}/quiz",
        json=QUIZ_REQUEST,
        headers=upload_api.authorization,
    )

    assert response.status_code == 500


def _true_false_payload(count: int = 2) -> dict[str, object]:
    return {
        "title": "True False Quiz",
        "questions": [
            {
                "question_number": index,
                "topic": f"Topic {index}",
                "question": f"Statement {index} is correct?",
                "options": ["True", "False"],
                "correct_option_index": 0,
                "explanation": "The statement matches the lecture material.",
            }
            for index in range(1, count + 1)
        ],
    }


def test_build_prompt_applies_the_requested_parameters() -> None:
    prompt = QuizService.build_prompt(
        "Example lecture material",
        _quiz_request(
            question_count=6,
            question_type=QuizQuestionType.TRUE_FALSE,
            difficulty=QuizDifficulty.HARD,
            topic_focus="Eigenvalues",
        ),
    )

    assert "{{QUESTION_COUNT}}" not in prompt
    assert "{{QUESTION_TYPE_DIRECTIVE}}" not in prompt
    assert "{{DIFFICULTY_DIRECTIVE}}" not in prompt
    assert "{{TOPIC_FOCUS}}" not in prompt
    assert "Generate exactly 6 questions" in prompt
    assert QUESTION_TYPE_DIRECTIVES[QuizQuestionType.TRUE_FALSE] in prompt
    assert DIFFICULTY_DIRECTIVES[QuizDifficulty.HARD] in prompt
    assert "Eigenvalues" in prompt


def test_generate_accepts_the_requested_true_false_questions(
    db_session,
    model_graph,
) -> None:
    _add_ready_document(
        db_session,
        model_graph,
        file_hash="1" * 64,
        text="Example lecture material",
    )

    class FakeProvider:
        def generate_json(self, prompt: str) -> dict[str, object]:
            return _true_false_payload(2)

    generation = QuizService.generate(
        db_session,
        model_graph.course.id,
        _quiz_request(question_count=2, question_type=QuizQuestionType.TRUE_FALSE),
        FakeProvider(),
    )

    assert len(generation.quiz.questions) == 2
    assert generation.quiz.questions[0].options == ["True", "False"]


def test_generate_rejects_a_question_count_mismatch(
    db_session,
    model_graph,
) -> None:
    _add_ready_document(
        db_session,
        model_graph,
        file_hash="2" * 64,
        text="Example lecture material",
    )

    class FakeProvider:
        def generate_json(self, prompt: str) -> dict[str, object]:
            return _valid_quiz_payload()

    try:
        QuizService.generate(
            db_session,
            model_graph.course.id,
            _quiz_request(question_count=5),
            FakeProvider(),
        )
    except QuizGenerationError as exc:
        assert "invalid structure" in str(exc)
    else:
        raise AssertionError("Expected QuizGenerationError")


def test_generate_rejects_a_question_type_mismatch(
    db_session,
    model_graph,
) -> None:
    _add_ready_document(
        db_session,
        model_graph,
        file_hash="3" * 64,
        text="Example lecture material",
    )

    class FakeProvider:
        def generate_json(self, prompt: str) -> dict[str, object]:
            return _valid_quiz_payload()

    try:
        QuizService.generate(
            db_session,
            model_graph.course.id,
            _quiz_request(question_type=QuizQuestionType.TRUE_FALSE),
            FakeProvider(),
        )
    except QuizGenerationError as exc:
        assert "invalid structure" in str(exc)
    else:
        raise AssertionError("Expected QuizGenerationError")


def test_save_generated_quiz_persists_topic_and_explanation(
    db_session,
    model_graph,
) -> None:
    _add_ready_document(
        db_session,
        model_graph,
        file_hash="4" * 64,
        text="Persisted lecture material",
    )

    class FakeProvider:
        def generate_json(self, prompt: str) -> dict[str, object]:
            return _valid_quiz_payload()

    generation = QuizService.generate(
        db_session,
        model_graph.course.id,
        _quiz_request(),
        FakeProvider(),
    )
    quiz = QuizService.save_generated_quiz(
        db_session,
        model_graph.course.id,
        generation.quiz,
    )

    questions = db_session.scalars(
        select(QuizQuestion)
        .where(QuizQuestion.quiz_id == quiz.id)
        .order_by(QuizQuestion.question_index)
    ).all()

    assert questions[0].topic == "Topic 1"
    assert questions[0].explanation == "Option A is correct."

    view = QuizService.build_quiz_view(quiz)
    assert view.quiz_id == quiz.id
    assert view.questions[0].question_id == questions[0].id
    assert view.questions[0].question_number == 1
    assert view.questions[0].topic == "Topic 1"
    assert view.questions[0].explanation == "Option A is correct."


def test_generate_quiz_endpoint_exposes_persisted_identifiers(
    upload_api,
    monkeypatch,
) -> None:
    with upload_api.session_factory() as session:
        user = session.get(User, upload_api.user_id)
        course = session.get(Course, upload_api.course_id)
        document = UploadedDocument(
            original_file_name="ids-quiz.txt",
            file_type="txt",
            mime_type="text/plain",
            file_size=10,
            file_hash="5" * 64,
            uploader=user,
            course=course,
            storage_provider="local:test",
            storage_key="ids-quiz.txt",
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
                text="Identifier quiz lecture material",
            )
        )
        session.commit()

    class FakeProvider:
        def generate_json(self, prompt: str) -> dict[str, object]:
            return _valid_quiz_payload()

    monkeypatch.setattr(
        quiz_route,
        "get_text_generation_provider",
        lambda: FakeProvider(),
    )

    response = upload_api.client.post(
        f"/api/courses/{upload_api.course_id}/quiz",
        json=QUIZ_REQUEST,
        headers=upload_api.authorization,
    )

    assert response.status_code == 200, response.text
    quiz_payload = response.json()["data"]["quiz"]

    with upload_api.session_factory() as session:
        stored = session.scalars(
            select(Quiz).where(Quiz.course_id == upload_api.course_id)
        ).all()

    assert len(stored) == 1
    assert quiz_payload["quiz_id"] == stored[0].id
    assert [q["question_number"] for q in quiz_payload["questions"]] == list(
        range(1, 11)
    )
    assert all(q["question_id"] > 0 for q in quiz_payload["questions"])
    assert quiz_payload["questions"][0]["topic"] == "Topic 1"
    assert quiz_payload["questions"][0]["explanation"] == "Option A is correct."


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(None, id="missing_body"),
        pytest.param({**QUIZ_REQUEST, "question_count": 0}, id="count_too_small"),
        pytest.param({**QUIZ_REQUEST, "question_count": 21}, id="count_too_large"),
        pytest.param({**QUIZ_REQUEST, "question_type": "essay"}, id="unknown_type"),
        pytest.param({**QUIZ_REQUEST, "difficulty": "brutal"}, id="unknown_difficulty"),
        pytest.param({**QUIZ_REQUEST, "topic_focus": ""}, id="empty_topic_focus"),
        pytest.param(
            {**QUIZ_REQUEST, "topic_focus": "x" * 201},
            id="overlong_topic_focus",
        ),
    ],
)
def test_generate_quiz_endpoint_rejects_an_invalid_request(
    upload_api,
    monkeypatch,
    body,
) -> None:
    def unreachable() -> None:
        raise AssertionError("Provider should not be constructed")

    monkeypatch.setattr(quiz_route, "get_text_generation_provider", unreachable)

    response = upload_api.client.post(
        f"/api/courses/{upload_api.course_id}/quiz",
        json=body,
        headers=upload_api.authorization,
    )

    assert response.status_code == 422, response.text
