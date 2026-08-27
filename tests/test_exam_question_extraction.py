"""The upload worker reads a past exam paper, and never fails an upload doing it."""

import hashlib
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from backend.app.models import (
    EMBEDDING_DIMENSIONS,
    JOB_STATUS_SUCCEEDED,
    Course,
    PastExamQuestion,
    ProcessingJob,
    Role,
    UploadedDocument,
    User,
)
from services.processing_jobs import enqueue_document_job
from services.text_generation import GenerationMetadata, TextGenerationError
from services.vector_store import PgVectorStore
from storage.local import LocalStorage
from workers.document_processor import process_next_job

pytestmark = pytest.mark.database_contract

STUB_METADATA = GenerationMetadata(provider="ollama", model="qwen3:8b", latency_ms=4)

PAPER = (
    b"Question 1. Define a graph and give two representations.\n"
    b"Question 2. Prove the handshake lemma.\n"
)


def extraction_payload(*questions) -> dict:
    return {
        "questions": list(questions)
        or [
            {
                "question_label": "Q1",
                "question_number": 1,
                "question_text": "Define a graph and give two representations.",
                "question_type": "short_answer",
                "topics": ["Graph Representations"],
                "citations": ["S1"],
            }
        ],
        "confidence_notes": "",
    }


class RecordingProvider:
    def __init__(self, result: dict | None = None, error: Exception | None = None):
        self._result = result if result is not None else extraction_payload()
        self._error = error
        self.calls = 0
        self.prompt = ""

    def generate_json_with_metadata(self, prompt: str):
        self.calls += 1
        self.prompt = prompt
        if self._error is not None:
            raise self._error
        return self._result, STUB_METADATA


@dataclass(frozen=True, slots=True)
class QueuedDocument:
    document_id: UUID
    job_id: int
    course_id: int
    storage: LocalStorage


class StubEmbeddingProvider:
    def embed_documents(self, texts):
        return [[float(index)] * EMBEDDING_DIMENSIONS for index in range(len(texts))]

    def embed_query(self, text):
        return self.embed_documents([text])[0]


def _queue_paper(
    session_factory,
    tmp_path: Path,
    *,
    material_kind: str = "past_exam",
    email: str = "exam-extraction-worker@example.com",
) -> QueuedDocument:
    storage = LocalStorage(tmp_path / "worker-uploads", namespace="exam")
    document_id = uuid4()

    with session_factory() as session:
        role = session.scalar(select(Role).where(Role.name == "user"))
        assert role is not None
        user = User(
            name="Paper owner",
            email=email,
            password_hash="not-a-real-hash",
            role=role,
        )
        course = Course(owner=user, title="Algorithms", semester="Fall")
        session.add(course)
        session.flush()

        storage_key = storage.generate_key(course.id, document_id, "txt")
        storage.save(storage_key, BytesIO(PAPER))
        document = UploadedDocument(
            id=document_id,
            original_file_name="Final 2024.txt",
            file_type="txt",
            mime_type="text/plain",
            file_size=len(PAPER),
            file_hash=hashlib.sha256(PAPER).hexdigest(),
            uploader=user,
            course=course,
            storage_provider=storage.provider,
            storage_key=storage_key,
            status="uploaded",
            material_kind=material_kind,
        )
        session.add(document)
        session.flush()
        job = enqueue_document_job(session, document)
        session.commit()
        return QueuedDocument(
            document_id=document.id,
            job_id=job.id,
            course_id=course.id,
            storage=storage,
        )


def _run(session_factory, queued) -> bool:
    return process_next_job(
        session_factory=session_factory,
        storage=queued.storage,
        worker_id="exam-extraction-worker",
        embedding_provider=StubEmbeddingProvider(),
        vector_store=PgVectorStore(),
    )


