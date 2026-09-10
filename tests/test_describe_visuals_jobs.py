import hashlib
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from uuid import UUID, uuid4

import pymupdf
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from backend.app.models import (
    Course,
    DocumentChunk,
    EMBEDDING_DIMENSIONS,
    DocumentPage,
    DocumentVisual,
    JOB_STATUS_FAILED,
    JOB_STATUS_QUEUED,
    JOB_STATUS_RUNNING,
    JOB_TYPE_DESCRIBE_VISUALS,
    JOB_TYPE_EXTRACT_DOCUMENT,
    ProcessingJob,
    ProfileDocument,
    ProfileDocumentPage,
    ProfileDocumentVisual,
    ProfileProcessingJob,
    Role,
    UploadedDocument,
    User,
)
from services.processing_jobs import (
    ChunkData,
    PageData,
    ClaimedJob,
    ClaimedProfileJob,
    ProcessingJobStateError,
    VisualData,
    complete_describe_job,
    update_job_stage,
    claim_next_describe_job,
    claim_next_job,
    claim_next_profile_describe_job,
    claim_next_profile_job,
    enqueue_describe_visuals_job,
    enqueue_describe_visuals_job_if_deferred,
    enqueue_document_job,
    enqueue_profile_describe_visuals_job,
    fail_describe_job,
    fail_profile_describe_job,
    record_visual_description,
    record_profile_visual_description,
    retry_failed_profile_job,
    record_visual_failure,
    recover_expired_jobs,
    stored_visual_descriptions,
    sweep_documents_needing_visual_description,
    sweep_profile_documents_needing_visual_description,
)
from services.document_pipeline import VisualDescription, VisualType
from services import image_understanding
from services.document import DocumentActiveError, DocumentService
from services.vector_store import PgVectorStore
from storage.local import LocalStorage
from workers import document_processor
from workers.document_processor import _ResumingImageUnderstandingProvider


@dataclass
class ReadyDocument:
    document_id: UUID
    course_id: int
    user_id: int
    storage: LocalStorage
    storage_key: str


def visual_pdf(page_count: int = 2) -> bytes:
    pdf = pymupdf.open()
    for number in range(page_count):
        page = pdf.new_page(width=300, height=300)
        page.insert_text((30, 30), f"Page {number + 1} body text.")
        pixel = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 2, 2), False)
        pixel.clear_with(255)
        page.insert_image(pymupdf.Rect(30, 50, 270, 270), stream=pixel.tobytes("png"))
    content = pdf.tobytes()
    pdf.close()
    return content


def seed_ready_document(
    session_factory,
    tmp_path: Path,
    *,
    email: str = "describe-owner@example.com",
    pending_visuals: int = 2,
    described_visuals: int = 0,
    content: bytes | None = None,
) -> ReadyDocument:
    content = content if content is not None else visual_pdf()
    storage = LocalStorage(tmp_path / "describe-uploads", namespace="describe")
    document_id = uuid4()

    with session_factory() as session:
        role = session.scalar(select(Role).where(Role.name == "user"))
        assert role is not None
        user = User(
            name="Describe owner",
            email=email,
            password_hash="not-a-real-hash",
            role=role,
        )
        course = Course(
            owner=user,
            title="Describe course",
            description=None,
            semester="Fall",
            exam_date=date(2026, 6, 15),
        )
        session.add(course)
        session.flush()

        storage_key = storage.generate_key(course.id, document_id, "pdf")
        storage.save(storage_key, BytesIO(content))

        document = UploadedDocument(
            id=document_id,
            original_file_name="visuals.pdf",
            file_type="pdf",
            mime_type="application/pdf",
            file_size=len(content),
            file_hash=hashlib.sha256(content).hexdigest(),
            uploader=user,
            course=course,
            storage_provider=storage.provider,
            storage_key=storage_key,
            status="ready",
        )
        session.add(document)
        session.flush()

        total = pending_visuals + described_visuals
        for index in range(total):
            page = DocumentPage(
                document_id=document_id,
                course_id=course.id,
                content_index=index,
                page_number=index + 1,
                raw_text=f"Page {index + 1} body text.",
                text=f"Page {index + 1} body text.",
                extraction_method="native",
                raw_extraction_method="native",
                has_images=True,
                has_visual_content=True,
                visual_analysis_status=(
                    "completed" if index < described_visuals else "pending"
                ),
            )
            page.visuals = [
                DocumentVisual(
                    visual_index=0,
                    visual_type="figure",
                    source="image",
                    bbox_x0=30.0,
                    bbox_y0=50.0,
                    bbox_x1=270.0,
                    bbox_y1=270.0,
                    description=(
                        f"A described figure on page {index + 1}."
                        if index < described_visuals
                        else None
                    ),
                    analysis_status=(
                        "succeeded" if index < described_visuals else "pending"
                    ),
                )
            ]
            session.add(page)
        session.commit()
        return ReadyDocument(
            document_id=document_id,
            course_id=course.id,
            user_id=user.id,
            storage=storage,
            storage_key=storage_key,
        )


def test_enqueue_describe_visuals_job_queues_against_a_ready_document(
    session_factory, tmp_path
):
    ready = seed_ready_document(session_factory, tmp_path)
    queued_at = datetime.now(timezone.utc)

    with session_factory() as session:
        document = session.get(UploadedDocument, ready.document_id)
        job = enqueue_describe_visuals_job(session, document, now=queued_at)
        session.commit()
        job_id = job.id

    with session_factory() as session:
        job = session.get(ProcessingJob, job_id)
        assert job.job_type == JOB_TYPE_DESCRIBE_VISUALS
        assert job.status == JOB_STATUS_QUEUED
        assert job.attempt_count == 0
        assert session.get(UploadedDocument, ready.document_id).status == "ready"


def test_enqueue_describe_visuals_job_refuses_a_document_that_is_not_ready(
    session_factory, tmp_path
):
    ready = seed_ready_document(session_factory, tmp_path)

    with session_factory() as session:
        document = session.get(UploadedDocument, ready.document_id)
        document.status = "processing"
        session.flush()
        with pytest.raises(ProcessingJobStateError):
            enqueue_describe_visuals_job(session, document)


