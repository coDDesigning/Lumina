import pytest

import routes.course_qa as course_qa_route
from backend.app.models import (
    AiUsageLog,
    Conversation,
    ConversationMessage,
    Course,
    DocumentChunk,
    Role,
    UploadedDocument,
    User,
)
from schemas.ai_usage import GenerationType
from schemas.conversation import ConversationType
from services.course_qa import (
    CourseQAError,
    CourseQAService,
    NoReadyCourseMaterialError,
)
from services.retrieval_material import (
    MaterialNotIndexedError,
    NoRelevantMaterialError,
)
from services.text_generation import (
    GenerationMetadata,
    TextGenerationConnectionError,
    TextGenerationError,
    TextGenerationRateLimitError,
    TextGenerationTimeoutError,
)
from utils.ai_errors import (
    NO_READY_MATERIAL_MESSAGE,
    PUBLIC_MESSAGES,
    AiErrorCode,
)

from schemas.prompt_context import EducationLevel, MaterialKind, PromptContext

PROMPT_CONTEXT = PromptContext(
    education_level=EducationLevel.HIGH_SCHOOL,
    course_title="AP Biology",
    subject_area="Biology",
    material_kind=MaterialKind.TEXTBOOK,
)


IRRELEVANT_SEED = 4.0


class UncalledTextProvider:
    def generate_text(self, prompt: str) -> str:
        raise AssertionError("Provider should not be called")


def _add_ready_document(
    db_session,
    *,
    user: User,
    course: Course,
    file_hash: str,
    text: str | list[str],
    retrieval_env=None,
    seeds: list[float] | None = None,
) -> UploadedDocument:
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

    texts = [text] if isinstance(text, str) else text
    chunks = [
        DocumentChunk(
            document=document,
            course=course,
            chunk_index=index,
            page_number=None,
            text=chunk_text,
        )
        for index, chunk_text in enumerate(texts)
    ]
    db_session.add_all(chunks)
    db_session.flush()
    if retrieval_env is not None:
        retrieval_env.index(db_session, document, chunks, seeds=seeds)
    db_session.commit()
    return document


