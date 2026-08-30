import json

import pytest
from sqlalchemy import select

import routes.flashcard as flashcard_route
from backend.app.models import (
    JOB_TYPE_GENERATE_FLASHCARD,
    Course,
    DocumentChunk,
    GeneratedOutput,
    GenerationJob,
    ProfileKnowledge,
    UploadedDocument,
)
from schemas.flashcard import (
    FlashcardGenerationContext,
    FlashcardGenerationSettings,
    FlashcardRequest,
)
from services.embeddings import EmbeddingConnectionError
from services.flashcard import (
    FlashcardGenerationError,
    FlashcardService,
    InvalidFlashcardStructureError,
    NoReadyCourseMaterialError,
)
from services.retrieval_material import (
    MaterialNotIndexedError,
    NoRelevantMaterialError,
)
from services.text_generation import (
    GenerationMetadata,
    TextGenerationError,
)
from utils.ai_errors import AiErrorCode

from schemas.prompt_context import EducationLevel, MaterialKind, PromptContext

PROMPT_CONTEXT = PromptContext(
    education_level=EducationLevel.HIGH_SCHOOL,
    course_title="AP Biology",
    subject_area="Biology",
    material_kind=MaterialKind.TEXTBOOK,
)

STUB_METADATA = GenerationMetadata(provider="ollama", model="qwen3:8b", latency_ms=5)
IRRELEVANT_SEED = 4.0


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


class CountingProvider:
    def __init__(self, result=None, error=None):
        self._result = result if result is not None else _valid_flashcard_payload()
        self._error = error
        self.calls = 0
        self.prompt = ""

    def generate_json_with_metadata(self, prompt: str):
        self.calls += 1
        self.prompt = prompt
        if self._error is not None:
            raise self._error
        return self._result, STUB_METADATA

    def generate_json(self, prompt: str):
        self.calls += 1
        self.prompt = prompt
        if self._error is not None:
            raise self._error
        return self._result


def _install_provider(monkeypatch, provider: CountingProvider) -> CountingProvider:
    monkeypatch.setattr(
        flashcard_route,
        "get_text_generation_provider",
        lambda *args, **kwargs: provider,
    )
    return provider


def _ascending_seeds(count: int) -> list[float]:
    return [index * 0.1 for index in range(count)]


def _add_ready_material(
    session,
    course_id: int,
    texts: list[str],
    *,
    file_hash: str,
    retrieval_env,
    seeds: list[float] | None = None,
) -> UploadedDocument:
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
    chunks = [
        DocumentChunk(
            document=document,
            course=course,
            chunk_index=index,
            page_number=None,
            text=text,
        )
        for index, text in enumerate(texts)
    ]
    session.add_all(chunks)
    session.flush()
    retrieval_env.index(
        session,
        document,
        chunks,
        seeds=seeds if seeds is not None else _ascending_seeds(len(chunks)),
    )
    session.commit()
    return document


# ---------------------------------------------------------------------------
# Retrieval Query Construction
# ---------------------------------------------------------------------------


def test_retrieval_query_uses_the_topic_focus_verbatim(model_graph) -> None:
    query = FlashcardService.build_retrieval_query(
        model_graph.course, FlashcardRequest(topic_focus="Dynamic Programming")
    )
    assert query == "Dynamic Programming"


def test_retrieval_query_for_all_topics_describes_the_course(model_graph) -> None:
    model_graph.course.title = "Operating Systems"
    model_graph.course.description = "Kernel architectures and memory management"
    query = FlashcardService.build_retrieval_query(
        model_graph.course, FlashcardRequest(topic_focus="All Topics")
    )
    assert "Operating Systems" in query
    assert "Kernel architectures and memory management" in query


# ---------------------------------------------------------------------------
# Material Selection with Semantic Retrieval
# ---------------------------------------------------------------------------


def test_get_course_material_uses_semantic_retrieval(
    db_session,
    model_graph,
    retrieval_env,
) -> None:
    _add_ready_material(
        db_session,
        model_graph.course.id,
        ["First relevant chunk", "Second relevant chunk"],
        file_hash="a" * 64,
        retrieval_env=retrieval_env,
    )

    material = FlashcardService.get_course_material(
        db_session,
        model_graph.course.id,
        query="First relevant chunk",
    )

    assert "First relevant chunk" in material.text
    assert material.chunks_used == 2
    assert material.chunks_available == 2
    assert material.truncated is False
    assert material.lowest_similarity is not None
    assert material.highest_similarity is not None


