"""End-to-end MVP student journey integration test.

Proves the primary Lumina student journey end-to-end across:
1. User registration & authentication
2. Student-owned course creation & profile knowledge persistence
3. Real document upload through the public API
4. Real document processing worker pipeline (extraction -> chunking -> embedding -> vector indexing)
5. Semantic retrieval and structured study guide generation
6. Persisted conversation reads and continuation
7. Quiz generation, quiz attempt submission, and course progress tracking
8. Cross-user authorization isolation and tenant boundary enforcement
9. Course hard deletion and residual cleanup across DB, vector store, and physical storage
"""

import json
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from backend.app.config import settings
from backend.app.database import get_db
from backend.app.models import (
    EMBEDDING_DIMENSIONS,
    ChunkEmbedding,
    Conversation,
    ConversationMessage,
    Course,
    DocumentChunk,
    DocumentPage,
    GeneratedOutput,
    ProcessingJob,
    ProfileKnowledge,
    Quiz,
    QuizAttempt,
    UploadedDocument,
    User,
)
from main import app
from services.embeddings import EmbeddingProvider
from services.semantic_retrieval import retrieve_course_chunks
from services.text_generation import GenerationMetadata, TextGenerationProvider
from services.vector_store import (
    ChromaVectorStore,
    PgVectorStore,
    VectorStore,
)
import routes.ai_tutor as ai_tutor_route
import routes.course_qa as course_qa_route
import routes.flashcard as flashcard_route
import routes.prompt_generator as prompt_generator_route
import routes.quiz as quiz_route
import routes.study_guide as study_guide_route
import services.course as course_service
import services.document as document_service
import services.document_embedding as doc_embedding_service
import services.embeddings as embeddings_module
import services.processing_jobs as processing_jobs_service
import services.semantic_retrieval as semantic_retrieval_service
import services.text_generation as text_generation_module
import services.vector_store as vector_store_module
from storage.dependencies import get_storage
from storage.local import LocalStorage
from workers.document_processor import process_next_job


class DeterministicEmbeddingProvider(EmbeddingProvider):
    """Deterministic embedding provider generating fixed-dimension unit vectors."""

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        results: list[list[float]] = []
        for text in texts:
            vector = [0.0] * EMBEDDING_DIMENSIONS
            vector[0] = 1.0
            vector[1] = 0.5 if "cell" in text.lower() else 0.2
            results.append(vector)
        return results

    def embed_query(self, text: str) -> list[float]:
        vector = [0.0] * EMBEDDING_DIMENSIONS
        vector[0] = 1.0
        vector[1] = 0.5 if "cell" in text.lower() else 0.2
        return vector


