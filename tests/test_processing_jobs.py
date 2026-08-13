import hashlib
import os
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path
from uuid import UUID, uuid4

import pymupdf
import pytest
from sqlalchemy import func, select

from backend.app.models import (
    JOB_STATUS_FAILED,
    JOB_STATUS_QUEUED,
    JOB_STATUS_RUNNING,
    JOB_STATUS_SUCCEEDED,
    Course,
    DocumentChunk,
    DocumentPage,
    DocumentVisual,
    ProcessingJob,
    Role,
    UploadedDocument,
    User,
)
from schemas.course import CourseUpdate
from services import document_extraction
from services.course import CourseService
from services.document import DocumentService
from services.document_extraction import (
    DocumentProcessingError,
    extract_document,
)
from services.processing_jobs import (
    ClaimedJob,
    ChunkData,
    PageData,
    VisualData,
    claim_next_job,
    complete_job,
    enqueue_document_job,
    fail_job,
    heartbeat_job,
    recover_expired_jobs,
    replace_document_pages,
    update_job_stage,
)
from storage.local import LocalStorage
from workers import document_processor
from workers.document_processor import (
    _document_data_from_process_result,
    _extract_with_timeout,
    process_next_job,
)


@dataclass(frozen=True, slots=True)
class QueuedDocument:
    document_id: UUID
    job_id: int
    course_id: int
    available_at: datetime
    storage: LocalStorage


class SlowStorage:
    provider = "slow:test"

    def open(self, _key: str):
        time.sleep(5)
        return BytesIO(b"eventual content")


class CrashingStorage:
    provider = "crash:test"

    def open(self, _key: str):
        os._exit(1)


class ImmediateStorage:
    provider = "immediate:test"

    def __init__(self, content: bytes) -> None:
        self.content = content

    def open(self, _key: str):
        return BytesIO(self.content)


def _queue_document(
    session_factory,
    tmp_path: Path,
    *,
    content: bytes = b"Durable processing notes",
    file_type: str = "txt",
    max_attempts: int = 3,
) -> QueuedDocument:
    storage = LocalStorage(tmp_path / "worker-uploads", namespace="worker")
    document_id = uuid4()

    with session_factory() as session:
        role = session.scalar(select(Role).where(Role.name == "user"))
        assert role is not None
        user = User(
            name="Worker owner",
            email="worker@example.com",
            password_hash="not-a-real-hash",
            role=role,
        )
        course = Course(
            owner=user,
            title="Worker course",
            description=None,
            instructor="Worker owner",
            price=0,
        )
        session.add(course)
        session.flush()

        storage_key = storage.generate_key(course.id, document_id, file_type)
        storage.save(storage_key, BytesIO(content))

        document = UploadedDocument(
            id=document_id,
            original_file_name=f"notes.{file_type}",
            file_type=file_type,
            mime_type="application/pdf" if file_type == "pdf" else "text/plain",
            file_size=len(content),
            file_hash=hashlib.sha256(content).hexdigest(),
            uploader=user,
            course=course,
            storage_provider=storage.provider,
            storage_key=storage_key,
            status="uploaded",
        )
        session.add(document)
        session.flush()
        job = enqueue_document_job(
            session,
            document,
            max_attempts=max_attempts,
        )
        session.commit()
        return QueuedDocument(
            document_id=document.id,
            job_id=job.id,
            course_id=course.id,
            available_at=job.available_at,
            storage=storage,
        )


def _image_pdf() -> bytes:
    pdf = pymupdf.open()
    page = pdf.new_page()
    pixel = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 2, 2), False)
    pixel.clear_with(255)
    page.insert_image(pymupdf.Rect(72, 72, 144, 144), stream=pixel.tobytes("png"))
    content = pdf.tobytes()
    pdf.close()
    return content


def test_worker_processes_text_into_canonical_chunks(session_factory, tmp_path):
    queued = _queue_document(session_factory, tmp_path)

    assert process_next_job(
        session_factory=session_factory,
        storage=queued.storage,
        worker_id="test-worker",
        lease_seconds=60,
    )
    assert not process_next_job(
        session_factory=session_factory,
        storage=queued.storage,
        worker_id="test-worker",
        lease_seconds=60,
    )

    with session_factory() as session:
        document = session.get(UploadedDocument, queued.document_id)
        job = session.get(ProcessingJob, queued.job_id)
        chunks = session.scalars(
            select(DocumentChunk).order_by(DocumentChunk.chunk_index)
        ).all()
        pages = session.scalars(
            select(DocumentPage).order_by(DocumentPage.content_index)
        ).all()
        assert document is not None
        assert job is not None
        assert document.status == "ready"
        assert job.status == JOB_STATUS_SUCCEEDED
        assert job.attempt_count == 1
        assert job.claim_token is None
        assert job.processing_stage is None
        assert job.failed_stage is None
        assert [chunk.text for chunk in chunks] == ["Durable processing notes"]
        assert len(pages) == 1
        assert pages[0].page_number is None
        assert pages[0].raw_text == "Durable processing notes"
        assert pages[0].text == "Durable processing notes"
        assert pages[0].raw_extraction_method == "decoded"
        assert pages[0].extraction_method == "decoded"
        assert pages[0].has_images is False
        assert pages[0].needs_ocr is False
        assert pages[0].ocr_status == "not_required"
        assert pages[0].has_visual_content is False
        assert pages[0].visual_analysis_status == "not_applicable"
        assert pages[0].visuals == []


