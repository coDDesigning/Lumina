import pytest

import routes.ai_tutor as ai_tutor_route

from backend.app.models import (
    AiUsageLog,
    Conversation,
    ConversationMessage,
    Course,
    DocumentChunk,
    ProfileKnowledge,
    UploadedDocument,
    User,
)
from schemas.ai_usage import GenerationType
from schemas.conversation import ConversationType
from services.ai_tutor import (
    AiTutorError,
    AiTutorService,
    NoReadyCourseMaterialError,
)
from services.retrieval_material import (
    MaterialNotIndexedError,
    NoRelevantMaterialError,
)
from services.text_generation import GenerationMetadata, TextGenerationError
from utils.ai_errors import PUBLIC_MESSAGES, AiErrorCode

from schemas.prompt_context import EducationLevel, MaterialKind, PromptContext

PROMPT_CONTEXT = PromptContext(
    education_level=EducationLevel.HIGH_SCHOOL,
    course_title="AP Biology",
    subject_area="Biology",
    material_kind=MaterialKind.TEXTBOOK,
)
IRRELEVANT_SEED = 4.0


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
        original_file_name="ai-tutor.txt",
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

    ready_chunks = [
        DocumentChunk(
            document=ready_document,
            course=model_graph.course,
            chunk_index=index,
            page_number=None,
            text=text,
        )
        for index, text in enumerate(["First chunk", "Second chunk"])
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

    question = "How do these chunks explain the topic?"
    material = AiTutorService.get_course_material(
        db_session,
        model_graph.course.id,
        query=question,
    )

    assert material.text == "First chunk\n\nSecond chunk"
    assert material.chunks_used == 2
    assert material.chunks_available == 2
    assert material.truncated is False
    assert material.lowest_similarity is not None
    assert material.highest_similarity == pytest.approx(1.0)
    assert retrieval_env.provider.embed_query_calls == [question]


def test_retrieval_query_is_the_current_question(model_graph) -> None:
    question = "Can you give me a hint about virtual memory?"

    assert (
        AiTutorService.build_retrieval_query(model_graph.course, question) == question
    )


def test_build_prompt_inserts_course_material_and_question() -> None:
    prompt = AiTutorService.build_prompt(
        "Example lecture material",
        "What is an operating system?",
        context=PROMPT_CONTEXT,
    )

    assert "{{COURSE_MATERIAL}}" not in prompt
    assert "{{QUESTION}}" not in prompt
    assert "Example lecture material" in prompt
    assert "What is an operating system?" in prompt
    assert (
        "Open with a concise hint or guiding question, then give the full "
        "explanation." in prompt
    )
    assert "When appropriate, guide the student with a helpful hint" not in prompt
    assert "apparent level" not in prompt
    assert "high_school" in prompt
    assert "AP Biology" in prompt
    assert "Biology" in prompt
    assert "textbook" in prompt


def test_generate_returns_tutor_response(
    db_session,
    model_graph,
    retrieval_env,
) -> None:
    _add_ready_document(
        db_session,
        user=model_graph.user,
        course=model_graph.course,
        file_hash="c" * 64,
        text="An operating system manages computer hardware and software resources.",
        retrieval_env=retrieval_env,
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
    assert result.material.chunks_used == 1
    assert result.material.retrieval_narrowed is False
    assert result.material.lowest_similarity == pytest.approx(1.0)
    assert result.model_used.startswith("ollama:")
    assert retrieval_env.provider.embed_query_calls == [
        "What does an operating system do?"
    ]

    conversation = db_session.get(Conversation, result.conversation_id)
    assert conversation is not None
    assert conversation.conversation_type == ConversationType.AI_TUTOR.value


def test_generate_uses_only_retrieved_chunks(
    db_session,
    model_graph,
    retrieval_env,
) -> None:
    _add_ready_document(
        db_session,
        user=model_graph.user,
        course=model_graph.course,
        file_hash="c1" + "c" * 62,
        text=["relevant-memory-material", "unrelated-alpha", "unrelated-beta"],
        retrieval_env=retrieval_env,
        seeds=[0.0, IRRELEVANT_SEED, IRRELEVANT_SEED],
    )
    captured_prompts: list[str] = []

    class FakeProvider:
        def generate_text(self, prompt: str) -> str:
            captured_prompts.append(prompt)
            return "Start with a hint, then explain virtual memory."

    generation = AiTutorService.generate(
        db_session,
        model_graph.course.id,
        "Help me understand memory",
        FakeProvider(),
        user_id=model_graph.user.id,
    )

    assert "relevant-memory-material" in captured_prompts[0]
    assert "unrelated-alpha" not in captured_prompts[0]
    assert "unrelated-beta" not in captured_prompts[0]
    assert generation.material.chunks_used == 1
    assert generation.material.chunks_available == 3
    assert generation.material.retrieval_narrowed is True
    assert generation.material.truncated is False


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
        file_hash=("c2" if indexed else "c3") + "c" * 62,
        text="Material whose retrieval state is under test",
        retrieval_env=retrieval_env if indexed else None,
        seeds=seeds,
    )

    class UncalledProvider:
        def generate_text(self, prompt: str) -> str:
            raise AssertionError("Provider should not be called")

    with pytest.raises(expected_error):
        AiTutorService.generate(
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
            generation_type=GenerationType.AI_TUTOR.value,
        )
        .one()
    )
    assert log.success is False
    assert log.error_category == error_category