def test_claiming_a_describe_job_leaves_the_document_ready(session_factory, tmp_path):
    ready = seed_ready_document(session_factory, tmp_path)
    queued_at = datetime.now(timezone.utc)

    with session_factory() as session:
        document = session.get(UploadedDocument, ready.document_id)
        enqueue_describe_visuals_job(session, document, now=queued_at)
        session.commit()

    with session_factory() as session:
        claim = claim_next_describe_job(
            session,
            "describe-worker",
            ready.storage.provider,
            60,
            now=queued_at + timedelta(seconds=1),
        )

    assert claim is not None
    assert claim.job_type == JOB_TYPE_DESCRIBE_VISUALS
    assert claim.document_id == ready.document_id
    assert claim.attempt_count == 1
    assert len(claim.claim_token) == 36

    with session_factory() as session:
        assert session.get(UploadedDocument, ready.document_id).status == "ready"
        job = session.scalar(
            select(ProcessingJob).where(
                ProcessingJob.document_id == ready.document_id,
                ProcessingJob.job_type == JOB_TYPE_DESCRIBE_VISUALS,
            )
        )
        assert job.status == JOB_STATUS_RUNNING
        assert job.processing_stage == "validating"


def test_a_describe_job_is_not_claimed_while_its_document_is_reprocessing(
    session_factory, tmp_path
):
    ready = seed_ready_document(session_factory, tmp_path)
    queued_at = datetime.now(timezone.utc)

    with session_factory() as session:
        document = session.get(UploadedDocument, ready.document_id)
        enqueue_describe_visuals_job(session, document, now=queued_at)
        document.status = "processing"
        session.commit()

    with session_factory() as session:
        claim = claim_next_describe_job(
            session,
            "describe-worker",
            ready.storage.provider,
            60,
            now=queued_at + timedelta(seconds=1),
        )

    assert claim is None


def test_the_extract_claim_ignores_describe_jobs(session_factory, tmp_path):
    ready = seed_ready_document(session_factory, tmp_path)
    queued_at = datetime.now(timezone.utc)

    with session_factory() as session:
        document = session.get(UploadedDocument, ready.document_id)
        enqueue_describe_visuals_job(session, document, now=queued_at)
        session.commit()

    with session_factory() as session:
        claim = claim_next_job(
            session,
            "extract-worker",
            ready.storage.provider,
            60,
            now=queued_at + timedelta(seconds=1),
        )

    assert claim is None


def test_a_running_describe_job_does_not_block_that_owner_from_extracting(
    session_factory, tmp_path
):
    ready = seed_ready_document(session_factory, tmp_path)
    queued_at = datetime.now(timezone.utc)
    second_document_id = uuid4()

    with session_factory() as session:
        document = session.get(UploadedDocument, ready.document_id)
        enqueue_describe_visuals_job(session, document, now=queued_at)

        content = b"Second document body"
        storage_key = ready.storage.generate_key(
            ready.course_id, second_document_id, "txt"
        )
        ready.storage.save(storage_key, BytesIO(content))
        second = UploadedDocument(
            id=second_document_id,
            original_file_name="second.txt",
            file_type="txt",
            mime_type="text/plain",
            file_size=len(content),
            file_hash=hashlib.sha256(content).hexdigest(),
            user_id=ready.user_id,
            course_id=ready.course_id,
            storage_provider=ready.storage.provider,
            storage_key=storage_key,
            status="uploaded",
        )
        session.add(second)
        session.flush()
        enqueue_document_job(session, second, now=queued_at)
        session.commit()

    with session_factory() as session:
        describe_claim = claim_next_describe_job(
            session,
            "describe-worker",
            ready.storage.provider,
            60,
            max_active_per_user=1,
            now=queued_at + timedelta(seconds=1),
        )
    assert describe_claim is not None

    with session_factory() as session:
        extract_claim = claim_next_job(
            session,
            "extract-worker",
            ready.storage.provider,
            60,
            max_active_per_user=1,
            now=queued_at + timedelta(seconds=2),
        )

    assert extract_claim is not None
    assert extract_claim.document_id == second_document_id
    assert extract_claim.job_type == JOB_TYPE_EXTRACT_DOCUMENT


def claimed_describe_job(session_factory, ready, *, now):
    with session_factory() as session:
        document = session.get(UploadedDocument, ready.document_id)
        enqueue_describe_visuals_job(session, document, now=now)
        session.commit()
    with session_factory() as session:
        return claim_next_describe_job(
            session,
            "describe-worker",
            ready.storage.provider,
            60,
            now=now + timedelta(seconds=1),
        )


def test_a_described_visual_is_checkpointed_on_its_own(session_factory, tmp_path):
    ready = seed_ready_document(session_factory, tmp_path)
    queued_at = datetime.now(timezone.utc)
    claim = claimed_describe_job(session_factory, ready, now=queued_at)

    with session_factory() as session:
        recorded = record_visual_description(
            session,
            claim.id,
            claim.claim_token,
            page_number=1,
            visual_index=0,
            visual_type="diagram",
            description="A labelled diagram of the cell membrane.",
        )

    assert recorded is True

    with session_factory() as session:
        visuals = session.scalars(
            select(DocumentVisual)
            .join(DocumentPage, DocumentPage.id == DocumentVisual.page_id)
            .where(DocumentPage.document_id == ready.document_id)
            .order_by(DocumentPage.page_number)
        ).all()
        assert visuals[0].analysis_status == "succeeded"
        assert visuals[0].visual_type == "diagram"
        assert visuals[0].description == "A labelled diagram of the cell membrane."
        assert visuals[1].analysis_status == "pending"
        assert visuals[1].description is None
        assert session.get(UploadedDocument, ready.document_id).status == "ready"


def test_a_checkpoint_is_refused_once_the_claim_is_gone(session_factory, tmp_path):
    ready = seed_ready_document(session_factory, tmp_path)
    queued_at = datetime.now(timezone.utc)
    claim = claimed_describe_job(session_factory, ready, now=queued_at)

    with session_factory() as session:
        recorded = record_visual_description(
            session,
            claim.id,
            "00000000-0000-0000-0000-000000000000",
            page_number=1,
            visual_index=0,
            visual_type="diagram",
            description="A description written by a worker that lost its claim.",
        )

    assert recorded is False

    with session_factory() as session:
        visual = session.scalar(
            select(DocumentVisual)
            .join(DocumentPage, DocumentPage.id == DocumentVisual.page_id)
            .where(DocumentPage.document_id == ready.document_id)
            .order_by(DocumentPage.page_number)
        )
        assert visual.analysis_status == "pending"
        assert visual.description is None


def test_a_checkpoint_is_refused_once_the_lease_expired(session_factory, tmp_path):
    ready = seed_ready_document(session_factory, tmp_path)
    queued_at = datetime.now(timezone.utc)
    claim = claimed_describe_job(session_factory, ready, now=queued_at)

    with session_factory() as session:
        recorded = record_visual_description(
            session,
            claim.id,
            claim.claim_token,
            page_number=1,
            visual_index=0,
            visual_type="diagram",
            description="A description written after the lease expired.",
            now=queued_at + timedelta(seconds=600),
        )

    assert recorded is False