class DeterministicTextGenerationProvider(TextGenerationProvider):
    """Deterministic text generation provider returning schema-conformant structures."""

    MODEL = "stub-deterministic-model-v1"

    def generate_text(self, prompt: str) -> str:
        return "Deterministic generated text response."

    def generate_json(self, prompt: str) -> dict[str, Any]:
        data, _ = self.generate_json_with_metadata(prompt)
        return data

    def generate_text_with_metadata(
        self, prompt: str
    ) -> tuple[str, GenerationMetadata]:
        return self.generate_text(prompt), GenerationMetadata(
            provider="deterministic",
            model=self.MODEL,
            latency_ms=5,
        )

    def generate_json_with_metadata(
        self, prompt: str
    ) -> tuple[dict[str, Any], GenerationMetadata]:
        metadata = GenerationMetadata(
            provider="deterministic",
            model=self.MODEL,
            latency_ms=5,
        )
        prompt_lower = prompt.lower()
        if "study guide" in prompt_lower or "summary format" in prompt_lower:
            guide = {
                "title": "Cell Biology Comprehensive Study Guide",
                "summary": "Covers cellular organization, organelle function, and mitosis.",
                "key_points": [
                    "Cells are the structural units of all living organisms.",
                    "Mitosis produces two genetically identical diploid cells.",
                ],
                "important_terms": [
                    {
                        "term": "Mitosis",
                        "definition": "A process of nuclear division in eukaryotic cells.",
                    },
                    {
                        "term": "Mitochondria",
                        "definition": "Organelles that generate most of the cell's ATP.",
                    },
                ],
                "common_mistakes": [
                    {
                        "mistake": "Confusing mitosis with meiosis",
                        "correction": "Mitosis creates somatic cells, whereas meiosis produces haploid gametes.",
                    }
                ],
                "exam_tips": {
                    "lecture_based": [
                        "Memorize prophase, metaphase, anaphase, and telophase."
                    ],
                    "ai_suggestions": [
                        "Be able to identify mitotic stages under microscopy."
                    ],
                },
                "difficulty": {
                    "level": "Medium",
                    "reason": "Requires understanding of sequential molecular steps.",
                },
                "estimated_study_time": "30 minutes",
                "prerequisites": ["Basic Cell Theory"],
                "learning_objectives": [
                    "Identify phases of the cell cycle",
                    "Explain cellular respiration pathways",
                ],
                "coverage": {
                    "status": "Complete",
                    "estimated_completeness": 95,
                },
                "confidence_notes": "Grounding material was sufficient and clearly structured.",
            }
            return guide, metadata

        # Default to Quiz generation structure
        quiz = {
            "title": "Cell Biology Mastery Quiz",
            "questions": [
                {
                    "question_number": 1,
                    "question_type": "multiple_choice",
                    "difficulty": "medium",
                    "topic": "Cell Division",
                    "question": "Which phase of mitosis involves chromosomes aligning at the cell equator?",
                    "options": ["Prophase", "Metaphase", "Anaphase", "Telophase"],
                    "correct_option_index": 1,
                    "explanation": "During metaphase, chromosomes align at the metaphase plate (equator).",
                },
                {
                    "question_number": 2,
                    "question_type": "true_false",
                    "difficulty": "medium",
                    "topic": "Cellular Energy",
                    "question": "ATP is the primary chemical energy carrier in human cells.",
                    "correct_answer": True,
                    "explanation": "Adenosine triphosphate (ATP) transports chemical energy within cells.",
                },
                {
                    "question_number": 3,
                    "question_type": "short_answer",
                    "difficulty": "medium",
                    "topic": "Cellular Energy",
                    "question": "Which molecule carries chemical energy within cells?",
                    "correct_answer": "ATP",
                    "accepted_answers": ["adenosine triphosphate"],
                    "explanation": "ATP transports chemical energy within cells.",
                },
            ],
        }
        return quiz, metadata


@dataclass(frozen=True)
class JourneyContext:
    client: TestClient
    session_factory: sessionmaker[Session]
    storage: LocalStorage
    storage_root: Path
    vector_store: VectorStore
    embedding_provider: DeterministicEmbeddingProvider
    text_provider: DeterministicTextGenerationProvider


@pytest.fixture
def journey_env(
    tmp_path: Path,
    session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[JourneyContext]:
    storage_root = tmp_path / "uploads"
    storage = LocalStorage(storage_root, chunk_size=1024)

    embedding_provider = DeterministicEmbeddingProvider()
    text_provider = DeterministicTextGenerationProvider()

    if settings.is_hosted:
        vector_store = PgVectorStore()
    else:
        chroma_dir = tmp_path / "chroma"
        vector_store = ChromaVectorStore(persist_directory=str(chroma_dir))

    # Wire overrides
    def override_get_db() -> Iterator[Session]:
        session = session_factory()
        try:
            yield session
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def override_get_storage() -> LocalStorage:
        return storage

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_storage] = override_get_storage

    # Wire provider mocks across all consuming modules
    for module in (
        text_generation_module,
        study_guide_route,
        quiz_route,
        course_qa_route,
        flashcard_route,
        ai_tutor_route,
        prompt_generator_route,
    ):
        monkeypatch.setattr(
            module,
            "get_text_generation_provider",
            lambda *args, **kwargs: text_provider,
        )

    for module in (
        embeddings_module,
        doc_embedding_service,
        semantic_retrieval_service,
    ):
        monkeypatch.setattr(
            module,
            "get_embedding_provider",
            lambda: embedding_provider,
        )

    for module in (
        vector_store_module,
        course_service,
        document_service,
        processing_jobs_service,
        semantic_retrieval_service,
    ):
        monkeypatch.setattr(
            module,
            "get_vector_store",
            lambda: vector_store,
        )

    try:
        with TestClient(app) as client:
            yield JourneyContext(
                client=client,
                session_factory=session_factory,
                storage=storage,
                storage_root=storage_root,
                vector_store=vector_store,
                embedding_provider=embedding_provider,
                text_provider=text_provider,
            )
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_storage, None)