def test_generate_wraps_text_generation_error(
    db_session,
    model_graph,
    retrieval_env,
) -> None:
    _add_ready_document(
        db_session,
        user=model_graph.user,
        course=model_graph.course,
        file_hash="d" * 64,
        text="Example lecture material",
        retrieval_env=retrieval_env,
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

    assert db_session.query(Conversation).count() == 0


def test_ai_tutor_endpoint_persists_typed_conversation_and_history(
    upload_api,
    retrieval_env,
    monkeypatch,
) -> None:
    with upload_api.session_factory() as session:
        user = session.get(User, upload_api.user_id)
        course = session.get(Course, upload_api.course_id)

        assert user is not None
        assert course is not None

        _add_ready_document(
            session,
            user=user,
            course=course,
            file_hash="9" * 64,
            text="API tutor lecture material",
            retrieval_env=retrieval_env,
        )

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
    conversation_id = payload["data"]["conversation_id"]
    assert isinstance(conversation_id, int)
    assert payload["data"]["context_truncated"] is False
    assert payload["data"]["retrieval_narrowed"] is False
    assert payload["data"]["chunks_used"] == 1
    assert payload["data"]["chunks_available"] == 1
    assert payload["data"]["lowest_similarity"] == pytest.approx(1.0)
    assert payload["data"]["highest_similarity"] == pytest.approx(1.0)
    assert retrieval_env.provider.embed_query_calls == ["Explain the topic."]

    with upload_api.session_factory() as session:
        conversation = session.get(Conversation, conversation_id)
        assert conversation is not None
        assert conversation.user_id == upload_api.user_id
        assert conversation.course_id == upload_api.course_id
        assert conversation.conversation_type == ConversationType.AI_TUTOR.value
        assert [
            (message.role, message.content) for message in conversation.messages
        ] == [
            ("user", "Explain the topic."),
            ("assistant", "Here is a clear explanation of the topic."),
        ]

    listed = upload_api.client.get(
        f"/api/courses/{upload_api.course_id}/conversations",
        headers=upload_api.authorization,
    )
    assert listed.status_code == 200
    summaries = listed.json()["data"]
    assert len(summaries) == 1
    assert summaries[0]["id"] == conversation_id
    assert summaries[0]["conversation_type"] == "ai_tutor"
    assert summaries[0]["preview"] == "Explain the topic."
    assert summaries[0]["message_count"] == 2

    detail = upload_api.client.get(
        f"/api/courses/{upload_api.course_id}/conversations/{conversation_id}",
        headers=upload_api.authorization,
    )
    assert detail.status_code == 200
    detail_data = detail.json()["data"]
    assert detail_data["conversation_type"] == "ai_tutor"
    assert [
        (message["role"], message["content"]) for message in detail_data["messages"]
    ] == [
        ("user", "Explain the topic."),
        ("assistant", "Here is a clear explanation of the topic."),
    ]


@pytest.mark.parametrize(
    ("indexed", "seeds", "error_code"),
    [
        (False, None, AiErrorCode.MATERIAL_NOT_INDEXED),
        (True, [IRRELEVANT_SEED], AiErrorCode.NO_RELEVANT_MATERIAL),
    ],
)
def test_ai_tutor_endpoint_curates_retrieval_failures(
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
            file_hash=("91" if indexed else "92") + "9" * 62,
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
        ai_tutor_route,
        "get_text_generation_provider",
        lambda: provider,
    )

    response = upload_api.client.post(
        f"/api/courses/{upload_api.course_id}/ai-tutor",
        json={"question": "Explain the requested topic"},
        headers=upload_api.authorization,
    )

    assert response.status_code == 409
    assert response.json()["detail"] == PUBLIC_MESSAGES[error_code]
    assert provider.calls == 0


def test_ai_tutor_continues_conversation_with_history(
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
            file_hash="93" + "9" * 62,
            text="Virtual memory uses pages and page tables.",
            retrieval_env=retrieval_env,
        )

    captured_prompts: list[str] = []

    class FakeProvider:
        def generate_text_with_metadata(self, prompt: str):
            captured_prompts.append(prompt)
            answer = (
                "Hint: think about pages. Virtual memory maps pages."
                if len(captured_prompts) == 1
                else "Hint: follow the mapping. Page tables map virtual pages."
            )
            return answer, GenerationMetadata(provider="ollama", model="qwen3:8b")

    provider = FakeProvider()
    monkeypatch.setattr(
        ai_tutor_route,
        "get_text_generation_provider",
        lambda: provider,
    )

    first = upload_api.client.post(
        f"/api/courses/{upload_api.course_id}/ai-tutor",
        json={"question": "What is virtual memory?"},
        headers=upload_api.authorization,
    )
    assert first.status_code == 200
    conversation_id = first.json()["data"]["conversation_id"]

    second = upload_api.client.post(
        f"/api/courses/{upload_api.course_id}/ai-tutor",
        json={
            "question": "How do those pages get mapped?",
            "conversation_id": conversation_id,
        },
        headers=upload_api.authorization,
    )

    assert second.status_code == 200
    assert second.json()["data"]["conversation_id"] == conversation_id
    assert "User: What is virtual memory?" in captured_prompts[1]
    assert (
        "Assistant: Hint: think about pages. Virtual memory maps pages."
        in captured_prompts[1]
    )
    assert "How do those pages get mapped?" in captured_prompts[1]
    assert retrieval_env.provider.embed_query_calls == [
        "What is virtual memory?",
        "How do those pages get mapped?",
    ]

    with upload_api.session_factory() as session:
        messages = (
            session.query(ConversationMessage)
            .filter_by(conversation_id=conversation_id)
            .order_by(ConversationMessage.id)
            .all()
        )
        assert [message.role for message in messages] == [
            "user",
            "assistant",
            "user",
            "assistant",
        ]


def test_ai_tutor_rejects_course_qa_conversation(
    upload_api,
    monkeypatch,
) -> None:
    with upload_api.session_factory() as session:
        conversation = Conversation(
            user_id=upload_api.user_id,
            course_id=upload_api.course_id,
            conversation_type=ConversationType.COURSE_QA.value,
        )
        session.add(conversation)
        session.commit()
        conversation_id = conversation.id

    class UncalledProvider:
        def generate_text(self, prompt: str) -> str:
            raise AssertionError("Provider should not be called")

    monkeypatch.setattr(
        ai_tutor_route,
        "get_text_generation_provider",
        lambda: UncalledProvider(),
    )

    response = upload_api.client.post(
        f"/api/courses/{upload_api.course_id}/ai-tutor",
        json={"question": "Continue", "conversation_id": conversation_id},
        headers=upload_api.authorization,
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "Conversation not found"}


def test_ai_tutor_endpoint_requires_authentication(
    api_context,
) -> None:
    response = api_context.client.post(
        "/api/courses/1/ai-tutor",
        json={"question": "Explain the topic."},
    )

    assert response.status_code == 401


def test_ai_tutor_rejects_nonpositive_conversation_id(upload_api) -> None:
    response = upload_api.client.post(
        f"/api/courses/{upload_api.course_id}/ai-tutor",
        json={"question": "Continue", "conversation_id": 0},
        headers=upload_api.authorization,
    )

    assert response.status_code == 422


def test_ai_tutor_with_profile_knowledge_opt_in(
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
            file_hash="p" * 64,
            text="Operating Systems Virtual Memory and Page Tables.",
            retrieval_env=retrieval_env,
        )
        session.add(
            ProfileKnowledge(
                user_id=upload_api.user_id,
                topic="OS Background",
                detail="Student knows 32-bit paging.",
            )
        )
        session.commit()

    captured_prompts: list[str] = []

    class FakeProvider:
        def generate_text_with_metadata(self, prompt: str):
            captured_prompts.append(prompt)
            return (
                "Hint: Think about pages.\n\nVirtual memory maps virtual addresses.",
                GenerationMetadata(provider="ollama", model="llama3"),
            )

    monkeypatch.setattr(
        ai_tutor_route,
        "get_text_generation_provider",
        lambda: FakeProvider(),
    )

    # 1. Opt-in True
    res_opt_in = upload_api.client.post(
        f"/api/courses/{upload_api.course_id}/ai-tutor",
        json={"question": "What is paging?", "use_profile_knowledge": True},
        headers=upload_api.authorization,
    )
    assert res_opt_in.status_code == 200
    assert "SUPPLEMENTARY PROFILE CONTEXT" in captured_prompts[-1]
    assert "OS Background" in captured_prompts[-1]
    assert res_opt_in.json()["data"]["profile_knowledge_used"] is True
    assert res_opt_in.json()["data"]["profile_knowledge_items_used"] == 1

    # 2. Opt-in False (default)
    res_opt_out = upload_api.client.post(
        f"/api/courses/{upload_api.course_id}/ai-tutor",
        json={"question": "What is TLB?"},
        headers=upload_api.authorization,
    )
    assert res_opt_out.status_code == 200
    assert "SUPPLEMENTARY PROFILE CONTEXT" not in captured_prompts[-1]
    assert "OS Background" not in captured_prompts[-1]
    assert res_opt_out.json()["data"]["profile_knowledge_used"] is False
    assert res_opt_out.json()["data"]["profile_knowledge_items_used"] == 0