def test_a_checkpoint_for_an_unknown_visual_is_refused(session_factory, tmp_path):
    ready = seed_ready_document(session_factory, tmp_path)
    queued_at = datetime.now(timezone.utc)
    claim = claimed_describe_job(session_factory, ready, now=queued_at)

    with session_factory() as session:
        recorded = record_visual_description(
            session,
            claim.id,
            claim.claim_token,
            page_number=99,
            visual_index=4,
            visual_type="diagram",
            description="A description of a visual this document does not carry.",
        )

    assert recorded is False


def test_a_failed_visual_is_checkpointed_without_a_description(
    session_factory, tmp_path
):
    ready = seed_ready_document(session_factory, tmp_path)
    queued_at = datetime.now(timezone.utc)
    claim = claimed_describe_job(session_factory, ready, now=queued_at)

    with session_factory() as session:
        recorded = record_visual_failure(
            session,
            claim.id,
            claim.claim_token,
            page_number=1,
            visual_index=0,
            error_code="VISUAL_ANALYSIS_FAILED",
        )

    assert recorded is True

    with session_factory() as session:
        visual = session.scalar(
            select(DocumentVisual)
            .join(DocumentPage, DocumentPage.id == DocumentVisual.page_id)
            .where(DocumentPage.document_id == ready.document_id)
            .order_by(DocumentPage.page_number)
        )
        assert visual.analysis_status == "failed"
        assert visual.error_code == "VISUAL_ANALYSIS_FAILED"
        assert visual.description is None


def test_stored_descriptions_resume_only_what_actually_succeeded(
    session_factory, tmp_path
):
    ready = seed_ready_document(
        session_factory, tmp_path, pending_visuals=1, described_visuals=1
    )

    with session_factory() as session:
        stored = stored_visual_descriptions(session, ready.document_id)

    assert stored == {(1, 0): ("figure", "A described figure on page 1.")}


def test_a_retryable_describe_failure_requeues_and_leaves_the_document_alone(
    session_factory, tmp_path
):
    ready = seed_ready_document(session_factory, tmp_path)
    queued_at = datetime.now(timezone.utc)
    claim = claimed_describe_job(session_factory, ready, now=queued_at)

    with session_factory() as session:
        status = fail_describe_job(
            session,
            claim.id,
            claim.claim_token,
            error_code="IMAGE_UNDERSTANDING_FAILED",
            error_message="The vision provider is unavailable.",
            retryable=True,
            now=queued_at + timedelta(seconds=5),
        )

    assert status == JOB_STATUS_QUEUED

    with session_factory() as session:
        document = session.get(UploadedDocument, ready.document_id)
        job = session.scalar(
            select(ProcessingJob).where(
                ProcessingJob.document_id == ready.document_id,
                ProcessingJob.job_type == JOB_TYPE_DESCRIBE_VISUALS,
            )
        )
        assert document.status == "ready"
        assert document.processing_error is None
        assert job.status == JOB_STATUS_QUEUED
        assert job.last_error_code == "IMAGE_UNDERSTANDING_FAILED"
        assert job.claim_token is None


def test_an_exhausted_describe_job_fails_without_failing_its_document(
    session_factory, tmp_path
):
    ready = seed_ready_document(session_factory, tmp_path)
    queued_at = datetime.now(timezone.utc)
    claim = claimed_describe_job(session_factory, ready, now=queued_at)

    with session_factory() as session:
        status = fail_describe_job(
            session,
            claim.id,
            claim.claim_token,
            error_code="IMAGE_UNDERSTANDING_FAILED",
            error_message="The vision provider is unavailable.",
            retryable=False,
            now=queued_at + timedelta(seconds=5),
        )

    assert status == JOB_STATUS_FAILED

    with session_factory() as session:
        document = session.get(UploadedDocument, ready.document_id)
        assert document.status == "ready"
        assert document.processing_error is None


def test_an_expired_describe_lease_is_recovered_without_touching_the_document(
    session_factory, tmp_path
):
    ready = seed_ready_document(session_factory, tmp_path)
    queued_at = datetime.now(timezone.utc)
    claimed_describe_job(session_factory, ready, now=queued_at)

    with session_factory() as session:
        recovered = recover_expired_jobs(
            session, now=queued_at + timedelta(seconds=600)
        )

    assert recovered == 1

    with session_factory() as session:
        document = session.get(UploadedDocument, ready.document_id)
        job = session.scalar(
            select(ProcessingJob).where(
                ProcessingJob.document_id == ready.document_id,
                ProcessingJob.job_type == JOB_TYPE_DESCRIBE_VISUALS,
            )
        )
        assert document.status == "ready"
        assert document.processing_error is None
        assert job.status == JOB_STATUS_QUEUED


def described_pages(page_count=2, text_suffix="A described figure."):
    return [
        PageData(
            content_index=index,
            text=f"Page {index + 1} body text.\n\n[Figure]\n{text_suffix}",
            page_number=index + 1,
            extraction_method="native",
            has_images=True,
            needs_ocr=False,
            raw_text=f"Page {index + 1} body text.",
            raw_extraction_method="native",
            has_visual_content=True,
            raw_needs_ocr=False,
            ocr_status="not_required",
            visual_analysis_status="completed",
            visuals=(
                VisualData(
                    visual_index=0,
                    visual_type="figure",
                    source="image",
                    bbox=(30.0, 50.0, 270.0, 270.0),
                    description=text_suffix,
                    analysis_status="succeeded",
                ),
            ),
        )
        for index in range(page_count)
    ]


def described_chunks(page_count=2, text_suffix="A described figure."):
    return [
        ChunkData(
            text=f"Page {index + 1} body text.\n\n[Figure]\n{text_suffix}",
            page_number=index + 1,
            end_page_number=index + 1,
        )
        for index in range(page_count)
    ]


def advance_describe_to_embedding(session_factory, claim):
    stages = ("extracting_text", "cleaning_text", "chunking", "generating_embeddings")
    for stage in stages:
        with session_factory() as session:
            assert update_job_stage(session, claim.id, claim.claim_token, stage)