def test_full_mvp_student_journey(journey_env: JourneyContext) -> None:
    """Exercise the complete student journey across all Lumina layers."""
    client = journey_env.client
    session_factory = journey_env.session_factory
    storage = journey_env.storage
    vector_store = journey_env.vector_store
    embedding_provider = journey_env.embedding_provider

    # =========================================================================
    # STAGE 1: User Registration & Authentication (Student 1)
    # =========================================================================
    reg_response = client.post(
        "/api/auth/register",
        json={
            "name": "Student One",
            "email": "student1@example.com",
            "password": "Password123!",
        },
    )
    assert reg_response.status_code == 200, reg_response.text
    assert reg_response.json()["user_email"] == "student1@example.com"

    login_response = client.post(
        "/api/auth/login",
        data={"username": "student1@example.com", "password": "Password123!"},
    )
    assert login_response.status_code == 200, login_response.text
    token1 = login_response.json()["access_token"]
    auth1 = {"Authorization": f"Bearer {token1}"}

    me_response = client.get("/api/auth/me", headers=auth1)
    assert me_response.status_code == 200
    student1_user_id = me_response.json()["id"]
    assert me_response.json()["email"] == "student1@example.com"

    # =========================================================================
    # STAGE 2: Course Creation & Profile Knowledge Persistence
    # =========================================================================
    course_create_res = client.post(
        "/api/courses/",
        headers=auth1,
        json={
            "title": "Cell Biology 101",
            "description": "Introduction to cell structure, energy, and mitosis",
            "semester": "Fall 2026",
            "exam_date": "2026-12-15",
        },
    )
    assert course_create_res.status_code == 201, course_create_res.text
    course_data = course_create_res.json()["data"]
    course_id = course_data["id"]
    assert course_data["owner_id"] == student1_user_id
    assert course_data["is_deleted"] is False

    # Create profile knowledge (which must survive future course deletion)
    pk_create_res = client.post(
        "/api/profile-knowledge/",
        headers=auth1,
        json={
            "topic": "Prior Academic Background",
            "detail": "Completed introductory biochemistry and general chemistry.",
        },
    )
    assert pk_create_res.status_code == 201, pk_create_res.text
    pk_id = pk_create_res.json()["data"]["id"]

    # =========================================================================
    # STAGE 3: Document Upload via API
    # =========================================================================
    document_content = (
        b"Cell Biology Lecture Notes\n\n"
        b"Section 1: Cell Structure and Organelles\n"
        b"Eukaryotic cells contain membrane-bound organelles including the nucleus, "
        b"endoplasmic reticulum, Golgi apparatus, and mitochondria. Mitochondria generate "
        b"adenosine triphosphate (ATP) through oxidative phosphorylation.\n\n"
        b"Section 2: The Cell Cycle and Mitosis\n"
        b"Mitosis is the process of nuclear division consisting of prophase, metaphase, "
        b"anaphase, and telophase. During metaphase, duplicated chromosomes align along "
        b"the metaphase plate before separation in anaphase.\n"
    )

    upload_response = client.post(
        f"/api/courses/{course_id}/documents",
        headers=auth1,
        files={"document": ("biology_notes.txt", document_content, "text/plain")},
    )
    assert upload_response.status_code == 201, upload_response.text
    upload_payload = upload_response.json()
    assert upload_payload["duplicate"] is False
    document_id = UUID(upload_payload["document"]["id"])
    assert upload_payload["document"]["status"] == "uploaded"

    # Confirm initial processing job state in DB and file in storage
    with session_factory() as session:
        doc_row = session.get(UploadedDocument, document_id)
        assert doc_row is not None
        assert doc_row.course_id == course_id
        assert doc_row.status == "uploaded"
        assert storage.exists(doc_row.storage_key)

        job_row = session.scalar(
            select(ProcessingJob).where(ProcessingJob.document_id == document_id)
        )
        assert job_row is not None
        assert job_row.status == "queued"
        assert job_row.id > 0

    # =========================================================================
    # STAGE 4: Real Document Processing Worker Execution
    # =========================================================================
    job_processed = process_next_job(
        session_factory=session_factory,
        storage=storage,
        worker_id="integration-worker-1",
        embedding_provider=embedding_provider,
        vector_store=vector_store,
    )
    assert job_processed is True, "Worker failed to claim and process the queued job"

    # Verify document and job status transitioned to ready / succeeded
    status_response = client.get(
        f"/api/courses/{course_id}/documents/{document_id}",
        headers=auth1,
    )
    assert status_response.status_code == 200, status_response.text
    status_payload = status_response.json()
    assert status_payload["document"]["status"] == "ready"
    assert status_payload["processing_job"]["status"] == "succeeded"
    assert status_payload["processing_job"]["finished_at"] is not None

    # Assert persisted relational and vector artifacts
    with session_factory() as session:
        pages = session.scalars(
            select(DocumentPage)
            .where(DocumentPage.document_id == document_id)
            .order_by(DocumentPage.content_index)
        ).all()
        assert len(pages) >= 1
        assert "Cell Biology Lecture Notes" in pages[0].text

        chunks = session.scalars(
            select(DocumentChunk)
            .where(DocumentChunk.document_id == document_id)
            .order_by(DocumentChunk.chunk_index)
        ).all()
        assert len(chunks) >= 1
        assert all(chunk.course_id == course_id for chunk in chunks)

        if settings.is_hosted:
            chunk_embeddings = session.scalars(
                select(ChunkEmbedding).where(ChunkEmbedding.document_id == document_id)
            ).all()
            assert len(chunk_embeddings) == len(chunks)
            assert all(
                emb.dimensions == EMBEDDING_DIMENSIONS for emb in chunk_embeddings
            )

        vector_count = vector_store.count_course_vectors(session, course_id)
        assert vector_count == len(chunks)

    # =========================================================================
    # STAGE 5: Semantic Retrieval & Structured Study Guide Generation
    # =========================================================================
    # 5a. Direct Semantic Retrieval Verification
    with session_factory() as session:
        retrieved_chunks = retrieve_course_chunks(
            session,
            course_id=course_id,
            query="mitosis cell division",
            limit=3,
            provider=embedding_provider,
            store=vector_store,
        )
        assert len(retrieved_chunks) >= 1
        assert retrieved_chunks[0].course_id == course_id
        assert retrieved_chunks[0].similarity > 0.0
        assert "mitosis" in retrieved_chunks[0].text.lower()

    # 5b. Study Guide Generation through API
    study_guide_res = client.post(
        f"/api/courses/{course_id}/study-guide",
        headers=auth1,
        json={
            "summary_format": "comprehensive",
            "topic_focus": "Mitosis and Cell Division",
            "summary_length": "long",
            "detail_level": "detailed",
            "summary_mode": "exam_focused",
        },
    )
    assert study_guide_res.status_code == 200, study_guide_res.text
    guide_payload = study_guide_res.json()["data"]
    assert (
        guide_payload["study_guide"]["title"]
        == "Cell Biology Comprehensive Study Guide"
    )
    assert len(guide_payload["study_guide"]["key_points"]) >= 2
    assert len(guide_payload["study_guide"]["important_terms"]) >= 2

    # Verify GeneratedOutput persistence in DB
    with session_factory() as session:
        generated_row = session.scalar(
            select(GeneratedOutput).where(GeneratedOutput.course_id == course_id)
        )
        assert generated_row is not None
        assert generated_row.user_id == student1_user_id
        assert (
            generated_row.model_used
            == f"deterministic:{DeterministicTextGenerationProvider.MODEL}"
        )
        assert generated_row.output_type == "study_guide"
        assert guide_payload["generated_output_id"] == generated_row.id

        stored_settings = json.loads(generated_row.generation_settings)
        assert stored_settings["summary_length"] == "long"
        assert stored_settings["detail_level"] == "detailed"
        assert stored_settings["summary_mode"] == "exam_focused"

        stored_context = json.loads(generated_row.generation_context)
        assert stored_context["chunks_used"] >= 1
        assert stored_context["highest_similarity"] > 0.0

    # 5c. The stored guide is readable again without any further generation
    history_res = client.get(
        f"/api/courses/{course_id}/generated-outputs", headers=auth1
    )
    assert history_res.status_code == 200, history_res.text
    history = history_res.json()["data"]
    assert [entry["id"] for entry in history] == [guide_payload["generated_output_id"]]

    detail_res = client.get(
        f"/api/courses/{course_id}/generated-outputs/"
        f"{guide_payload['generated_output_id']}",
        headers=auth1,
    )
    assert detail_res.status_code == 200, detail_res.text
    assert detail_res.json()["data"]["content"] == guide_payload["study_guide"]

    # =========================================================================
    # STAGE 6: Persisted Conversation Read & Resume
    # =========================================================================
    first_qa = client.post(
        f"/api/courses/{course_id}/qa",
        headers=auth1,
        json={"question": "What does mitosis produce?"},
    )
    assert first_qa.status_code == 200, first_qa.text
    conversation_id = first_qa.json()["data"]["conversation_id"]

    conversation_list = client.get(
        f"/api/courses/{course_id}/conversations", headers=auth1
    )
    assert conversation_list.status_code == 200, conversation_list.text
    assert conversation_list.json()["data"] == [
        {
            "id": conversation_id,
            "course_id": course_id,
            "user_id": student1_user_id,
            "conversation_type": "course_qa",
            "preview": "What does mitosis produce?",
            "message_count": 2,
            "created_at": conversation_list.json()["data"][0]["created_at"],
            "updated_at": conversation_list.json()["data"][0]["updated_at"],
        }
    ]

    first_detail = client.get(
        f"/api/courses/{course_id}/conversations/{conversation_id}", headers=auth1
    )
    assert first_detail.status_code == 200, first_detail.text
    assert [
        (message["role"], message["content"])
        for message in first_detail.json()["data"]["messages"]
    ] == [
        ("user", "What does mitosis produce?"),
        ("assistant", "Deterministic generated text response."),
    ]

    resumed_qa = client.post(
        f"/api/courses/{course_id}/qa",
        headers=auth1,
        json={
            "question": "Can you restate that?",
            "conversation_id": conversation_id,
        },
    )
    assert resumed_qa.status_code == 200, resumed_qa.text
    assert resumed_qa.json()["data"]["conversation_id"] == conversation_id

    resumed_detail = client.get(
        f"/api/courses/{course_id}/conversations/{conversation_id}", headers=auth1
    )
    assert resumed_detail.status_code == 200, resumed_detail.text
    resumed_messages = resumed_detail.json()["data"]["messages"]
    assert [message["role"] for message in resumed_messages] == [
        "user",
        "assistant",
        "user",
        "assistant",
    ]
    assert resumed_messages[2]["content"] == "Can you restate that?"

    # =========================================================================
    # STAGE 7: Quiz Generation, Attempt Submission & Course Progress
    # =========================================================================
    # 7a. Generate Quiz
    quiz_res = client.post(
        f"/api/courses/{course_id}/quiz",
        headers=auth1,
        json={
            "question_count": 3,
            "question_types": ["multiple_choice", "true_false", "short_answer"],
            "difficulty": "medium",
            "topic_focus": "Mitosis & Cellular Energy",
        },
    )
    assert quiz_res.status_code == 200, quiz_res.text
    quiz_payload = quiz_res.json()["data"]["quiz"]
    quiz_id = quiz_payload["quiz_id"]
    assert quiz_payload["title"] == "Cell Biology Mastery Quiz"
    assert len(quiz_payload["questions"]) == 3
    q1 = quiz_payload["questions"][0]
    q2 = quiz_payload["questions"][1]
    q3 = quiz_payload["questions"][2]
    assert [q["question_type"] for q in quiz_payload["questions"]] == [
        "multiple_choice",
        "true_false",
        "short_answer",
    ]

    listed = client.get(f"/api/courses/{course_id}/quizzes", headers=auth1)
    assert listed.status_code == 200, listed.text
    assert [row["quiz_id"] for row in listed.json()["data"]] == [quiz_id]
    assert listed.json()["data"][0]["question_count"] == 3

    fetched = client.get(f"/api/courses/{course_id}/quizzes/{quiz_id}", headers=auth1)
    assert fetched.status_code == 200, fetched.text
    assert [q["question_number"] for q in fetched.json()["data"]["questions"]] == [
        1,
        2,
        3,
    ]

    # 7b. Submit Quiz Attempt
    attempt_res = client.post(
        f"/api/courses/{course_id}/quizzes/{quiz_id}/attempts",
        headers=auth1,
        json={
            "answers": [
                {
                    "question_id": q1["question_id"],
                    "selected_option_index": q1["correct_option_index"],
                },
                {
                    "question_id": q2["question_id"],
                    "selected_option_index": q2["correct_option_index"],
                },
                {
                    "question_id": q3["question_id"],
                    "text_response": "adenosine triphosphate",
                },
            ],
            "time_spent_seconds": 65,
        },
    )
    assert attempt_res.status_code == 201, attempt_res.text
    attempt_data = attempt_res.json()["data"]
    assert attempt_data["score"] == 1.0
    assert attempt_data["correct_count"] == 3
    assert attempt_data["graded_count"] == 3
    assert attempt_data["total_questions"] == 3

    # 7c. Verify Course Progress
    progress_res = client.get(f"/api/courses/{course_id}/progress", headers=auth1)
    assert progress_res.status_code == 200, progress_res.text
    progress_data = progress_res.json()["data"]
    assert progress_data["attempts_count"] == 1
    assert progress_data["average_score"] == 1.0
    assert len(progress_data["topic_mastery"]) >= 1

    # =========================================================================
    # STAGE 8: Cross-User Authorization Isolation
    # =========================================================================
    reg2_res = client.post(
        "/api/auth/register",
        json={
            "name": "Student Two",
            "email": "student2@example.com",
            "password": "Password123!",
        },
    )
    assert reg2_res.status_code == 200
    login2_res = client.post(
        "/api/auth/login",
        data={"username": "student2@example.com", "password": "Password123!"},
    )
    assert login2_res.status_code == 200
    token2 = login2_res.json()["access_token"]
    auth2 = {"Authorization": f"Bearer {token2}"}

    # Student 2 creates their own distinct course
    s2_course_res = client.post(
        "/api/courses/",
        headers=auth2,
        json={"title": "Chemistry 101"},
    )
    assert s2_course_res.status_code == 201
    s2_course_id = s2_course_res.json()["data"]["id"]

    # Student 2 must receive 404 for all of Student 1's resources
    assert client.get(f"/api/courses/{course_id}", headers=auth2).status_code == 404
    assert (
        client.get(f"/api/courses/{course_id}/documents", headers=auth2).status_code
        == 404
    )
    assert (
        client.get(
            f"/api/courses/{course_id}/documents/{document_id}", headers=auth2
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/api/courses/{course_id}/study-guide",
            headers=auth2,
            json={"summary_format": "overview", "topic_focus": "Mitosis"},
        ).status_code
        == 404
    )
    assert (
        client.get(
            f"/api/courses/{course_id}/generated-outputs", headers=auth2
        ).status_code
        == 404
    )
    assert (
        client.get(
            f"/api/courses/{course_id}/generated-outputs/"
            f"{guide_payload['generated_output_id']}",
            headers=auth2,
        ).status_code
        == 404
    )
    assert (
        client.get(f"/api/courses/{course_id}/conversations", headers=auth2).status_code
        == 404
    )
    assert (
        client.get(
            f"/api/courses/{course_id}/conversations/{conversation_id}", headers=auth2
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/api/courses/{course_id}/quiz",
            headers=auth2,
            json={
                "question_count": 2,
                "question_types": ["multiple_choice"],
                "difficulty": "easy",
                "topic_focus": "Cells",
            },
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/api/courses/{course_id}/quizzes/{quiz_id}/attempts",
            headers=auth2,
            json={
                "answers": [
                    {"question_id": q1["question_id"], "selected_option_index": 0}
                ]
            },
        ).status_code
        == 404
    )
    assert (
        client.get(f"/api/courses/{course_id}/quizzes", headers=auth2).status_code
        == 404
    )
    assert (
        client.get(
            f"/api/courses/{course_id}/quizzes/{quiz_id}", headers=auth2
        ).status_code
        == 404
    )
    assert (
        client.get(f"/api/courses/{course_id}/progress", headers=auth2).status_code
        == 404
    )
    assert client.delete(f"/api/courses/{course_id}", headers=auth2).status_code == 404

    # Semantic retrieval on Student 2's course returns 0 results
    with session_factory() as session:
        s2_retrieval = retrieve_course_chunks(
            session,
            course_id=s2_course_id,
            query="mitosis cell division",
            limit=5,
            provider=embedding_provider,
            store=vector_store,
        )
        assert len(s2_retrieval) == 0

    # =========================================================================
    # STAGE 9: Course Hard Deletion & Residue Verification
    # =========================================================================
    delete_res = client.delete(
        f"/api/courses/{course_id}?hard_delete=true",
        headers=auth1,
    )
    assert delete_res.status_code == 200, delete_res.text

    # 9a. Verify Course and related metadata are gone
    assert client.get(f"/api/courses/{course_id}", headers=auth1).status_code == 404

    with session_factory() as session:
        assert session.get(Course, course_id) is None
        assert (
            session.scalar(
                select(func.count())
                .select_from(UploadedDocument)
                .where(UploadedDocument.course_id == course_id)
            )
            == 0
        )
        assert session.get(Conversation, conversation_id) is None
        assert (
            session.scalar(
                select(func.count())
                .select_from(ConversationMessage)
                .where(ConversationMessage.conversation_id == conversation_id)
            )
            == 0
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(DocumentPage)
                .where(DocumentPage.course_id == course_id)
            )
            == 0
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(DocumentChunk)
                .where(DocumentChunk.course_id == course_id)
            )
            == 0
        )
        if settings.is_hosted:
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(ChunkEmbedding)
                    .where(ChunkEmbedding.course_id == course_id)
                )
                == 0
            )
        assert (
            session.scalar(
                select(func.count())
                .select_from(ProcessingJob)
                .where(ProcessingJob.course_id == course_id)
            )
            == 0
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(GeneratedOutput)
                .where(GeneratedOutput.course_id == course_id)
            )
            == 0
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(Quiz)
                .where(Quiz.course_id == course_id)
            )
            == 0
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(QuizAttempt)
                .where(QuizAttempt.quiz_id == quiz_id)
            )
            == 0
        )

        # Vector store returns 0 records for deleted course
        assert vector_store.count_course_vectors(session, course_id) == 0

    # 9b. Verify physical storage file is deleted
    assert storage.exists(doc_row.storage_key) is False

    # 9c. Verify User and ProfileKnowledge survived
    with session_factory() as session:
        user1 = session.get(User, student1_user_id)
        assert user1 is not None
        assert user1.email == "student1@example.com"

        pk_item = session.get(ProfileKnowledge, pk_id)
        assert pk_item is not None
        assert pk_item.user_id == student1_user_id

    # Verify ProfileKnowledge is still accessible via API for Student 1
    pk_check_res = client.get(f"/api/profile-knowledge/{pk_id}", headers=auth1)
    assert pk_check_res.status_code == 200
    assert pk_check_res.json()["data"]["topic"] == "Prior Academic Background"
