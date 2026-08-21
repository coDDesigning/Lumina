import routes.flashcard as flashcard_route
from sqlalchemy import select

from backend.app.models import (
    Course,
    DocumentChunk,
    GeneratedOutput,
    ProfileKnowledge,
    UploadedDocument,
    User,
)
from schemas.flashcard import (
    FlashcardGenerationContext,
    FlashcardGenerationSettings,
)
from services.flashcard import (
    FlashcardGenerationError,
    FlashcardService,
    NoReadyCourseMaterialError,
)
from services.text_generation import TextGenerationError


def _valid_flashcard_payload() -> dict[str, object]:
    return {
        "deck_title": "Example Flashcards",
        "card_count": 10,
        "flashcards": [
            {
                "card_number": index,
                "difficulty": (
                    "Easy" if index <= 3 else "Medium" if index <= 7 else "Hard"
                ),
                "front": f"Question {index}?",
                "back": f"Answer {index}.",
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
        original_file_name="flashcards.txt",
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

    material = FlashcardService.get_course_material(
        db_session,
        model_graph.course.id,
    )

    assert material.text == "First chunk\n\nSecond chunk"
    assert material.chunks_used == 2
    assert material.chunks_available == 2
    assert material.truncated is False


def test_build_prompt_inserts_course_material() -> None:
    prompt = FlashcardService.build_prompt("Example lecture material")

    assert "{{TEXT}}" not in prompt
    assert "Example lecture material" in prompt


def test_generate_returns_validated_flashcards(
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
            return _valid_flashcard_payload()

    generation = FlashcardService.generate(
        db_session,
        model_graph.course.id,
        FakeProvider(),
    )

    result = generation.flashcards

    assert result.deck_title == "Example Flashcards"
    assert result.card_count == 10
    assert len(result.flashcards) == 10
    assert result.flashcards[0].card_number == 1
    assert result.flashcards[0].difficulty == "Easy"


def test_generate_rejects_missing_ready_course_material(
    db_session,
    model_graph,
) -> None:
    class FakeProvider:
        def generate_json(self, prompt: str) -> dict[str, object]:
            raise AssertionError("Provider should not be called")

    try:
        FlashcardService.generate(
            db_session,
            model_graph.course.id,
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
        FlashcardService.generate(
            db_session,
            model_graph.course.id,
            FakeProvider(),
        )
    except FlashcardGenerationError as exc:
        assert "Text generation provider failed." in str(exc)
    else:
        raise AssertionError("Expected FlashcardGenerationError")


def test_generate_rejects_invalid_flashcard_structure(
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
                "deck_title": "Invalid Flashcards",
                "card_count": 0,
                "flashcards": [],
            }

    try:
        FlashcardService.generate(
            db_session,
            model_graph.course.id,
            FakeProvider(),
        )
    except FlashcardGenerationError as exc:
        assert "invalid structure" in str(exc)
    else:
        raise AssertionError("Expected FlashcardGenerationError")


def test_save_generated_flashcards_persists_output(
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
            return _valid_flashcard_payload()

    flashcards = FlashcardService.generate(
        db_session,
        model_graph.course.id,
        FakeProvider(),
    )

    settings_json = FlashcardGenerationSettings().model_dump_json()
    context_json = FlashcardGenerationContext.from_material(
        flashcards.material
    ).model_dump_json()

    generated_output = FlashcardService.save_generated_flashcards(
        db_session,
        model_graph.course.id,
        flashcards.flashcards,
        user_id=model_graph.user.id,
        model_used=flashcards.model_used,
        generation_settings=settings_json,
        generation_context=context_json,
    )

    persisted = db_session.scalar(
        select(GeneratedOutput).where(GeneratedOutput.id == generated_output.id)
    )

    assert persisted is not None
    assert persisted.course_id == model_graph.course.id
    assert persisted.output_type == "flashcards"
    assert '"deck_title":"Example Flashcards"' in persisted.content
    assert '"card_count":10' in persisted.content
    assert persisted.generation_settings is not None
    assert persisted.generation_context is not None


def test_flashcard_generation_with_profile_knowledge_opt_in(
    db_session,
    model_graph,
) -> None:
    _add_ready_document(
        db_session,
        model_graph,
        file_hash="1" * 64,
        text="Cellular biology and mitochondria function.",
    )
    db_session.add(
        ProfileKnowledge(
            user_id=model_graph.user.id,
            topic="Cellular Biology Focus",
            detail="Student needs extra practice on ATP synthesis.",
        )
    )
    db_session.commit()

    class PromptCapturingProvider:
        def __init__(self):
            self.prompt = ""

        def generate_json(self, prompt: str) -> dict[str, object]:
            self.prompt = prompt
            return _valid_flashcard_payload()

    # 1. Opt-in True
    provider_opt_in = PromptCapturingProvider()
    generation_opt_in = FlashcardService.generate(
        db_session,
        model_graph.course.id,
        provider_opt_in,
        user_id=model_graph.user.id,
        include_profile_context=True,
    )
    assert "[Supplementary Student Knowledge Profile]" in provider_opt_in.prompt
    assert "Cellular Biology Focus" in provider_opt_in.prompt
    assert "Student needs extra practice on ATP synthesis." in provider_opt_in.prompt
    assert generation_opt_in.profile_knowledge is not None
    assert generation_opt_in.profile_knowledge.items_used == 1

    # 2. Opt-in False
    provider_opt_out = PromptCapturingProvider()
    generation_opt_out = FlashcardService.generate(
        db_session,
        model_graph.course.id,
        provider_opt_out,
        user_id=model_graph.user.id,
        include_profile_context=False,
    )
    assert "[Supplementary Student Knowledge Profile]" not in provider_opt_out.prompt
    assert "Cellular Biology Focus" not in provider_opt_out.prompt
    assert generation_opt_out.profile_knowledge is not None
    assert generation_opt_out.profile_knowledge.is_empty


def test_generate_flashcards_endpoint_returns_generated_flashcards(
    upload_api,
    monkeypatch,
) -> None:
    with upload_api.session_factory() as session:
        user = session.get(User, upload_api.user_id)
        course = session.get(Course, upload_api.course_id)

        assert user is not None
        assert course is not None

        document = UploadedDocument(
            original_file_name="api-flashcards.txt",
            file_type="txt",
            mime_type="text/plain",
            file_size=10,
            file_hash="9" * 64,
            uploader=user,
            course=course,
            storage_provider="local:test",
            storage_key="api-flashcards.txt",
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
                text="API flashcard lecture material",
            )
        )
        session.commit()

    class FakeProvider:
        def generate_json(self, prompt: str) -> dict[str, object]:
            assert "API flashcard lecture material" in prompt
            return _valid_flashcard_payload()

    monkeypatch.setattr(
        flashcard_route,
        "get_text_generation_provider",
        lambda: FakeProvider(),
    )

    response = upload_api.client.post(
        f"/api/courses/{upload_api.course_id}/flashcards",
        json={"include_profile_context": True},
        headers=upload_api.authorization,
    )

    assert response.status_code == 200

    payload = response.json()

    assert payload["success"] is True
    assert payload["message"] == "Flashcards generated successfully"
    assert payload["data"]["flashcards"]["deck_title"] == "Example Flashcards"
    assert payload["data"]["flashcards"]["card_count"] == 10
    assert len(payload["data"]["flashcards"]["flashcards"]) == 10
    assert payload["data"]["context_truncated"] is False
    assert payload["data"]["chunks_used"] == 1
    assert payload["data"]["chunks_available"] == 1
    assert "generated_output_id" in payload["data"]
    assert payload["data"]["generated_output_id"] is not None
    assert payload["data"]["profile_knowledge_used"] is False