def test_completing_a_describe_job_swaps_chunks_and_keeps_the_document_ready(
    session_factory, tmp_path
):
    ready = seed_ready_document(session_factory, tmp_path)
    queued_at = datetime.now(timezone.utc)
    claim = claimed_describe_job(session_factory, ready, now=queued_at)
    advance_describe_to_embedding(session_factory, claim)

    with session_factory() as session:
        page_ids_before = set(
            session.scalars(
                select(DocumentPage.id).where(
                    DocumentPage.document_id == ready.document_id
                )
            ).all()
        )

    with session_factory() as session:
        completed = complete_describe_job(
            session,
            claim.id,
            claim.claim_token,
            described_chunks(),
            described_pages(),
            embeddings=[[0.1] * EMBEDDING_DIMENSIONS for _ in range(2)],
            vector_store=PgVectorStore(),
            now=queued_at + timedelta(seconds=10),
        )

    assert completed is True

    with session_factory() as session:
        document = session.get(UploadedDocument, ready.document_id)
        assert document.status == "ready"
        chunks = session.scalars(
            select(DocumentChunk.text)
            .where(DocumentChunk.document_id == ready.document_id)
            .order_by(DocumentChunk.chunk_index)
        ).all()
        assert len(chunks) == 2
        assert all("[Figure]" in text for text in chunks)
        page_ids_after = set(
            session.scalars(
                select(DocumentPage.id).where(
                    DocumentPage.document_id == ready.document_id
                )
            ).all()
        )
        assert page_ids_after == page_ids_before
        job = session.scalar(
            select(ProcessingJob).where(
                ProcessingJob.document_id == ready.document_id,
                ProcessingJob.job_type == JOB_TYPE_DESCRIBE_VISUALS,
            )
        )
        assert job.status == "succeeded"
        assert job.claim_token is None


def test_a_describe_completion_that_changes_the_page_shape_is_refused(
    session_factory, tmp_path
):
    ready = seed_ready_document(session_factory, tmp_path)
    queued_at = datetime.now(timezone.utc)
    claim = claimed_describe_job(session_factory, ready, now=queued_at)
    advance_describe_to_embedding(session_factory, claim)

    with session_factory() as session:
        with pytest.raises(ValueError):
            complete_describe_job(
                session,
                claim.id,
                claim.claim_token,
                described_chunks(page_count=1),
                described_pages(page_count=1),
                embeddings=[[0.1] * EMBEDDING_DIMENSIONS],
                vector_store=PgVectorStore(),
                now=queued_at + timedelta(seconds=10),
            )

    with session_factory() as session:
        pages = session.scalars(
            select(DocumentPage).where(DocumentPage.document_id == ready.document_id)
        ).all()
        assert len(pages) == 2
        assert session.get(UploadedDocument, ready.document_id).status == "ready"


def test_a_describe_completion_that_regresses_ocr_is_refused(session_factory, tmp_path):
    ready = seed_ready_document(session_factory, tmp_path)
    queued_at = datetime.now(timezone.utc)

    with session_factory() as session:
        for page in session.scalars(
            select(DocumentPage).where(DocumentPage.document_id == ready.document_id)
        ).all():
            page.ocr_status = "succeeded"
        session.commit()

    claim = claimed_describe_job(session_factory, ready, now=queued_at)
    advance_describe_to_embedding(session_factory, claim)

    with session_factory() as session:
        with pytest.raises(ValueError):
            complete_describe_job(
                session,
                claim.id,
                claim.claim_token,
                described_chunks(),
                described_pages(),
                embeddings=[[0.1] * EMBEDDING_DIMENSIONS for _ in range(2)],
                vector_store=PgVectorStore(),
                now=queued_at + timedelta(seconds=10),
            )

    with session_factory() as session:
        statuses = session.scalars(
            select(DocumentPage.ocr_status).where(
                DocumentPage.document_id == ready.document_id
            )
        ).all()
        assert set(statuses) == {"succeeded"}


def test_a_describe_completion_is_refused_once_the_document_is_deleting(
    session_factory, tmp_path
):
    ready = seed_ready_document(session_factory, tmp_path)
    queued_at = datetime.now(timezone.utc)
    claim = claimed_describe_job(session_factory, ready, now=queued_at)
    advance_describe_to_embedding(session_factory, claim)

    with session_factory() as session:
        session.get(UploadedDocument, ready.document_id).status = "deleting"
        session.commit()

    with session_factory() as session:
        completed = complete_describe_job(
            session,
            claim.id,
            claim.claim_token,
            described_chunks(),
            described_pages(),
            embeddings=[[0.1] * EMBEDDING_DIMENSIONS for _ in range(2)],
            vector_store=PgVectorStore(),
            now=queued_at + timedelta(seconds=10),
        )

    assert completed is False

    with session_factory() as session:
        assert session.get(UploadedDocument, ready.document_id).status == "deleting"


def test_a_describe_completion_whose_visuals_moved_is_refused(
    session_factory, tmp_path
):
    ready = seed_ready_document(session_factory, tmp_path)
    queued_at = datetime.now(timezone.utc)
    claim = claimed_describe_job(session_factory, ready, now=queued_at)
    advance_describe_to_embedding(session_factory, claim)

    moved = described_pages()
    moved[0] = replace(
        moved[0],
        visuals=(replace(moved[0].visuals[0], bbox=(31.0, 51.0, 271.0, 271.0)),),
    )

    with session_factory() as session:
        with pytest.raises(ValueError):
            complete_describe_job(
                session,
                claim.id,
                claim.claim_token,
                described_chunks(),
                moved,
                embeddings=[[0.1] * EMBEDDING_DIMENSIONS for _ in range(2)],
                vector_store=PgVectorStore(),
                now=queued_at + timedelta(seconds=10),
            )

    with session_factory() as session:
        chunks = session.scalars(
            select(DocumentChunk).where(DocumentChunk.document_id == ready.document_id)
        ).all()
        assert chunks == []
        assert session.get(UploadedDocument, ready.document_id).status == "ready"


class _RecordingConnection:
    def __init__(self):
        self.sent = []

    def send(self, message):
        self.sent.append(message)


class _CountingVision:
    enabled = True

    def __init__(self):
        self.calls = []

    def describe_visual(self, visual_png, *, page_number, visual_index, suggested_type):
        self.calls.append((page_number, visual_index))
        return VisualDescription(
            visual_type=suggested_type,
            description=f"A fresh description for page {page_number}.",
        )


def test_a_cached_visual_is_never_sent_to_the_provider_again():
    delegate = _CountingVision()
    connection = _RecordingConnection()
    provider = _ResumingImageUnderstandingProvider(
        delegate,
        {(1, 0): ("diagram", "A description an earlier attempt already paid for.")},
        connection,
    )

    result = provider.describe_visual(
        b"\x89PNG\r\n\x1a\n",
        page_number=1,
        visual_index=0,
        suggested_type=VisualType.FIGURE,
    )

    assert delegate.calls == []
    assert connection.sent == []
    assert result.visual_type == VisualType.DIAGRAM
    assert result.description == ("A description an earlier attempt already paid for.")


def test_a_fresh_description_is_reported_before_it_is_returned():
    delegate = _CountingVision()
    connection = _RecordingConnection()
    provider = _ResumingImageUnderstandingProvider(delegate, {}, connection)

    result = provider.describe_visual(
        b"\x89PNG\r\n\x1a\n",
        page_number=3,
        visual_index=1,
        suggested_type=VisualType.DIAGRAM,
    )

    assert delegate.calls == [(3, 1)]
    assert connection.sent == [
        ("visual", 3, 1, "diagram", "A fresh description for page 3.")
    ]
    assert result.description == "A fresh description for page 3."