def test_build_prompt_inserts_course_material_and_topic_focus() -> None:
    prompt = FlashcardService.build_prompt(
        "Example lecture material",
        topic_focus="Tree Traversal",
        context=PROMPT_CONTEXT,
    )

    assert "{{TEXT}}" not in prompt
    assert "{{TOPIC_FOCUS}}" not in prompt
    assert "Example lecture material" in prompt
    assert "Tree Traversal" in prompt


# ---------------------------------------------------------------------------
# Generation Logic & Error Handling
# ---------------------------------------------------------------------------


def test_generate_returns_validated_flashcards(
    db_session,
    model_graph,
    retrieval_env,
) -> None:
    _add_ready_material(
        db_session,
        model_graph.course.id,
        ["Example lecture material"],
        file_hash="c" * 64,
        retrieval_env=retrieval_env,
    )

    provider = CountingProvider()
    generation = FlashcardService.generate(
        db_session,
        model_graph.course.id,
        provider,
        request=FlashcardRequest(topic_focus="All Topics"),
    )

    result = generation.flashcards

    assert result.deck_title == "Example Flashcards"
    assert result.card_count == 10
    assert len(result.flashcards) == 10
    assert result.flashcards[0].card_number == 1
    assert result.flashcards[0].difficulty == "Easy"
    assert "Example lecture material" in provider.prompt
    assert "All Topics" in provider.prompt


def test_generate_rejects_missing_ready_course_material(
    db_session,
    model_graph,
) -> None:
    provider = CountingProvider()

    with pytest.raises(NoReadyCourseMaterialError) as exc_info:
        FlashcardService.generate(
            db_session,
            model_graph.course.id,
            provider,
        )
    assert "No processed course material" in str(exc_info.value)
    assert provider.calls == 0


def test_generate_handles_material_not_indexed_error(
    db_session,
    model_graph,
    retrieval_env,
) -> None:
    """A course with chunks but no vectors raises MaterialNotIndexedError."""
    document = UploadedDocument(
        original_file_name="unindexed.txt",
        file_type="txt",
        mime_type="text/plain",
        file_size=10,
        file_hash="u" * 64,
        uploader=model_graph.user,
        course=model_graph.course,
        storage_provider="local:test",
        storage_key="unindexed.txt",
        status="ready",
    )
    db_session.add(document)
    db_session.flush()
    db_session.add(
        DocumentChunk(
            document=document,
            course=model_graph.course,
            chunk_index=0,
            page_number=None,
            text="Unindexed chunk text",
        )
    )
    db_session.commit()

    provider = CountingProvider()

    with pytest.raises(MaterialNotIndexedError):
        FlashcardService.generate(
            db_session,
            model_graph.course.id,
            provider,
        )
    assert provider.calls == 0


def test_generate_handles_no_relevant_material_error(
    db_session,
    model_graph,
    retrieval_env,
) -> None:
    _add_ready_material(
        db_session,
        model_graph.course.id,
        ["Irrelevant chunk text"],
        file_hash="i" * 64,
        retrieval_env=retrieval_env,
        seeds=[IRRELEVANT_SEED],
    )

    provider = CountingProvider()

    with pytest.raises(NoRelevantMaterialError):
        FlashcardService.generate(
            db_session,
            model_graph.course.id,
            provider,
            request=FlashcardRequest(topic_focus="Irrelevant query"),
        )
    assert provider.calls == 0


def test_generate_wraps_text_generation_error(
    db_session,
    model_graph,
    retrieval_env,
) -> None:
    _add_ready_material(
        db_session,
        model_graph.course.id,
        ["Example lecture material"],
        file_hash="d" * 64,
        retrieval_env=retrieval_env,
    )

    provider = CountingProvider(error=TextGenerationError("Provider failed"))

    with pytest.raises(FlashcardGenerationError) as exc_info:
        FlashcardService.generate(
            db_session,
            model_graph.course.id,
            provider,
        )
    assert "Text generation provider failed." in str(exc_info.value)


def test_generate_rejects_invalid_flashcard_structure(
    db_session,
    model_graph,
    retrieval_env,
) -> None:
    _add_ready_material(
        db_session,
        model_graph.course.id,
        ["Example lecture material"],
        file_hash="e" * 64,
        retrieval_env=retrieval_env,
    )

    provider = CountingProvider(
        result={
            "deck_title": "Invalid Flashcards",
            "card_count": 0,
            "flashcards": [],
        }
    )

    with pytest.raises(InvalidFlashcardStructureError):
        FlashcardService.generate(
            db_session,
            model_graph.course.id,
            provider,
        )