def _document(session_factory, queued) -> UploadedDocument:
    with session_factory() as session:
        job = session.get(ProcessingJob, queued.job_id)
        document = session.get(UploadedDocument, queued.document_id)
        assert job.status == JOB_STATUS_SUCCEEDED, (
            f"job is {job.status} after stage {job.failed_stage}: "
            f"{job.last_error_code} {job.last_error_message}"
        )
        session.expunge(document)
        return document


def _questions(session_factory, queued):
    with session_factory() as session:
        return session.scalars(
            select(PastExamQuestion)
            .where(PastExamQuestion.document_id == queued.document_id)
            .order_by(PastExamQuestion.position)
        ).all()


def _install(monkeypatch, provider: RecordingProvider) -> RecordingProvider:
    monkeypatch.setattr(
        "services.exam_question_extraction.get_text_generation_provider",
        lambda *args, **kwargs: provider,
    )
    return provider


def test_a_past_exam_upload_gives_up_its_questions_as_it_is_processed(
    session_factory, tmp_path, monkeypatch
) -> None:
    provider = _install(monkeypatch, RecordingProvider())
    queued = _queue_paper(session_factory, tmp_path)

    assert _run(session_factory, queued) is True

    document = _document(session_factory, queued)
    assert document.status == "ready"
    assert document.exam_extraction_status == "succeeded"
    assert document.exam_extraction_error_code is None

    assert provider.calls == 1
    questions = _questions(session_factory, queued)
    assert len(questions) == 1
    assert questions[0].topic_key == "graph-representation"
    assert questions[0].course_id == queued.course_id


def test_the_whole_paper_reaches_the_prompt_rather_than_a_relevance_slice(
    session_factory, tmp_path, monkeypatch
) -> None:
    provider = _install(monkeypatch, RecordingProvider())
    queued = _queue_paper(session_factory, tmp_path)

    _run(session_factory, queued)

    assert "handshake lemma" in provider.prompt
    assert "Define a graph" in provider.prompt


def test_a_document_that_is_not_a_past_exam_is_never_read_for_questions(
    session_factory, tmp_path, monkeypatch
) -> None:
    provider = _install(monkeypatch, RecordingProvider())
    queued = _queue_paper(session_factory, tmp_path, material_kind="lecture_notes")

    _run(session_factory, queued)

    assert provider.calls == 0
    assert _document(session_factory, queued).exam_extraction_status == "not_applicable"
    assert _questions(session_factory, queued) == []


def test_a_provider_failure_records_the_reason_and_still_finishes_the_upload(
    session_factory, tmp_path, monkeypatch
) -> None:
    """Extraction is best-effort: a paper nobody could read is still material."""
    _install(
        monkeypatch,
        RecordingProvider(error=TextGenerationError("the provider is down")),
    )
    queued = _queue_paper(session_factory, tmp_path)

    assert _run(session_factory, queued) is True

    document = _document(session_factory, queued)
    assert document.status == "ready"
    assert document.exam_extraction_status == "failed"
    assert document.exam_extraction_error_code == "provider_error"
    assert _questions(session_factory, queued) == []


def test_an_unavailable_provider_never_reaches_the_job(
    session_factory, tmp_path, monkeypatch
) -> None:
    def unavailable(*args, **kwargs):
        raise RuntimeError("no provider is configured")

    monkeypatch.setattr(
        "services.exam_question_extraction.get_text_generation_provider", unavailable
    )
    queued = _queue_paper(session_factory, tmp_path)

    assert _run(session_factory, queued) is True

    document = _document(session_factory, queued)
    assert document.status == "ready"
    assert document.exam_extraction_status == "not_configured"


def test_deleting_the_paper_retracts_the_questions_read_from_it(
    session_factory, tmp_path, monkeypatch
) -> None:
    _install(monkeypatch, RecordingProvider())
    queued = _queue_paper(session_factory, tmp_path)
    _run(session_factory, queued)
    assert _questions(session_factory, queued)

    with session_factory() as session:
        session.delete(session.get(UploadedDocument, queued.document_id))
        session.commit()

    assert _questions(session_factory, queued) == []