def test_a_visual_the_provider_declines_is_not_reported():
    class SilentVision:
        enabled = True

        def describe_visual(
            self, visual_png, *, page_number, visual_index, suggested_type
        ):
            return None

    connection = _RecordingConnection()
    provider = _ResumingImageUnderstandingProvider(SilentVision(), {}, connection)

    result = provider.describe_visual(
        b"\x89PNG\r\n\x1a\n",
        page_number=1,
        visual_index=0,
        suggested_type=VisualType.DIAGRAM,
    )

    assert result is None
    assert connection.sent == []


def test_a_disabled_delegate_disables_the_resuming_provider():
    class DisabledVision:
        enabled = False

        def describe_visual(
            self, visual_png, *, page_number, visual_index, suggested_type
        ):
            raise AssertionError("a disabled provider must not be called")

    provider = _ResumingImageUnderstandingProvider(
        DisabledVision(), {}, _RecordingConnection()
    )

    assert provider.enabled is False


class _StubEmbeddings:
    def embed_documents(self, texts):
        return [[0.1] * EMBEDDING_DIMENSIONS for _ in texts]

    def embed_query(self, text):
        return [0.1] * EMBEDDING_DIMENSIONS


class _StubVisionServer:
    def __init__(self, *, stall_after: int | None = None, stall_seconds: float = 30.0):
        self.stall_after = stall_after
        self.stall_seconds = stall_seconds
        self.request_count = 0
        self._lock = threading.Lock()
        server = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                self.rfile.read(length)
                if self.path.endswith("/api/show"):
                    self._respond({"capabilities": ["completion", "vision"]})
                    return
                with server._lock:
                    server.request_count += 1
                    ordinal = server.request_count
                if server.stall_after is not None and ordinal > server.stall_after:
                    time.sleep(server.stall_seconds)
                self._respond({"response": f"A stub description number {ordinal}."})

            def _respond(self, payload):
                body = json.dumps(payload).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                return

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self._httpd.server_address[1]
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc_info):
        self._httpd.shutdown()
        self._httpd.server_close()


def _use_stub_vision(monkeypatch, port, *, attempt_timeout):
    monkeypatch.setenv("OLLAMA_BASE_URL", f"http://127.0.0.1:{port}")
    monkeypatch.setenv("OLLAMA_MODEL", "stub-vision")
    monkeypatch.setenv("IMAGE_UNDERSTANDING_ENABLED", "true")
    monkeypatch.setenv("IMAGE_UNDERSTANDING_TIMEOUT_SECONDS", "60")
    monkeypatch.setenv("DESCRIBE_VISUALS_ATTEMPT_TIMEOUT_SECONDS", str(attempt_timeout))
    monkeypatch.setattr(
        document_processor,
        "settings",
        replace(
            document_processor.settings,
            describe_visuals_attempt_timeout_seconds=attempt_timeout,
        ),
    )
    monkeypatch.setattr(
        image_understanding,
        "settings",
        replace(
            image_understanding.settings,
            ai_vision_model="ollama:stub-vision",
            ollama_base_url=f"http://127.0.0.1:{port}",
            image_understanding_timeout_seconds=60,
        ),
    )


def test_an_interrupted_describe_attempt_keeps_what_it_already_paid_for(
    session_factory, tmp_path, monkeypatch
):
    ready = seed_ready_document(session_factory, tmp_path)
    with session_factory() as session:
        document = session.get(UploadedDocument, ready.document_id)
        enqueue_describe_visuals_job(session, document)
        session.commit()

    with _StubVisionServer(stall_after=1) as server:
        _use_stub_vision(monkeypatch, server.port, attempt_timeout=8)
        handled = document_processor.process_next_job(
            session_factory=session_factory,
            storage=ready.storage,
            worker_id="describe-worker",
            embedding_provider=_StubEmbeddings(),
            vector_store=PgVectorStore(),
        )
        assert handled is True
        first_attempt_requests = server.request_count

    assert first_attempt_requests == 2

    with session_factory() as session:
        visuals = session.scalars(
            select(DocumentVisual)
            .join(DocumentPage, DocumentPage.id == DocumentVisual.page_id)
            .where(DocumentPage.document_id == ready.document_id)
            .order_by(DocumentPage.page_number)
        ).all()
        assert visuals[0].analysis_status == "succeeded"
        assert visuals[0].description == "A stub description number 1."
        assert visuals[1].analysis_status == "pending"
        assert visuals[1].description is None
        job = session.scalar(
            select(ProcessingJob).where(
                ProcessingJob.document_id == ready.document_id,
                ProcessingJob.job_type == JOB_TYPE_DESCRIBE_VISUALS,
            )
        )
        assert job.status == JOB_STATUS_QUEUED
        assert job.last_error_code == "PROCESSING_TIMEOUT"
        assert session.get(UploadedDocument, ready.document_id).status == "ready"

    with session_factory() as session:
        job = session.scalar(
            select(ProcessingJob).where(
                ProcessingJob.document_id == ready.document_id,
                ProcessingJob.job_type == JOB_TYPE_DESCRIBE_VISUALS,
            )
        )
        job.available_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        session.commit()

    with _StubVisionServer() as server:
        _use_stub_vision(monkeypatch, server.port, attempt_timeout=120)
        handled = document_processor.process_next_job(
            session_factory=session_factory,
            storage=ready.storage,
            worker_id="describe-worker",
            embedding_provider=_StubEmbeddings(),
            vector_store=PgVectorStore(),
        )
        assert handled is True
        second_attempt_requests = server.request_count

    assert second_attempt_requests == 1

    with session_factory() as session:
        visuals = session.scalars(
            select(DocumentVisual)
            .join(DocumentPage, DocumentPage.id == DocumentVisual.page_id)
            .where(DocumentPage.document_id == ready.document_id)
            .order_by(DocumentPage.page_number)
        ).all()
        assert visuals[0].description == "A stub description number 1."
        assert visuals[1].analysis_status == "succeeded"
        chunks = session.scalars(
            select(DocumentChunk.text)
            .where(DocumentChunk.document_id == ready.document_id)
            .order_by(DocumentChunk.chunk_index)
        ).all()
        assert any("A stub description number 1." in text for text in chunks)
        job = session.scalar(
            select(ProcessingJob).where(
                ProcessingJob.document_id == ready.document_id,
                ProcessingJob.job_type == JOB_TYPE_DESCRIBE_VISUALS,
            )
        )
        assert job.status == "succeeded"
        assert session.get(UploadedDocument, ready.document_id).status == "ready"