# ---------------------------------------------------------------------------
# Output Persistence & Attribution
# ---------------------------------------------------------------------------


def test_save_generated_flashcards_persists_output(
    db_session,
    model_graph,
    retrieval_env,
) -> None:
    _add_ready_material(
        db_session,
        model_graph.course.id,
        ["Persisted lecture material"],
        file_hash="f" * 64,
        retrieval_env=retrieval_env,
    )

    provider = CountingProvider()
    flashcards = FlashcardService.generate(
        db_session,
        model_graph.course.id,
        provider,
        request=FlashcardRequest(topic_focus="Recursion"),
    )

    settings_json = FlashcardGenerationSettings.from_request(
        flashcards.effective_request,
        retrieval_limit=24,
        retrieval_min_similarity=0.25,
    ).model_dump_json()
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
    assert persisted.user_id == model_graph.user.id
    assert persisted.model_used == flashcards.model_used
    assert persisted.output_type == "flashcards"
    assert '"deck_title":"Example Flashcards"' in persisted.content
    assert '"card_count":10' in persisted.content
    assert persisted.generation_settings is not None
    assert persisted.generation_context is not None

    settings_dict = json.loads(persisted.generation_settings)
    assert settings_dict["version"] == 1
    assert settings_dict["output_type"] == "flashcards"
    assert settings_dict["topic_focus"] == "Recursion"
    assert settings_dict["use_profile_knowledge"] is False
    assert settings_dict["retrieval_limit"] == 24
    assert settings_dict["retrieval_min_similarity"] == 0.25

    context_dict = json.loads(persisted.generation_context)
    assert context_dict["version"] == 1
    assert context_dict["chunks_used"] == 1
    assert context_dict["chunks_available"] == 1
    assert context_dict["truncated"] is False
    assert "lowest_similarity" in context_dict
    assert "highest_similarity" in context_dict


def test_flashcard_generation_with_profile_knowledge_opt_in(
    db_session,
    model_graph,
    retrieval_env,
) -> None:
    _add_ready_material(
        db_session,
        model_graph.course.id,
        ["Cellular biology and mitochondria function."],
        file_hash="1" * 64,
        retrieval_env=retrieval_env,
    )
    db_session.add(
        ProfileKnowledge(
            user_id=model_graph.user.id,
            topic="Cellular Biology Focus",
            detail="Student needs extra practice on ATP synthesis.",
        )
    )
    db_session.commit()

    # 1. Opt-in True
    provider_opt_in = CountingProvider()
    generation_opt_in = FlashcardService.generate(
        db_session,
        model_graph.course.id,
        provider_opt_in,
        user_id=model_graph.user.id,
        include_profile_context=True,
    )
    assert "SUPPLEMENTARY PROFILE CONTEXT" in provider_opt_in.prompt
    assert "Cellular Biology Focus" in provider_opt_in.prompt
    assert "Student needs extra practice on ATP synthesis." in provider_opt_in.prompt
    assert generation_opt_in.profile_knowledge is not None
    assert generation_opt_in.profile_knowledge.items_used == 1

    # 2. Opt-in False
    provider_opt_out = CountingProvider()
    generation_opt_out = FlashcardService.generate(
        db_session,
        model_graph.course.id,
        provider_opt_out,
        user_id=model_graph.user.id,
        include_profile_context=False,
    )
    assert "SUPPLEMENTARY PROFILE CONTEXT" not in provider_opt_out.prompt
    assert "Cellular Biology Focus" not in provider_opt_out.prompt
    assert generation_opt_out.profile_knowledge is not None
    assert generation_opt_out.profile_knowledge.is_empty


# ---------------------------------------------------------------------------
# API Endpoint Tests
# ---------------------------------------------------------------------------