def test_get_course_material_uses_semantic_retrieval_and_ready_chunks(
    db_session,
    model_graph,
    retrieval_env,
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

    ready_chunks = [
        DocumentChunk(
            document=ready_document,
            course=model_graph.course,
            chunk_index=index,
            page_number=None,
            text=text,
        )
        for index, text in enumerate(["First ready chunk", "Second ready chunk"])
    ]
    uploaded_chunk = DocumentChunk(
        document=uploaded_document,
        course=model_graph.course,
        chunk_index=0,
        page_number=None,
        text="Should not be included",
    )
    db_session.add_all([*ready_chunks, uploaded_chunk])
    db_session.flush()
    retrieval_env.index(db_session, ready_document, ready_chunks, seeds=[0.0, 0.1])
    retrieval_env.index(db_session, uploaded_document, [uploaded_chunk])
    db_session.commit()

    question = "Which chunks are ready?"
    material = CourseQAService.get_course_material(
        db_session,
        model_graph.course.id,
        query=question,
    )

    assert material.text == "First ready chunk\n\nSecond ready chunk"
    assert material.chunks_used == 2
    assert material.chunks_available == 2
    assert material.truncated is False
    assert material.lowest_similarity is not None
    assert material.highest_similarity == pytest.approx(1.0)
    assert retrieval_env.provider.embed_query_calls == [question]


def test_retrieval_query_is_the_current_question(model_graph) -> None:
    question = "How does a red-black tree stay balanced?"

    assert (
        CourseQAService.build_retrieval_query(model_graph.course, question) == question
    )


def test_build_prompt_inserts_material_and_question() -> None:
    prompt = CourseQAService.build_prompt(
        "Photosynthesis converts light energy into chemical energy.",
        "What does photosynthesis do?",
        context=PROMPT_CONTEXT,
    )

    assert "{{COURSE_MATERIAL}}" not in prompt
    assert "{{QUESTION}}" not in prompt
    assert "Photosynthesis converts light energy into chemical energy." in prompt
    assert "What does photosynthesis do?" in prompt


def test_generate_returns_answer_and_logs_telemetry(
    db_session,
    model_graph,
    retrieval_env,
) -> None:
    _add_ready_document(
        db_session,
        user=model_graph.user,
        course=model_graph.course,
        file_hash="3" * 64,
        text="The cell membrane controls the movement of substances in and out of cells.",
        retrieval_env=retrieval_env,
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
    assert result.material.retrieval_narrowed is False
    assert result.material.lowest_similarity == pytest.approx(1.0)
    assert result.material.highest_similarity == pytest.approx(1.0)
    assert result.model_used == "gemini:gemini-2.5-flash"
    assert retrieval_env.provider.embed_query_calls == ["What is the cell membrane?"]

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


def test_generate_uses_only_retrieved_chunks(
    db_session,
    model_graph,
    retrieval_env,
) -> None:
    _add_ready_document(
        db_session,
        user=model_graph.user,
        course=model_graph.course,
        file_hash="31" + "3" * 62,
        text=["relevant-cell-material", "unrelated-alpha", "unrelated-beta"],
        retrieval_env=retrieval_env,
        seeds=[0.0, IRRELEVANT_SEED, IRRELEVANT_SEED],
    )
    captured_prompts: list[str] = []

    class FakeProvider:
        def generate_text(self, prompt: str) -> str:
            captured_prompts.append(prompt)
            return "Answer"

    generation = CourseQAService.generate(
        db_session,
        model_graph.course.id,
        "Explain cells",
        FakeProvider(),
        user_id=model_graph.user.id,
    )

    assert "relevant-cell-material" in captured_prompts[0]
    assert "unrelated-alpha" not in captured_prompts[0]
    assert "unrelated-beta" not in captured_prompts[0]
    assert generation.material.chunks_used == 1
    assert generation.material.chunks_available == 3
    assert generation.material.retrieval_narrowed is True
    assert generation.material.truncated is False


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


@pytest.mark.parametrize(
    ("indexed", "seeds", "expected_error", "error_category"),
    [
        (False, None, MaterialNotIndexedError, "material_not_indexed"),
        (True, [IRRELEVANT_SEED], NoRelevantMaterialError, "no_relevant_material"),
    ],
)
def test_generate_reports_retrieval_failures_without_calling_provider(
    db_session,
    model_graph,
    retrieval_env,
    indexed,
    seeds,
    expected_error,
    error_category,
) -> None:
    _add_ready_document(
        db_session,
        user=model_graph.user,
        course=model_graph.course,
        file_hash=("32" if indexed else "33") + "3" * 62,
        text="Material that retrieval must classify",
        retrieval_env=retrieval_env if indexed else None,
        seeds=seeds,
    )

    class UncalledProvider:
        def generate_text(self, prompt: str) -> str:
            raise AssertionError("Provider should not be called")

    with pytest.raises(expected_error):
        CourseQAService.generate(
            db_session,
            model_graph.course.id,
            "Explain a relevant topic",
            UncalledProvider(),
            user_id=model_graph.user.id,
        )

    log = (
        db_session.query(AiUsageLog)
        .filter_by(
            user_id=model_graph.user.id,
            course_id=model_graph.course.id,
            generation_type=GenerationType.COURSE_QA.value,
        )
        .one()
    )
    assert log.success is False
    assert log.error_category == error_category


def test_generate_wraps_provider_error(
    db_session,
    model_graph,
    retrieval_env,
) -> None:
    _add_ready_document(
        db_session,
        user=model_graph.user,
        course=model_graph.course,
        file_hash="4" * 64,
        text="Sample material",
        retrieval_env=retrieval_env,
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
    retrieval_env,
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
            retrieval_env=retrieval_env,
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
    assert payload["data"]["retrieval_narrowed"] is False
    assert payload["data"]["chunks_used"] == 1
    assert payload["data"]["chunks_available"] == 1
    assert payload["data"]["lowest_similarity"] == pytest.approx(1.0)
    assert payload["data"]["highest_similarity"] == pytest.approx(1.0)
    assert retrieval_env.provider.embed_query_calls == ["What is mitochondria?"]


@pytest.mark.parametrize(
    ("indexed", "seeds", "error_code"),
    [
        (False, None, AiErrorCode.MATERIAL_NOT_INDEXED),
        (True, [IRRELEVANT_SEED], AiErrorCode.NO_RELEVANT_MATERIAL),
    ],
)
def test_course_qa_api_curates_retrieval_failures(
    upload_api,
    retrieval_env,
    monkeypatch,
    indexed,
    seeds,
    error_code,
) -> None:
    with upload_api.session_factory() as session:
        user = session.get(User, upload_api.user_id)
        course = session.get(Course, upload_api.course_id)
        assert user is not None and course is not None
        _add_ready_document(
            session,
            user=user,
            course=course,
            file_hash=("51" if indexed else "52") + "5" * 62,
            text="Material whose retrieval state is under test",
            retrieval_env=retrieval_env if indexed else None,
            seeds=seeds,
        )

    class UncalledProvider:
        calls = 0

        def generate_text(self, prompt: str) -> str:
            self.calls += 1
            return "Unexpected answer"

    provider = UncalledProvider()
    monkeypatch.setattr(
        course_qa_route,
        "get_text_generation_provider",
        lambda: provider,
    )

    response = upload_api.client.post(
        f"/api/courses/{upload_api.course_id}/qa",
        json={"question": "Explain the requested topic"},
        headers=upload_api.authorization,
    )

    assert response.status_code == 409
    assert response.json()["detail"] == PUBLIC_MESSAGES[error_code]
    assert provider.calls == 0


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
    retrieval_env,
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
            retrieval_env=retrieval_env,
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
            retrieval_env=retrieval_env,
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
    retrieval_env,
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
            retrieval_env=retrieval_env,
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


def test_course_qa_creates_conversation_and_persists_messages(
    upload_api,
    retrieval_env,
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
            file_hash="9" * 64,
            text="Gravity attracts objects with mass toward each other.",
            retrieval_env=retrieval_env,
        )

    class FakeProvider:
        def generate_text_with_metadata(self, prompt: str):
            assert "Gravity attracts objects with mass" in prompt
            assert "What is gravity?" in prompt
            return (
                "Gravity is the attraction between objects with mass.",
                GenerationMetadata(
                    provider="ollama",
                    model="llama3.1",
                    latency_ms=10,
                ),
            )

    monkeypatch.setattr(
        course_qa_route,
        "get_text_generation_provider",
        lambda: FakeProvider(),
    )

    response = upload_api.client.post(
        f"/api/courses/{upload_api.course_id}/qa",
        json={"question": "What is gravity?"},
        headers=upload_api.authorization,
    )

    assert response.status_code == 200

    payload = response.json()
    conversation_id = payload["data"]["conversation_id"]

    assert isinstance(conversation_id, int)
    assert conversation_id > 0

    with upload_api.session_factory() as session:
        conversation = session.get(Conversation, conversation_id)

        assert conversation is not None
        assert conversation.user_id == upload_api.user_id
        assert conversation.course_id == upload_api.course_id
        assert conversation.conversation_type == ConversationType.COURSE_QA.value

        messages = (
            session.query(ConversationMessage)
            .filter_by(conversation_id=conversation_id)
            .order_by(ConversationMessage.id)
            .all()
        )

        assert len(messages) == 2
        assert messages[0].role == "user"
        assert messages[0].content == "What is gravity?"
        assert messages[1].role == "assistant"
        assert (
            messages[1].content
            == "Gravity is the attraction between objects with mass."
        )


def test_course_qa_continues_existing_conversation_with_history(
    upload_api,
    retrieval_env,
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
            file_hash="a" * 64,
            text="Photosynthesis converts light energy into chemical energy.",
            retrieval_env=retrieval_env,
        )

    captured_prompts: list[str] = []

    class FakeProvider:
        def generate_text_with_metadata(self, prompt: str):
            captured_prompts.append(prompt)

            if len(captured_prompts) == 1:
                return (
                    "Photosynthesis converts light energy into chemical energy.",
                    GenerationMetadata(
                        provider="ollama",
                        model="llama3.1",
                        latency_ms=10,
                    ),
                )

            return (
                "It means plants use light to produce stored chemical energy.",
                GenerationMetadata(
                    provider="ollama",
                    model="llama3.1",
                    latency_ms=10,
                ),
            )

    monkeypatch.setattr(
        course_qa_route,
        "get_text_generation_provider",
        lambda: FakeProvider(),
    )

    first_response = upload_api.client.post(
        f"/api/courses/{upload_api.course_id}/qa",
        json={"question": "What is photosynthesis?"},
        headers=upload_api.authorization,
    )

    assert first_response.status_code == 200

    conversation_id = first_response.json()["data"]["conversation_id"]

    second_response = upload_api.client.post(
        f"/api/courses/{upload_api.course_id}/qa",
        json={
            "question": "Can you explain that more simply?",
            "conversation_id": conversation_id,
        },
        headers=upload_api.authorization,
    )

    assert second_response.status_code == 200
    assert second_response.json()["data"]["conversation_id"] == conversation_id

    assert len(captured_prompts) == 2

    second_prompt = captured_prompts[1]

    assert "User: What is photosynthesis?" in second_prompt
    assert (
        "Assistant: Photosynthesis converts light energy into chemical energy."
        in second_prompt
    )
    assert "Can you explain that more simply?" in second_prompt
    assert retrieval_env.provider.embed_query_calls == [
        "What is photosynthesis?",
        "Can you explain that more simply?",
    ]

    with upload_api.session_factory() as session:
        messages = (
            session.query(ConversationMessage)
            .filter_by(conversation_id=conversation_id)
            .order_by(ConversationMessage.id)
            .all()
        )

        assert len(messages) == 4
        assert [message.role for message in messages] == [
            "user",
            "assistant",
            "user",
            "assistant",
        ]


def test_course_qa_rejects_conversation_from_another_course(
    upload_api,
    monkeypatch,
) -> None:
    with upload_api.session_factory() as session:
        conversation = Conversation(
            user_id=upload_api.user_id,
            course_id=upload_api.other_course_id,
            conversation_type=ConversationType.COURSE_QA.value,
        )
        session.add(conversation)
        session.commit()
        conversation_id = conversation.id

    monkeypatch.setattr(
        course_qa_route,
        "get_text_generation_provider",
        lambda: UncalledTextProvider(),
    )

    response = upload_api.client.post(
        f"/api/courses/{upload_api.course_id}/qa",
        json={"question": "Continue", "conversation_id": conversation_id},
        headers=upload_api.authorization,
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "Conversation not found"}


def test_course_qa_rejects_another_users_conversation(
    upload_api,
    monkeypatch,
) -> None:
    with upload_api.session_factory() as session:
        role = session.query(Role).filter_by(name="user").first()
        other_user = User(
            name="Conversation Intruder",
            email="conversation-intruder@example.com",
            password_hash="hash",
            role=role,
        )
        session.add(other_user)
        session.flush()
        conversation = Conversation(
            user_id=other_user.id,
            course_id=upload_api.course_id,
            conversation_type=ConversationType.COURSE_QA.value,
        )
        session.add(conversation)
        session.commit()
        conversation_id = conversation.id

    monkeypatch.setattr(
        course_qa_route,
        "get_text_generation_provider",
        lambda: UncalledTextProvider(),
    )

    response = upload_api.client.post(
        f"/api/courses/{upload_api.course_id}/qa",
        json={"question": "Continue", "conversation_id": conversation_id},
        headers=upload_api.authorization,
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "Conversation not found"}


def test_course_qa_rejects_ai_tutor_conversation(
    upload_api,
    monkeypatch,
) -> None:
    with upload_api.session_factory() as session:
        conversation = Conversation(
            user_id=upload_api.user_id,
            course_id=upload_api.course_id,
            conversation_type=ConversationType.AI_TUTOR.value,
        )
        session.add(conversation)
        session.commit()
        conversation_id = conversation.id

    monkeypatch.setattr(
        course_qa_route,
        "get_text_generation_provider",
        lambda: UncalledTextProvider(),
    )

    response = upload_api.client.post(
        f"/api/courses/{upload_api.course_id}/qa",
        json={"question": "Continue", "conversation_id": conversation_id},
        headers=upload_api.authorization,
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "Conversation not found"}


def test_course_qa_provider_failure_does_not_create_conversation(
    upload_api,
    retrieval_env,
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
            file_hash="b" * 64,
            text="Ready course material",
            retrieval_env=retrieval_env,
        )

        conversations_before = (
            session.query(Conversation)
            .filter_by(
                user_id=upload_api.user_id,
                course_id=upload_api.course_id,
            )
            .count()
        )

    class FailingProvider:
        def generate_text(self, prompt: str) -> str:
            raise TextGenerationError("Generation failed")

    monkeypatch.setattr(
        course_qa_route,
        "get_text_generation_provider",
        lambda: FailingProvider(),
    )

    response = upload_api.client.post(
        f"/api/courses/{upload_api.course_id}/qa",
        json={"question": "This generation should fail"},
        headers=upload_api.authorization,
    )

    assert response.status_code >= 400

    with upload_api.session_factory() as session:
        conversations_after = (
            session.query(Conversation)
            .filter_by(
                user_id=upload_api.user_id,
                course_id=upload_api.course_id,
            )
            .count()
        )

        assert conversations_after == conversations_before


def test_course_qa_rejects_nonpositive_conversation_id(
    upload_api,
) -> None:
    response = upload_api.client.post(
        f"/api/courses/{upload_api.course_id}/qa",
        json={
            "question": "Continue this conversation",
            "conversation_id": 0,
        },
        headers=upload_api.authorization,
    )

    assert response.status_code == 422