def test_a_single_slot_worker_never_claims_a_describe_job(monkeypatch):
    claims: list[bool] = []

    def fake_process_next_job(**kwargs):
        claims.append(kwargs.get("claim_describe", True))
        return False

    monkeypatch.setattr(document_processor, "process_next_job", fake_process_next_job)
    monkeypatch.setattr(
        document_processor, "_maintenance_cycle", lambda *args, **kwargs: None
    )

    document_processor._run_worker_serially(
        once=True,
        worker_id="solo-worker",
        session_factory=lambda: None,
        storage=None,
        stop=threading.Event(),
    )

    assert claims == [False]


def test_only_slots_beyond_the_first_claim_describe_jobs(monkeypatch):
    seen: dict[str, bool] = {}
    stop = threading.Event()

    def fake_process_next_job(**kwargs):
        seen[kwargs["worker_id"]] = kwargs.get("claim_describe", True)
        if len(seen) >= 2:
            stop.set()
        return False

    monkeypatch.setattr(document_processor, "process_next_job", fake_process_next_job)
    monkeypatch.setattr(
        document_processor, "_maintenance_cycle", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        document_processor,
        "settings",
        replace(document_processor.settings, processing_job_poll_seconds=0.01),
    )

    document_processor._run_worker_slots(
        worker_id="slots-worker",
        concurrency=2,
        session_factory=lambda: None,
        storage=None,
        stop=stop,
    )

    assert seen["slots-worker:slot-0"] is False
    assert seen["slots-worker:slot-1"] is True


def describe_job_count(session_factory, document_id):
    with session_factory() as session:
        return session.scalar(
            select(func.count())
            .select_from(ProcessingJob)
            .where(
                ProcessingJob.document_id == document_id,
                ProcessingJob.job_type == JOB_TYPE_DESCRIBE_VISUALS,
            )
        )


def test_the_sweep_queues_a_ready_document_whose_visuals_are_still_pending(
    session_factory, tmp_path
):
    ready = seed_ready_document(session_factory, tmp_path)

    with session_factory() as session:
        queued = sweep_documents_needing_visual_description(session)

    assert queued == 1
    assert describe_job_count(session_factory, ready.document_id) == 1


def test_the_sweep_rescues_documents_processed_while_vision_was_off(
    session_factory, tmp_path
):
    ready = seed_ready_document(session_factory, tmp_path)
    with session_factory() as session:
        for page in session.scalars(
            select(DocumentPage).where(DocumentPage.document_id == ready.document_id)
        ).all():
            page.visual_analysis_status = "not_configured"
            for visual in page.visuals:
                visual.analysis_status = "not_configured"
        session.commit()

    with session_factory() as session:
        queued = sweep_documents_needing_visual_description(session)

    assert queued == 1
    assert describe_job_count(session_factory, ready.document_id) == 1


def test_the_sweep_gives_each_document_exactly_one_turn(session_factory, tmp_path):
    ready = seed_ready_document(session_factory, tmp_path)

    with session_factory() as session:
        assert sweep_documents_needing_visual_description(session) == 1
    with session_factory() as session:
        assert sweep_documents_needing_visual_description(session) == 0

    assert describe_job_count(session_factory, ready.document_id) == 1


def test_a_document_whose_visuals_are_all_described_is_never_swept(
    session_factory, tmp_path
):
    ready = seed_ready_document(
        session_factory, tmp_path, pending_visuals=0, described_visuals=2
    )

    with session_factory() as session:
        assert sweep_documents_needing_visual_description(session) == 0

    assert describe_job_count(session_factory, ready.document_id) == 0


def test_a_document_that_is_not_ready_is_never_swept(session_factory, tmp_path):
    ready = seed_ready_document(session_factory, tmp_path)
    with session_factory() as session:
        session.get(UploadedDocument, ready.document_id).status = "processing"
        session.commit()

    with session_factory() as session:
        assert sweep_documents_needing_visual_description(session) == 0

    assert describe_job_count(session_factory, ready.document_id) == 0


def test_a_deleted_course_is_never_swept(session_factory, tmp_path):
    ready = seed_ready_document(session_factory, tmp_path)
    with session_factory() as session:
        session.get(Course, ready.course_id).is_deleted = True
        session.commit()

    with session_factory() as session:
        assert sweep_documents_needing_visual_description(session) == 0

    assert describe_job_count(session_factory, ready.document_id) == 0


def test_a_document_with_deferred_visuals_is_queued_immediately(
    session_factory, tmp_path
):
    ready = seed_ready_document(session_factory, tmp_path)

    with session_factory() as session:
        queued = enqueue_describe_visuals_job_if_deferred(session, ready.document_id)

    assert queued is True
    assert describe_job_count(session_factory, ready.document_id) == 1


def test_a_fully_described_document_is_not_queued_immediately(
    session_factory, tmp_path
):
    ready = seed_ready_document(
        session_factory, tmp_path, pending_visuals=0, described_visuals=2
    )

    with session_factory() as session:
        queued = enqueue_describe_visuals_job_if_deferred(session, ready.document_id)

    assert queued is False
    assert describe_job_count(session_factory, ready.document_id) == 0


def test_queueing_a_deferred_document_twice_keeps_one_job(session_factory, tmp_path):
    ready = seed_ready_document(session_factory, tmp_path)

    with session_factory() as session:
        assert enqueue_describe_visuals_job_if_deferred(session, ready.document_id)
    with session_factory() as session:
        assert not enqueue_describe_visuals_job_if_deferred(session, ready.document_id)

    assert describe_job_count(session_factory, ready.document_id) == 1


def test_a_running_describe_job_blocks_deleting_its_document(session_factory, tmp_path):
    ready = seed_ready_document(session_factory, tmp_path)
    queued_at = datetime.now(timezone.utc)
    claimed_describe_job(session_factory, ready, now=queued_at)

    with session_factory() as session:
        with pytest.raises(DocumentActiveError):
            DocumentService.delete_document(
                session,
                ready.storage,
                ready.document_id,
                ready.course_id,
                vector_store=PgVectorStore(),
            )

    with session_factory() as session:
        assert session.get(UploadedDocument, ready.document_id) is not None


def test_forcing_a_delete_still_removes_a_document_being_described(
    session_factory, tmp_path
):
    ready = seed_ready_document(session_factory, tmp_path)
    queued_at = datetime.now(timezone.utc)
    claimed_describe_job(session_factory, ready, now=queued_at)

    with session_factory() as session:
        DocumentService.delete_document(
            session,
            ready.storage,
            ready.document_id,
            ready.course_id,
            vector_store=PgVectorStore(),
            force=True,
        )

    with session_factory() as session:
        assert session.get(UploadedDocument, ready.document_id) is None