def test_image_pdf_fails_permanently_then_can_be_retried(
    session_factory,
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("OCR_LANGUAGE", "lumina_missing_language")
    queued = _queue_document(
        session_factory,
        tmp_path,
        content=_image_pdf(),
        file_type="pdf",
    )

    assert process_next_job(
        session_factory=session_factory,
        storage=queued.storage,
        worker_id="ocr-worker",
        lease_seconds=60,
    )

    with session_factory() as session:
        document = session.get(UploadedDocument, queued.document_id)
        job = session.get(ProcessingJob, queued.job_id)
        assert document is not None
        assert job is not None
        assert document.status == "failed"
        assert job.status == JOB_STATUS_FAILED
        assert job.last_error_code == "OCR_UNAVAILABLE"
        assert job.failed_stage == "running_ocr"
        assert job.finished_at is not None
        page = session.scalar(select(DocumentPage))
        assert page is not None
        assert page.page_number == 1
        assert page.raw_text == ""
        assert page.text == ""
        assert page.has_images is True
        assert page.has_visual_content is True
        assert page.needs_ocr is True
        assert page.ocr_status == "pending"
        assert page.visual_analysis_status == "pending"
        visual = session.scalar(select(DocumentVisual))
        assert visual is not None
        assert visual.page_id == page.id
        assert visual.visual_index == 0
        assert visual.visual_type == "figure"
        assert visual.source == "image"
        assert visual.analysis_status == "pending"

    with session_factory() as session:
        document, job = DocumentService.retry_document(
            session,
            queued.document_id,
            queued.course_id,
        )
        assert document.status == "uploaded"
        assert job.status == JOB_STATUS_QUEUED
        assert job.attempt_count == 0
        assert job.last_error_code is None
        assert job.failed_stage is None


def test_corrupted_pdf_fails_worker_without_partial_content(session_factory, tmp_path):
    queued = _queue_document(
        session_factory,
        tmp_path,
        content=b"%PDF-1.7\ntruncated",
        file_type="pdf",
    )

    assert process_next_job(
        session_factory=session_factory,
        storage=queued.storage,
        worker_id="corrupt-pdf-worker",
        lease_seconds=60,
    )

    with session_factory() as session:
        document = session.get(UploadedDocument, queued.document_id)
        job = session.get(ProcessingJob, queued.job_id)
        assert document is not None
        assert job is not None
        assert document.status == "failed"
        assert job.status == JOB_STATUS_FAILED
        assert job.last_error_code == "CORRUPTED_PDF"
        assert job.failed_stage == "validating"
        assert session.scalar(select(func.count(DocumentPage.id))) == 0
        assert session.scalar(select(func.count(DocumentChunk.id))) == 0


def test_worker_persists_exact_raw_markdown_before_cleaning(session_factory, tmp_path):
    content = b"# Heading  \r\n\r\nParagraph with a hard break.  \r\n"
    queued = _queue_document(
        session_factory,
        tmp_path,
        content=content,
        file_type="markdown",
    )

    assert process_next_job(
        session_factory=session_factory,
        storage=queued.storage,
        worker_id="markdown-worker",
        lease_seconds=60,
    )

    with session_factory() as session:
        page = session.scalar(select(DocumentPage))
        chunk = session.scalar(select(DocumentChunk))
        assert page is not None
        assert chunk is not None
        assert page.raw_text == content.decode("utf-8")
        assert page.text == "# Heading  \n\nParagraph with a hard break.  "
        assert page.page_number is None
        assert chunk.text == "# Heading  \n\nParagraph with a hard break.  "


def test_processing_stage_transitions_are_ordered_and_claim_fenced(
    session_factory,
    tmp_path,
):
    queued = _queue_document(session_factory, tmp_path)
    with session_factory() as session:
        claim = claim_next_job(
            session,
            "stage-worker",
            queued.storage.provider,
            60,
            now=queued.available_at + timedelta(seconds=1),
        )
    assert claim is not None

    with session_factory() as session:
        job = session.get(ProcessingJob, queued.job_id)
        assert job is not None
        assert job.processing_stage == "validating"
    with session_factory() as session:
        assert not update_job_stage(
            session,
            claim.id,
            "wrong-claim",
            "extracting_text",
            now=queued.available_at + timedelta(seconds=2),
        )
    with session_factory() as session:
        assert update_job_stage(
            session,
            claim.id,
            claim.claim_token,
            "extracting_text",
            now=queued.available_at + timedelta(seconds=2),
        )
    with session_factory() as session:
        assert not update_job_stage(
            session,
            claim.id,
            claim.claim_token,
            "chunking",
            now=queued.available_at + timedelta(seconds=3),
        )


def test_sqlite_claim_is_exclusive_and_completion_is_fenced(session_factory, tmp_path):
    queued = _queue_document(session_factory, tmp_path)
    claim_time = queued.available_at + timedelta(seconds=1)

    def claim(worker_id: str):
        with session_factory() as session:
            return claim_next_job(
                session,
                worker_id,
                queued.storage.provider,
                60,
                now=claim_time,
            )

    with ThreadPoolExecutor(max_workers=2) as executor:
        claims = list(executor.map(claim, ["worker-a", "worker-b"]))

    winners = [claim for claim in claims if claim is not None]
    assert len(winners) == 1
    winner = winners[0]

    with session_factory() as session:
        assert not heartbeat_job(
            session,
            winner.id,
            "wrong-token",
            60,
            now=claim_time + timedelta(seconds=5),
        )
    with session_factory() as session:
        assert heartbeat_job(
            session,
            winner.id,
            winner.claim_token,
            60,
            now=claim_time + timedelta(seconds=5),
        )
    with session_factory() as session:
        assert not complete_job(
            session,
            winner.id,
            "stale-token",
            [ChunkData("Stale")],
            now=claim_time + timedelta(seconds=10),
        )
    with session_factory() as session:
        assert complete_job(
            session,
            winner.id,
            winner.claim_token,
            [ChunkData("Canonical")],
            now=claim_time + timedelta(seconds=10),
        )


def test_raw_pages_are_claim_fenced_and_atomically_replaced(session_factory, tmp_path):
    queued = _queue_document(session_factory, tmp_path)
    with session_factory() as session:
        claim = claim_next_job(
            session,
            "page-worker",
            queued.storage.provider,
            60,
            now=queued.available_at + timedelta(seconds=1),
        )
    assert claim is not None
    with session_factory() as session:
        assert update_job_stage(
            session,
            claim.id,
            claim.claim_token,
            "extracting_text",
            now=queued.available_at + timedelta(seconds=2),
        )

    first_pages = [
        PageData(
            content_index=0,
            text="First extraction",
            raw_text="First extraction",
            page_number=None,
            extraction_method="decoded",
            has_images=False,
            needs_ocr=False,
        )
    ]
    with session_factory() as session:
        assert replace_document_pages(
            session,
            claim.id,
            claim.claim_token,
            first_pages,
            now=queued.available_at + timedelta(seconds=3),
        )
    with session_factory() as session:
        assert not replace_document_pages(
            session,
            claim.id,
            "stale-claim",
            [
                replace(
                    first_pages[0],
                    raw_text="Stale extraction",
                    text="Stale extraction",
                )
            ],
            now=queued.available_at + timedelta(seconds=4),
        )
    with session_factory() as session:
        assert replace_document_pages(
            session,
            claim.id,
            claim.claim_token,
            [
                replace(
                    first_pages[0],
                    raw_text="Replacement extraction",
                    text="Replacement extraction",
                )
            ],
            now=queued.available_at + timedelta(seconds=5),
        )

    with session_factory() as session:
        pages = session.scalars(select(DocumentPage)).all()
        assert len(pages) == 1
        assert pages[0].raw_text == "Replacement extraction"
        assert pages[0].text == "Replacement extraction"
        assert pages[0].raw_extraction_method == "decoded"

    with session_factory() as session:
        assert (
            fail_job(
                session,
                claim.id,
                claim.claim_token,
                error_code="LATER_STAGE_FAILED",
                error_message="Later processing failed",
                retryable=False,
                now=queued.available_at + timedelta(seconds=6),
            )
            == JOB_STATUS_FAILED
        )
    with session_factory() as session:
        persisted = session.scalar(select(DocumentPage))
        assert persisted is not None
        assert persisted.raw_text == "Replacement extraction"
        assert persisted.text == "Replacement extraction"

    with session_factory() as session:
        DocumentService.retry_document(session, queued.document_id, queued.course_id)
    with session_factory() as session:
        retry_claim = claim_next_job(
            session,
            "retry-page-worker",
            queued.storage.provider,
            60,
            now=queued.available_at + timedelta(seconds=7),
        )
    assert retry_claim is not None
    with session_factory() as session:
        assert update_job_stage(
            session,
            retry_claim.id,
            retry_claim.claim_token,
            "extracting_text",
            now=queued.available_at + timedelta(seconds=8),
        )
    with session_factory() as session:
        assert replace_document_pages(
            session,
            retry_claim.id,
            retry_claim.claim_token,
            [
                replace(
                    first_pages[0],
                    raw_text="Successful retry extraction",
                    text="Successful retry extraction",
                )
            ],
            now=queued.available_at + timedelta(seconds=9),
        )
    with session_factory() as session:
        pages = session.scalars(select(DocumentPage)).all()
        assert len(pages) == 1
        assert pages[0].raw_text == "Successful retry extraction"
        assert pages[0].text == "Successful retry extraction"


def test_completion_fences_and_replaces_raw_pages_with_enriched_content(
    session_factory,
    tmp_path,
):
    queued = _queue_document(session_factory, tmp_path)
    claim_time = queued.available_at + timedelta(seconds=1)
    with session_factory() as session:
        claim = claim_next_job(
            session,
            "enrichment-worker",
            queued.storage.provider,
            60,
            now=claim_time,
        )
    assert claim is not None
    with session_factory() as session:
        assert update_job_stage(
            session,
            claim.id,
            claim.claim_token,
            "extracting_text",
            now=claim_time + timedelta(seconds=1),
        )

    raw_pages = [
        PageData(
            content_index=0,
            raw_text="Sparse native text",
            text="Sparse native text",
            page_number=1,
            raw_extraction_method="native",
            extraction_method="native",
            has_images=True,
            needs_ocr=True,
            ocr_status="pending",
            has_visual_content=True,
            visual_analysis_status="pending",
            visuals=(
                VisualData(
                    visual_index=0,
                    visual_type="figure",
                    source="image",
                    bbox=(10.0, 20.0, 110.0, 220.0),
                ),
            ),
        )
    ]
    with session_factory() as session:
        assert replace_document_pages(
            session,
            claim.id,
            claim.claim_token,
            raw_pages,
            now=claim_time + timedelta(seconds=2),
        )

    enriched_pages = [
        PageData(
            content_index=0,
            raw_text="Sparse\x00 native text",
            text=("Recognized effective text\n\n[Chart]\nRevenue grew year over year."),
            page_number=1,
            raw_extraction_method="native",
            extraction_method="ocr",
            has_images=True,
            needs_ocr=False,
            ocr_status="succeeded",
            has_visual_content=True,
            visual_analysis_status="completed",
            visuals=(
                VisualData(
                    visual_index=0,
                    visual_type="chart",
                    source="image",
                    bbox=(10.0, 20.0, 110.0, 220.0),
                    description="Revenue grew\x00 year over year.",
                    analysis_status="succeeded",
                ),
            ),
        )
    ]
    chunks = [
        ChunkData(
            "Recognized effective text\n\n[Chart]\nRevenue grew year over year.",
            page_number=1,
        )
    ]
    with session_factory() as session:
        assert not complete_job(
            session,
            claim.id,
            "stale-token",
            chunks,
            enriched_pages,
            now=claim_time + timedelta(seconds=3),
        )
    with session_factory() as session:
        page = session.scalar(select(DocumentPage))
        visual = session.scalar(select(DocumentVisual))
        job = session.get(ProcessingJob, queued.job_id)
        assert page is not None
        assert visual is not None
        assert job is not None
        assert page.raw_text == "Sparse native text"
        assert page.text == "Sparse native text"
        assert page.extraction_method == "native"
        assert visual.visual_type == "figure"
        assert visual.analysis_status == "pending"
        assert job.status == JOB_STATUS_RUNNING

    with session_factory() as session:
        assert complete_job(
            session,
            claim.id,
            claim.claim_token,
            chunks,
            enriched_pages,
            now=claim_time + timedelta(seconds=4),
        )

    with session_factory() as session:
        page = session.scalar(select(DocumentPage))
        visual = session.scalar(select(DocumentVisual))
        chunk = session.scalar(select(DocumentChunk))
        assert page is not None
        assert visual is not None
        assert chunk is not None
        assert page.raw_text == "Sparse native text"
        assert page.text == (
            "Recognized effective text\n\n[Chart]\nRevenue grew year over year."
        )
        assert page.raw_extraction_method == "native"
        assert page.extraction_method == "ocr"
        assert page.needs_ocr is False
        assert page.ocr_status == "succeeded"
        assert page.has_visual_content is True
        assert page.visual_analysis_status == "completed"
        assert visual.page_id == page.id
        assert visual.visual_index == 0
        assert visual.visual_type == "chart"
        assert visual.source == "image"
        assert (
            visual.bbox_x0,
            visual.bbox_y0,
            visual.bbox_x1,
            visual.bbox_y1,
        ) == (10.0, 20.0, 110.0, 220.0)
        assert visual.description == "Revenue grew year over year."
        assert visual.analysis_status == "succeeded"
        assert visual.error_code is None
        assert chunk.text == chunks[0].text
        assert session.scalar(select(func.count(DocumentPage.id))) == 1
        assert session.scalar(select(func.count(DocumentVisual.id))) == 1
        assert session.scalar(select(func.count(DocumentChunk.id))) == 1


def test_raw_page_persistence_removes_postgresql_unsupported_nuls(
    session_factory,
    tmp_path,
):
    queued = _queue_document(session_factory, tmp_path)
    with session_factory() as session:
        claim = claim_next_job(
            session,
            "nul-page-worker",
            queued.storage.provider,
            60,
            now=queued.available_at + timedelta(seconds=1),
        )
    assert claim is not None
    with session_factory() as session:
        assert update_job_stage(
            session,
            claim.id,
            claim.claim_token,
            "extracting_text",
            now=queued.available_at + timedelta(seconds=2),
        )
    with session_factory() as session:
        assert replace_document_pages(
            session,
            claim.id,
            claim.claim_token,
            [
                PageData(
                    content_index=0,
                    text="Before\x00After",
                    raw_text="Raw\x00Text",
                    page_number=1,
                    extraction_method="native",
                    has_images=False,
                    needs_ocr=False,
                )
            ],
            now=queued.available_at + timedelta(seconds=3),
        )
    with session_factory() as session:
        page = session.scalar(select(DocumentPage))
        assert page is not None
        assert page.raw_text == "RawText"
        assert page.text == "BeforeAfter"


def test_raw_page_persistence_rejects_an_empty_result(session_factory, tmp_path):
    queued = _queue_document(session_factory, tmp_path)

    with pytest.raises(ValueError, match="at least one page"):
        with session_factory() as session:
            replace_document_pages(session, queued.job_id, "claim", [])


@pytest.mark.parametrize(
    ("page", "message"),
    [
        (
            PageData(0, "text", None, "decoded", False, False, raw_text=1),
            "raw text must be a string",
        ),
        (
            PageData(0, "text", None, "ocr", False, False),
            "extraction method is unsupported",
        ),
        (
            PageData(
                0,
                "text",
                None,
                "decoded",
                False,
                False,
                ocr_status="unknown",
            ),
            "OCR status is unsupported",
        ),
        (
            PageData(
                0,
                "text",
                None,
                "decoded",
                False,
                False,
                visual_analysis_status="unknown",
            ),
            "visual status is unsupported",
        ),
        (
            PageData(
                0,
                "text",
                1,
                "native",
                True,
                False,
                has_visual_content=False,
                visuals=(VisualData(0, "figure", "image", (0.0, 0.0, 10.0, 10.0)),),
            ),
            "visual detection flag is inconsistent",
        ),
        (
            PageData(
                0,
                "text",
                1,
                "native",
                True,
                False,
                has_visual_content=True,
                visuals=(VisualData(1, "figure", "image", (0.0, 0.0, 10.0, 10.0)),),
            ),
            "visual indexes must be contiguous",
        ),
        (
            PageData(
                0,
                "text",
                1,
                "native",
                True,
                False,
                has_visual_content=True,
                visuals=(VisualData(0, "figure", "image", (0.0, 0.0, 0.0, 10.0)),),
            ),
            "visual bounding box is invalid",
        ),
        (
            PageData(
                0,
                "text",
                1,
                "native",
                True,
                False,
                has_visual_content=True,
                visuals=(
                    VisualData(
                        0,
                        "figure",
                        "image",
                        (0.0, 0.0, 10.0, 10.0),
                        description="Premature description",
                    ),
                ),
            ),
            "visual description is invalid",
        ),
        (
            PageData(
                0,
                "text",
                1,
                "native",
                True,
                False,
                has_visual_content=True,
                visuals=(
                    VisualData(
                        0,
                        "figure",
                        "image",
                        (0.0, 0.0, 10.0, 10.0),
                        analysis_status="failed",
                    ),
                ),
            ),
            "require an error code",
        ),
    ],
    ids=[
        "raw-text-type",
        "raw-ocr-method",
        "ocr-status",
        "visual-status",
        "visual-flag",
        "visual-index",
        "visual-bbox",
        "visual-description-status",
        "visual-failure-code",
    ],
)
def test_raw_page_persistence_validates_enrichment_data(
    session_factory,
    tmp_path,
    page,
    message,
):
    queued = _queue_document(session_factory, tmp_path)

    with pytest.raises(ValueError, match=message):
        with session_factory() as session:
            replace_document_pages(session, queued.job_id, "claim", [page])


def test_transient_failure_requeues_then_exhausts_attempts(session_factory, tmp_path):
    queued = _queue_document(session_factory, tmp_path, max_attempts=2)
    first_claim_at = queued.available_at + timedelta(seconds=1)
    with session_factory() as session:
        first = claim_next_job(
            session,
            "worker",
            queued.storage.provider,
            30,
            now=first_claim_at,
        )
    assert first is not None

    first_failure_at = first_claim_at + timedelta(seconds=1)
    with session_factory() as session:
        assert (
            fail_job(
                session,
                first.id,
                first.claim_token,
                error_code="TEMPORARY_PROVIDER_ERROR",
                error_message="temporary   provider\nerror",
                retryable=True,
                retry_delay_seconds=10,
                now=first_failure_at,
            )
            == JOB_STATUS_QUEUED
        )

    with session_factory() as session:
        assert (
            claim_next_job(
                session,
                "worker",
                queued.storage.provider,
                30,
                now=first_failure_at + timedelta(seconds=9),
            )
            is None
        )
    with session_factory() as session:
        second = claim_next_job(
            session,
            "worker",
            queued.storage.provider,
            30,
            now=first_failure_at + timedelta(seconds=10),
        )
    assert second is not None
    assert second.attempt_count == 2

    with session_factory() as session:
        assert (
            fail_job(
                session,
                second.id,
                second.claim_token,
                error_code="TEMPORARY_PROVIDER_ERROR",
                error_message="still unavailable",
                retryable=True,
                now=first_failure_at + timedelta(seconds=11),
            )
            == JOB_STATUS_FAILED
        )

    with session_factory() as session:
        job = session.get(ProcessingJob, queued.job_id)
        document = session.get(UploadedDocument, queued.document_id)
        assert job is not None
        assert document is not None
        assert job.status == JOB_STATUS_FAILED
        assert job.last_error_message == "still unavailable"
        assert document.status == "failed"


def test_terminal_cleaning_failure_preserves_raw_checkpoint(session_factory, tmp_path):
    queued = _queue_document(session_factory, tmp_path, max_attempts=1)
    claim_time = queued.available_at + timedelta(seconds=1)
    with session_factory() as session:
        claim = claim_next_job(
            session,
            "cleaning-worker",
            queued.storage.provider,
            60,
            now=claim_time,
        )
    assert claim is not None

    with session_factory() as session:
        assert update_job_stage(
            session,
            claim.id,
            claim.claim_token,
            "extracting_text",
            now=claim_time + timedelta(seconds=1),
        )
    with session_factory() as session:
        assert replace_document_pages(
            session,
            claim.id,
            claim.claim_token,
            [PageData(0, "Raw checkpoint", None, "decoded", False, False)],
            now=claim_time + timedelta(seconds=2),
        )
    with session_factory() as session:
        assert update_job_stage(
            session,
            claim.id,
            claim.claim_token,
            "cleaning_text",
            now=claim_time + timedelta(seconds=3),
        )
    with session_factory() as session:
        assert (
            fail_job(
                session,
                claim.id,
                claim.claim_token,
                error_code="TEXT_CLEANING_FAILED",
                error_message=(
                    "The extracted document content could not be prepared for processing."
                ),
                retryable=True,
                now=claim_time + timedelta(seconds=4),
            )
            == JOB_STATUS_FAILED
        )

    with session_factory() as session:
        document = session.get(UploadedDocument, queued.document_id)
        job = session.get(ProcessingJob, queued.job_id)
        page = session.scalar(select(DocumentPage))
        assert document is not None
        assert job is not None
        assert page is not None
        assert document.status == "failed"
        assert job.last_error_code == "TEXT_CLEANING_FAILED"
        assert job.failed_stage == "cleaning_text"
        assert page.raw_text == "Raw checkpoint"
        assert page.text == "Raw checkpoint"


def test_expired_leases_are_requeued_then_failed(session_factory, tmp_path):
    queued = _queue_document(session_factory, tmp_path, max_attempts=2)
    first_claim_at = queued.available_at + timedelta(seconds=1)
    with session_factory() as session:
        first = claim_next_job(
            session,
            "worker",
            queued.storage.provider,
            10,
            now=first_claim_at,
        )
    assert first is not None

    with session_factory() as session:
        assert (
            recover_expired_jobs(
                session,
                now=first_claim_at + timedelta(seconds=9),
            )
            == 0
        )
    with session_factory() as session:
        assert (
            recover_expired_jobs(
                session,
                now=first_claim_at + timedelta(seconds=11),
            )
            == 1
        )
    with session_factory() as session:
        assert not complete_job(
            session,
            first.id,
            first.claim_token,
            [ChunkData("Late result")],
            now=first_claim_at + timedelta(seconds=11),
        )

    second_claim_at = first_claim_at + timedelta(seconds=12)
    with session_factory() as session:
        second = claim_next_job(
            session,
            "worker",
            queued.storage.provider,
            10,
            now=second_claim_at,
        )
    assert second is not None
    with session_factory() as session:
        assert (
            recover_expired_jobs(
                session,
                now=second_claim_at + timedelta(seconds=11),
            )
            == 1
        )

    with session_factory() as session:
        job = session.get(ProcessingJob, queued.job_id)
        document = session.get(UploadedDocument, queued.document_id)
        assert job is not None
        assert document is not None
        assert job.status == JOB_STATUS_FAILED
        assert job.last_error_code == "LEASE_EXPIRED"
        assert document.status == "failed"


def test_course_deletion_immediately_fences_running_claim(session_factory, tmp_path):
    queued = _queue_document(session_factory, tmp_path)
    claim_time = queued.available_at + timedelta(seconds=1)
    with session_factory() as session:
        claim = claim_next_job(
            session,
            "worker",
            queued.storage.provider,
            60,
            now=claim_time,
        )
    assert claim is not None

    with session_factory() as session:
        CourseService.soft_delete_course(session, queued.course_id)
    with session_factory() as session:
        assert not complete_job(
            session,
            claim.id,
            claim.claim_token,
            [ChunkData("Late result")],
            now=claim_time + timedelta(seconds=2),
        )
    with session_factory() as session:
        job = session.get(ProcessingJob, queued.job_id)
        document = session.get(UploadedDocument, queued.document_id)
        assert job is not None
        assert document is not None
        assert job.status == JOB_STATUS_FAILED
        assert job.last_error_code == "COURSE_DELETED"
        assert job.claim_token is None
        assert document.status == "failed"


def test_generic_course_update_cannot_bypass_job_fencing(session_factory, tmp_path):
    queued = _queue_document(session_factory, tmp_path)

    with session_factory() as session:
        updated = CourseService.update_course(
            session,
            queued.course_id,
            CourseUpdate(is_deleted=True),
        )
        assert updated.is_deleted is True

    with session_factory() as session:
        job = session.get(ProcessingJob, queued.job_id)
        document = session.get(UploadedDocument, queued.document_id)
        assert job is not None
        assert document is not None
        assert job.status == JOB_STATUS_FAILED
        assert job.last_error_code == "COURSE_DELETED"
        assert document.status == "failed"


def test_completion_failure_rolls_back_chunks_and_state(
    session_factory, tmp_path, monkeypatch
):
    queued = _queue_document(session_factory, tmp_path)
    claim_time = queued.available_at + timedelta(seconds=1)
    with session_factory() as session:
        claim = claim_next_job(
            session,
            "worker",
            queued.storage.provider,
            60,
            now=claim_time,
        )
    assert claim is not None

    session = session_factory()
    try:

        def fail_commit() -> None:
            session.flush()
            raise RuntimeError("injected commit failure")

        monkeypatch.setattr(session, "commit", fail_commit)
        with pytest.raises(RuntimeError, match="injected commit failure"):
            complete_job(
                session,
                claim.id,
                claim.claim_token,
                [ChunkData("Atomic result")],
                [
                    PageData(
                        content_index=0,
                        raw_text="Raw result",
                        text="Effective result",
                        page_number=1,
                        raw_extraction_method="native",
                        extraction_method="native",
                        has_images=True,
                        needs_ocr=False,
                        has_visual_content=True,
                        visual_analysis_status="completed",
                        visuals=(
                            VisualData(
                                visual_index=0,
                                visual_type="diagram",
                                source="image",
                                bbox=(1.0, 2.0, 30.0, 40.0),
                                description="Atomic visual",
                                analysis_status="succeeded",
                            ),
                        ),
                    )
                ],
                now=claim_time + timedelta(seconds=1),
            )
        session.rollback()
    finally:
        session.close()

    with session_factory() as verification:
        job = verification.get(ProcessingJob, queued.job_id)
        document = verification.get(UploadedDocument, queued.document_id)
        assert job is not None
        assert document is not None
        assert job.status == JOB_STATUS_RUNNING
        assert document.status == "processing"
        assert verification.scalar(select(func.count(DocumentChunk.id))) == 0
        assert verification.scalar(select(func.count(DocumentPage.id))) == 0
        assert verification.scalar(select(func.count(DocumentVisual.id))) == 0


def test_worker_contains_finalization_errors_until_lease_recovery(
    session_factory, tmp_path, monkeypatch
):
    queued = _queue_document(session_factory, tmp_path)

    def fail_completion(*_args, **_kwargs):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(document_processor, "complete_job", fail_completion)
    assert process_next_job(
        session_factory=session_factory,
        storage=queued.storage,
        worker_id="resilient-worker",
        lease_seconds=2,
    )

    with session_factory() as session:
        job = session.get(ProcessingJob, queued.job_id)
        assert job is not None
        assert job.status == JOB_STATUS_RUNNING
        assert job.lease_expires_at is not None
        recovery_time = job.lease_expires_at + timedelta(seconds=1)
    with session_factory() as session:
        assert recover_expired_jobs(session, now=recovery_time) == 1


def test_extraction_attempt_timeout_terminates_stuck_subprocess():
    job = ClaimedJob(
        id=1,
        document_id=uuid4(),
        course_id=1,
        claim_token=str(uuid4()),
        attempt_count=1,
        max_attempts=3,
        storage_provider=SlowStorage.provider,
        storage_key="document.txt",
        file_hash="0" * 64,
        file_type="txt",
        file_size=16,
    )

    with pytest.raises(DocumentProcessingError) as error:
        _extract_with_timeout(SlowStorage(), job, timeout_seconds=1)
    assert error.value.code == "PROCESSING_TIMEOUT"
    assert error.value.retryable is True


def test_extraction_process_crash_is_reaped_as_safe_failure():
    job = ClaimedJob(
        id=1,
        document_id=uuid4(),
        course_id=1,
        claim_token=str(uuid4()),
        attempt_count=1,
        max_attempts=3,
        storage_provider=CrashingStorage.provider,
        storage_key="document.txt",
        file_hash="0" * 64,
        file_type="txt",
        file_size=16,
    )

    with pytest.raises(DocumentProcessingError) as error:
        _extract_with_timeout(CrashingStorage(), job, timeout_seconds=5)
    assert error.value.code == "UNEXPECTED_PROCESSING_ERROR"


@pytest.mark.parametrize(
    "result",
    [
        ("unexpected",),
        ("extracted", []),
        ("succeeded", [PageData(0, "raw", None, "decoded", False, False)]),
        ("succeeded", [ChunkData("not a page")], [ChunkData("chunk")]),
        (
            "succeeded",
            [PageData(0, "raw", None, "decoded", False, False)],
            [PageData(0, "not a chunk", None, "decoded", False, False)],
        ),
        ("failed", "CODE", "message", "not-a-boolean"),
        (),
    ],
)
def test_unexpected_child_messages_never_complete_as_document_data(result):
    with pytest.raises(DocumentProcessingError) as error:
        _document_data_from_process_result(result)
    assert error.value.code == "UNEXPECTED_PROCESSING_ERROR"


def test_child_succeeded_message_returns_pages_and_chunks():
    pages = [PageData(0, "effective", None, "decoded", False, False, raw_text="raw")]
    chunks = [ChunkData("effective")]

    assert _document_data_from_process_result(("succeeded", pages, chunks)) == (
        pages,
        chunks,
    )


def test_extraction_persistence_time_counts_toward_attempt_timeout():
    content = b"Deadline bounded extraction"
    storage = ImmediateStorage(content)
    job = ClaimedJob(
        id=1,
        document_id=uuid4(),
        course_id=1,
        claim_token=str(uuid4()),
        attempt_count=1,
        max_attempts=3,
        storage_provider=storage.provider,
        storage_key="document.txt",
        file_hash=hashlib.sha256(content).hexdigest(),
        file_type="txt",
        file_size=len(content),
    )

    with pytest.raises(DocumentProcessingError) as error:
        _extract_with_timeout(
            storage,
            job,
            timeout_seconds=1,
            extraction_callback=lambda _pages, _remaining: time.sleep(1.1),
        )
    assert error.value.code == "PROCESSING_TIMEOUT"


def test_extraction_preserves_pdf_pages_and_enforces_integrity(tmp_path):
    storage = LocalStorage(tmp_path / "extract", namespace="worker")
    pdf = pymupdf.open()
    first = pdf.new_page()
    first.insert_text((72, 72), "First searchable page has course material")
    second = pdf.new_page()
    second.insert_text((72, 72), "Second searchable page has more material")
    content = pdf.tobytes()
    pdf.close()
    key = storage.generate_key(1, uuid4(), "pdf")
    storage.save(key, BytesIO(content))

    result = extract_document(
        storage,
        storage_provider=storage.provider,
        storage_key=key,
        expected_hash=hashlib.sha256(content).hexdigest(),
        expected_size=len(content),
        file_type="pdf",
    )
    assert [page.page_number for page in result.pages] == [1, 2]
    assert [page.extraction_method for page in result.pages] == ["native", "native"]
    assert [chunk.page_number for chunk in result.chunks] == [1, 2]

    with pytest.raises(DocumentProcessingError) as error:
        extract_document(
            storage,
            storage_provider=storage.provider,
            storage_key=key,
            expected_hash="0" * 64,
            expected_size=len(content),
            file_type="pdf",
        )
    assert error.value.code == "STORAGE_HASH_MISMATCH"
    assert error.value.retryable is False


def test_extraction_enforces_configured_text_limit(tmp_path, monkeypatch):
    storage = LocalStorage(tmp_path / "bounded", namespace="worker")
    content = b"Text beyond the configured extraction limit"
    key = storage.generate_key(1, uuid4(), "txt")
    storage.save(key, BytesIO(content))
    monkeypatch.setattr(
        document_extraction,
        "settings",
        replace(document_extraction.settings, max_extracted_characters=5),
    )

    with pytest.raises(DocumentProcessingError) as error:
        extract_document(
            storage,
            storage_provider=storage.provider,
            storage_key=key,
            expected_hash=hashlib.sha256(content).hexdigest(),
            expected_size=len(content),
            file_type="txt",
        )
    assert error.value.code == "EXTRACTED_TEXT_LIMIT_EXCEEDED"


def test_claim_skips_documents_for_another_storage_provider(session_factory, tmp_path):
    queued = _queue_document(session_factory, tmp_path)
    with session_factory() as session:
        assert (
            claim_next_job(
                session,
                "worker",
                "local:another-node",
                60,
                now=queued.available_at + timedelta(seconds=1),
            )
            is None
        )
    with session_factory() as session:
        job = session.get(ProcessingJob, queued.job_id)
        assert job is not None
        assert job.status == JOB_STATUS_QUEUED