def test_generate_flashcards_endpoint_returns_generated_flashcards(
    upload_api,
    retrieval_env,
    monkeypatch,
) -> None:
    with upload_api.session_factory() as session:
        _add_ready_material(
            session,
            upload_api.course_id,
            ["API flashcard lecture material"],
            file_hash="9" * 64,
            retrieval_env=retrieval_env,
        )

    provider = _install_provider(monkeypatch, CountingProvider())

    response = upload_api.client.post(
        f"/api/courses/{upload_api.course_id}/flashcards",
        json={"topic_focus": "Memory Hierarchy", "include_profile_context": True},
        headers=upload_api.authorization,
    )

    assert response.status_code == 200, response.text

    payload = response.json()

    assert payload["success"] is True
    assert payload["message"] == "Flashcards generated successfully"
    assert payload["data"]["flashcards"]["deck_title"] == "Example Flashcards"
    assert payload["data"]["flashcards"]["card_count"] == 10
    assert len(payload["data"]["flashcards"]["flashcards"]) == 10
    assert payload["data"]["context_truncated"] is False
    assert payload["data"]["retrieval_narrowed"] is False
    assert payload["data"]["lowest_similarity"] is not None
    assert payload["data"]["highest_similarity"] is not None
    assert payload["data"]["chunks_used"] == 1
    assert payload["data"]["chunks_available"] == 1
    assert payload["data"]["generated_output_id"] is not None
    assert payload["data"]["profile_knowledge_used"] is False

    assert "Memory Hierarchy" in provider.prompt

    output_id = payload["data"]["generated_output_id"]
    detail_res = upload_api.client.get(
        f"/api/courses/{upload_api.course_id}/generated-outputs/{output_id}",
        headers=upload_api.authorization,
    )
    assert detail_res.status_code == 200
    detail = detail_res.json()["data"]
    assert detail["id"] == output_id
    assert detail["output_type"] == "flashcards"
    assert detail["user_id"] == upload_api.user_id
    assert detail["generation_settings"]["version"] == 1
    assert detail["generation_settings"]["output_type"] == "flashcards"
    assert detail["generation_settings"]["topic_focus"] == "Memory Hierarchy"
    assert detail["generation_settings"]["use_profile_knowledge"] is True
    assert detail["generation_context"]["version"] == 1
    assert detail["generation_context"]["chunks_used"] == 1
    assert detail["generation_context"]["chunks_available"] == 1
    assert detail["content"]["deck_title"] == "Example Flashcards"


def test_generate_flashcards_endpoint_unindexed_material_returns_409(
    upload_api,
    retrieval_env,
    monkeypatch,
) -> None:
    with upload_api.session_factory() as session:
        course = session.get(Course, upload_api.course_id)
        doc = UploadedDocument(
            original_file_name="unindexed.txt",
            file_type="txt",
            mime_type="text/plain",
            file_size=10,
            file_hash="8" * 64,
            user_id=upload_api.user_id,
            course=course,
            storage_provider="local:test",
            storage_key="unindexed.txt",
            status="ready",
        )
        session.add(doc)
        session.flush()
        session.add(
            DocumentChunk(
                document=doc,
                course=course,
                chunk_index=0,
                page_number=None,
                text="Some text",
            )
        )
        session.commit()

    provider = _install_provider(monkeypatch, CountingProvider())

    response = upload_api.client.post(
        f"/api/courses/{upload_api.course_id}/flashcards",
        json={"topic_focus": "All Topics"},
        headers=upload_api.authorization,
    )

    assert response.status_code == 409
    assert response.headers.get("X-Error-Code") == AiErrorCode.MATERIAL_NOT_INDEXED
    assert provider.calls == 0


def test_generate_flashcards_endpoint_no_relevant_material_returns_409(
    upload_api,
    retrieval_env,
    monkeypatch,
) -> None:
    with upload_api.session_factory() as session:
        _add_ready_material(
            session,
            upload_api.course_id,
            ["Completely unrelated material"],
            file_hash="7" * 64,
            retrieval_env=retrieval_env,
            seeds=[IRRELEVANT_SEED],
        )

    provider = _install_provider(monkeypatch, CountingProvider())

    response = upload_api.client.post(
        f"/api/courses/{upload_api.course_id}/flashcards",
        json={"topic_focus": "Quantum Physics"},
        headers=upload_api.authorization,
    )

    assert response.status_code == 409
    assert response.headers.get("X-Error-Code") == AiErrorCode.NO_RELEVANT_MATERIAL
    assert provider.calls == 0