def test_retrying_a_failed_document_rearms_its_visual_description(
    session_factory, tmp_path
):
    ready = seed_ready_document(session_factory, tmp_path)
    with session_factory() as session:
        document = session.get(UploadedDocument, ready.document_id)
        enqueue_describe_visuals_job(session, document)
        session.commit()

    with session_factory() as session:
        job = session.scalar(
            select(ProcessingJob).where(
                ProcessingJob.document_id == ready.document_id,
                ProcessingJob.job_type == JOB_TYPE_EXTRACT_DOCUMENT,
            )
        )
        if job is None:
            job = ProcessingJob(
                document_id=ready.document_id,
                course_id=ready.course_id,
                job_type=JOB_TYPE_EXTRACT_DOCUMENT,
                max_attempts=3,
                available_at=datetime.now(timezone.utc),
            )
            session.add(job)
        job.status = JOB_STATUS_FAILED
        job.finished_at = datetime.now(timezone.utc)
        job.last_error_code = "OCR_REQUIRED"
        job.attempt_count = 1
        session.get(UploadedDocument, ready.document_id).status = "failed"
        session.commit()

    with session_factory() as session:
        DocumentService.retry_document(session, ready.document_id, ready.course_id)

    assert describe_job_count(session_factory, ready.document_id) == 0


class _ScriptedConnection:
    def __init__(self):
        self.sent = []

    def send(self, message):
        self.sent.append(message)

    def recv(self):
        return ("continue",)

    def close(self):
        return None


def _extraction_job(ready, *, profile: bool):
    common = dict(
        id=1,
        document_id=ready.document_id,
        claim_token="0" * 36,
        attempt_count=1,
        max_attempts=3,
        storage_provider=ready.storage.provider,
        storage_key=ready.storage_key,
        file_hash=hashlib.sha256(
            ready.storage.open(ready.storage_key).read()
        ).hexdigest(),
        file_type="pdf",
        file_size=len(ready.storage.open(ready.storage_key).read()),
        correlation_id=None,
        parent_operation_id=None,
    )
    if profile:
        return ClaimedProfileJob(user_id=ready.user_id, **common)
    return ClaimedJob(course_id=ready.course_id, user_id=ready.user_id, **common)


def _described_statuses(connection):
    for message in connection.sent:
        if message[0] == "succeeded":
            return [
                visual.analysis_status for page in message[1] for visual in page.visuals
            ]
    raise AssertionError("the extraction child never reported success")


def test_a_course_extraction_defers_visuals_beyond_its_inline_budget(
    session_factory, tmp_path, monkeypatch
):
    ready = seed_ready_document(
        session_factory, tmp_path, content=visual_pdf(page_count=4)
    )
    connection = _ScriptedConnection()

    with _StubVisionServer() as server:
        _use_stub_vision(monkeypatch, server.port, attempt_timeout=120)
        document_processor._extraction_process(
            connection, ready.storage, _extraction_job(ready, profile=False)
        )

    statuses = _described_statuses(connection)
    assert statuses.count("succeeded") == 2
    assert statuses.count("pending") == 2


def test_a_profile_extraction_defers_visuals_the_same_way(
    session_factory, tmp_path, monkeypatch
):
    ready = seed_ready_document(
        session_factory, tmp_path, content=visual_pdf(page_count=4)
    )
    connection = _ScriptedConnection()

    with _StubVisionServer() as server:
        _use_stub_vision(monkeypatch, server.port, attempt_timeout=120)
        document_processor._extraction_process(
            connection, ready.storage, _extraction_job(ready, profile=True)
        )

    statuses = _described_statuses(connection)
    assert statuses.count("succeeded") == 2
    assert statuses.count("pending") == 2


@dataclass
class ReadyProfileDocument:
    document_id: UUID
    user_id: int
    storage: LocalStorage
    storage_key: str


def seed_ready_profile_document(
    session_factory,
    tmp_path: Path,
    *,
    email: str = "profile-describe@example.com",
    pending_visuals: int = 2,
    described_visuals: int = 0,
) -> ReadyProfileDocument:
    content = visual_pdf(page_count=pending_visuals + described_visuals)
    storage = LocalStorage(tmp_path / "profile-uploads", namespace="profile")
    document_id = uuid4()

    with session_factory() as session:
        role = session.scalar(select(Role).where(Role.name == "user"))
        assert role is not None
        user = User(
            name="Profile owner",
            email=email,
            password_hash="not-a-real-hash",
            role=role,
        )
        session.add(user)
        session.flush()

        storage_key = storage.generate_key(user.id, document_id, "pdf")
        storage.save(storage_key, BytesIO(content))

        document = ProfileDocument(
            id=document_id,
            user_id=user.id,
            original_file_name="profile-visuals.pdf",
            file_type="pdf",
            mime_type="application/pdf",
            file_size=len(content),
            file_hash=hashlib.sha256(content).hexdigest(),
            storage_provider=storage.provider,
            storage_key=storage_key,
            status="ready",
        )
        session.add(document)
        session.flush()

        for index in range(pending_visuals + described_visuals):
            page = ProfileDocumentPage(
                document_id=document_id,
                user_id=user.id,
                content_index=index,
                page_number=index + 1,
                raw_text=f"Page {index + 1} body text.",
                text=f"Page {index + 1} body text.",
                extraction_method="native",
                raw_extraction_method="native",
                has_images=True,
                has_visual_content=True,
                visual_analysis_status=(
                    "completed" if index < described_visuals else "pending"
                ),
            )
            page.visuals = [
                ProfileDocumentVisual(
                    visual_index=0,
                    visual_type="figure",
                    source="image",
                    bbox_x0=30.0,
                    bbox_y0=50.0,
                    bbox_x1=270.0,
                    bbox_y1=270.0,
                    description=(
                        f"A described figure on page {index + 1}."
                        if index < described_visuals
                        else None
                    ),
                    analysis_status=(
                        "succeeded" if index < described_visuals else "pending"
                    ),
                )
            ]
            session.add(page)
        session.commit()
        return ReadyProfileDocument(
            document_id=document_id,
            user_id=user.id,
            storage=storage,
            storage_key=storage_key,
        )


def profile_describe_job_count(session_factory, document_id):
    with session_factory() as session:
        return session.scalar(
            select(func.count())
            .select_from(ProfileProcessingJob)
            .where(
                ProfileProcessingJob.document_id == document_id,
                ProfileProcessingJob.job_type == JOB_TYPE_DESCRIBE_VISUALS,
            )
        )


def test_claiming_a_profile_describe_job_leaves_the_document_ready(
    session_factory, tmp_path
):
    ready = seed_ready_profile_document(session_factory, tmp_path)
    queued_at = datetime.now(timezone.utc)

    with session_factory() as session:
        document = session.get(ProfileDocument, ready.document_id)
        enqueue_profile_describe_visuals_job(session, document, now=queued_at)
        session.commit()

    with session_factory() as session:
        claim = claim_next_profile_describe_job(
            session,
            "profile-describe-worker",
            ready.storage.provider,
            60,
            now=queued_at + timedelta(seconds=1),
        )

    assert claim is not None
    assert claim.job_type == JOB_TYPE_DESCRIBE_VISUALS
    assert claim.document_id == ready.document_id

    with session_factory() as session:
        assert session.get(ProfileDocument, ready.document_id).status == "ready"


def test_the_profile_extract_claim_ignores_describe_jobs(session_factory, tmp_path):
    ready = seed_ready_profile_document(session_factory, tmp_path)
    queued_at = datetime.now(timezone.utc)

    with session_factory() as session:
        document = session.get(ProfileDocument, ready.document_id)
        enqueue_profile_describe_visuals_job(session, document, now=queued_at)
        session.commit()

    with session_factory() as session:
        claim = claim_next_profile_job(
            session,
            "profile-worker",
            ready.storage.provider,
            60,
            now=queued_at + timedelta(seconds=1),
        )

    assert claim is None


def test_a_profile_visual_is_checkpointed_on_its_own(session_factory, tmp_path):
    ready = seed_ready_profile_document(session_factory, tmp_path)
    queued_at = datetime.now(timezone.utc)

    with session_factory() as session:
        document = session.get(ProfileDocument, ready.document_id)
        enqueue_profile_describe_visuals_job(session, document, now=queued_at)
        session.commit()
    with session_factory() as session:
        claim = claim_next_profile_describe_job(
            session,
            "profile-describe-worker",
            ready.storage.provider,
            60,
            now=queued_at + timedelta(seconds=1),
        )

    with session_factory() as session:
        recorded = record_profile_visual_description(
            session,
            claim.id,
            claim.claim_token,
            page_number=1,
            visual_index=0,
            visual_type="diagram",
            description="A labelled profile diagram.",
        )

    assert recorded is True

    with session_factory() as session:
        visuals = session.scalars(
            select(ProfileDocumentVisual)
            .join(
                ProfileDocumentPage,
                ProfileDocumentPage.id == ProfileDocumentVisual.page_id,
            )
            .where(ProfileDocumentPage.document_id == ready.document_id)
            .order_by(ProfileDocumentPage.page_number)
        ).all()
        assert visuals[0].analysis_status == "succeeded"
        assert visuals[0].description == "A labelled profile diagram."
        assert visuals[1].analysis_status == "pending"


def test_a_failed_profile_describe_job_leaves_the_document_ready(
    session_factory, tmp_path
):
    ready = seed_ready_profile_document(session_factory, tmp_path)
    queued_at = datetime.now(timezone.utc)

    with session_factory() as session:
        document = session.get(ProfileDocument, ready.document_id)
        enqueue_profile_describe_visuals_job(session, document, now=queued_at)
        session.commit()
    with session_factory() as session:
        claim = claim_next_profile_describe_job(
            session,
            "profile-describe-worker",
            ready.storage.provider,
            60,
            now=queued_at + timedelta(seconds=1),
        )

    with session_factory() as session:
        requeued = fail_profile_describe_job(
            session,
            claim.id,
            claim.claim_token,
            "IMAGE_UNDERSTANDING_FAILED",
            "The vision provider is unavailable.",
            retryable=True,
            now=queued_at + timedelta(seconds=5),
        )

    assert requeued is True

    with session_factory() as session:
        document = session.get(ProfileDocument, ready.document_id)
        assert document.status == "ready"
        assert document.processing_error is None


def test_the_sweep_queues_profile_documents_too(session_factory, tmp_path):
    ready = seed_ready_profile_document(session_factory, tmp_path)

    with session_factory() as session:
        assert sweep_profile_documents_needing_visual_description(session) == 1
    with session_factory() as session:
        assert sweep_profile_documents_needing_visual_description(session) == 0

    assert profile_describe_job_count(session_factory, ready.document_id) == 1


def test_a_described_profile_document_is_never_swept(session_factory, tmp_path):
    ready = seed_ready_profile_document(
        session_factory, tmp_path, pending_visuals=0, described_visuals=2
    )

    with session_factory() as session:
        assert sweep_profile_documents_needing_visual_description(session) == 0

    assert profile_describe_job_count(session_factory, ready.document_id) == 0


def test_a_profile_document_reports_its_visual_analysis_rollup(
    session_factory, tmp_path
):
    ready = seed_ready_profile_document(session_factory, tmp_path)

    with session_factory() as session:
        document = session.scalar(
            select(ProfileDocument)
            .options(selectinload(ProfileDocument.pages))
            .where(ProfileDocument.id == ready.document_id)
        )
        assert document.visual_analysis_status == "pending"

    ready_described = seed_ready_profile_document(
        session_factory,
        tmp_path,
        email="profile-described@example.com",
        pending_visuals=0,
        described_visuals=2,
    )
    with session_factory() as session:
        document = session.scalar(
            select(ProfileDocument)
            .options(selectinload(ProfileDocument.pages))
            .where(ProfileDocument.id == ready_described.document_id)
        )
        assert document.visual_analysis_status == "completed"


def test_retrying_a_failed_profile_document_rearms_its_visual_description(
    session_factory, tmp_path
):
    ready = seed_ready_profile_document(session_factory, tmp_path)
    with session_factory() as session:
        document = session.get(ProfileDocument, ready.document_id)
        enqueue_profile_describe_visuals_job(session, document)
        session.commit()

    with session_factory() as session:
        job = session.scalar(
            select(ProfileProcessingJob).where(
                ProfileProcessingJob.document_id == ready.document_id,
                ProfileProcessingJob.job_type == JOB_TYPE_EXTRACT_DOCUMENT,
            )
        )
        if job is None:
            job = ProfileProcessingJob(
                document_id=ready.document_id,
                user_id=ready.user_id,
                job_type=JOB_TYPE_EXTRACT_DOCUMENT,
                max_attempts=3,
                available_at=datetime.now(timezone.utc),
            )
            session.add(job)
        job.status = JOB_STATUS_FAILED
        job.finished_at = datetime.now(timezone.utc)
        job.last_error_code = "OCR_REQUIRED"
        job.attempt_count = 1
        session.get(ProfileDocument, ready.document_id).status = "failed"
        session.commit()

    with session_factory() as session:
        retry_failed_profile_job(session, ready.document_id, ready.user_id)

    assert profile_describe_job_count(session_factory, ready.document_id) == 0