def test_generate_flashcards_endpoint_retrieval_error_returns_503(
    upload_api,
    retrieval_env,
    monkeypatch,
) -> None:
    with upload_api.session_factory() as session:
        _add_ready_material(
            session,
            upload_api.course_id,
            ["Some indexed material"],
            file_hash="6" * 64,
            retrieval_env=retrieval_env,
        )

    import services.retrieval_material as retrieval_material_service

    def fail_retrieve(*args, **kwargs):
        raise EmbeddingConnectionError("Embedding provider is down")

    monkeypatch.setattr(
        retrieval_material_service, "retrieve_course_chunks", fail_retrieve
    )

    provider = _install_provider(monkeypatch, CountingProvider())

    response = upload_api.client.post(
        f"/api/courses/{upload_api.course_id}/flashcards",
        json={"topic_focus": "Some topic"},
        headers=upload_api.authorization,
    )

    assert response.status_code == 503
    assert response.headers.get("X-Error-Code") == AiErrorCode.RETRIEVAL_UNAVAILABLE
    assert provider.calls == 0


def test_generate_flashcards_rejects_unavailable_model(
    upload_api,
) -> None:
    with upload_api.session_factory() as session:
        course = session.get(Course, upload_api.course_id)
        document = UploadedDocument(
            original_file_name="lecture.txt",
            file_type="txt",
            mime_type="text/plain",
            file_size=10,
            file_hash="8" * 64,
            user_id=upload_api.user_id,
            course=course,
            storage_provider="local:test",
            storage_key="lecture.txt",
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
                text="Flashcard content",
            )
        )
        session.commit()

    from utils.ai_errors import ERROR_CODE_HEADER, PUBLIC_MESSAGES, AiErrorCode

    response = upload_api.client.post(
        f"/api/courses/{upload_api.course_id}/flashcards",
        json={"model": "nonexistent:model"},
        headers=upload_api.authorization,
    )

    assert response.status_code == 400
    assert (
        response.headers.get(ERROR_CODE_HEADER) == AiErrorCode.UNAVAILABLE_MODEL.value
    )
    assert response.json()["detail"] == PUBLIC_MESSAGES[AiErrorCode.UNAVAILABLE_MODEL]


def test_generate_flashcards_rejects_json_incompatible_model(
    upload_api, monkeypatch
) -> None:
    with upload_api.session_factory() as session:
        course = session.get(Course, upload_api.course_id)
        document = UploadedDocument(
            original_file_name="lecture.txt",
            file_type="txt",
            mime_type="text/plain",
            file_size=10,
            file_hash="7" * 64,
            user_id=upload_api.user_id,
            course=course,
            storage_provider="local:test",
            storage_key="lecture2.txt",
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
                text="Flashcard content",
            )
        )
        session.commit()

    from types import SimpleNamespace
    import services.text_generation as text_gen
    from utils.ai_errors import ERROR_CODE_HEADER, PUBLIC_MESSAGES, AiErrorCode

    fake_settings = SimpleNamespace(
        ai_available_vendors=("ollama",),
        ai_default_model="ollama:text-only",
        ai_model_catalog={
            "ollama": [
                {
                    "model": "text-only",
                    "json_mode": False,
                    "context_window": 8192,
                    "vision": False,
                }
            ]
        },
    )
    monkeypatch.setattr(text_gen, "settings", fake_settings)

    response = upload_api.client.post(
        f"/api/courses/{upload_api.course_id}/flashcards",
        json={"model": "ollama:text-only"},
        headers=upload_api.authorization,
    )

    assert response.status_code == 400
    assert (
        response.headers.get(ERROR_CODE_HEADER) == AiErrorCode.INCOMPATIBLE_MODEL.value
    )
    assert response.json()["detail"] == PUBLIC_MESSAGES[AiErrorCode.INCOMPATIBLE_MODEL]


def test_enqueue_flashcards_endpoint_queues_a_background_job(upload_api) -> None:
    response = upload_api.client.post(
        f"/api/courses/{upload_api.course_id}/flashcards/jobs",
        json={"topic_focus": "Memory Hierarchy", "include_profile_context": True},
        headers=upload_api.authorization,
    )

    assert response.status_code == 202, response.text

    payload = response.json()
    assert payload["success"] is True
    assert payload["data"]["status"] == "queued"

    with upload_api.session_factory() as session:
        job = session.get(GenerationJob, payload["data"]["job_id"])
        assert job is not None
        assert job.job_type == JOB_TYPE_GENERATE_FLASHCARD
        assert job.course_id == upload_api.course_id
        assert job.user_id == upload_api.user_id

        # The queued payload carries the model the request resolved to, because
        # the worker runs without the student's preferences in hand.
        queued_request = json.loads(job.request_payload)
        assert queued_request["topic_focus"] == "Memory Hierarchy"
        assert queued_request["use_profile_knowledge"] is True
        assert queued_request["model"]
