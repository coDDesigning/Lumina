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
    extract_document_chunks,
)
from services.processing_jobs import (
    ClaimedJob,
    ChunkData,
    claim_next_job,
    complete_job,
    enqueue_document_job,
    fail_job,
    heartbeat_job,
    recover_expired_jobs,
)
from storage.local import LocalStorage
from workers import document_processor
from workers.document_processor import _extract_with_timeout, process_next_job


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
            status="pending",
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
        assert document is not None
        assert job is not None
        assert document.status == "completed"
        assert job.status == JOB_STATUS_SUCCEEDED
        assert job.attempt_count == 1
        assert job.claim_token is None
        assert [chunk.text for chunk in chunks] == ["Durable processing notes"]


def test_image_pdf_fails_permanently_then_can_be_retried(session_factory, tmp_path):
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
        assert job.last_error_code == "OCR_REQUIRED"
        assert job.finished_at is not None

    with session_factory() as session:
        document, job = DocumentService.retry_document(
            session,
            queued.document_id,
            queued.course_id,
        )
        assert document.status == "pending"
        assert job.status == JOB_STATUS_QUEUED
        assert job.attempt_count == 0
        assert job.last_error_code is None


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


def test_extraction_preserves_pdf_pages_and_enforces_integrity(tmp_path):
    storage = LocalStorage(tmp_path / "extract", namespace="worker")
    pdf = pymupdf.open()
    first = pdf.new_page()
    first.insert_text((72, 72), "First page")
    second = pdf.new_page()
    second.insert_text((72, 72), "Second page")
    content = pdf.tobytes()
    pdf.close()
    key = storage.generate_key(1, uuid4(), "pdf")
    storage.save(key, BytesIO(content))

    chunks = extract_document_chunks(
        storage,
        storage_provider=storage.provider,
        storage_key=key,
        expected_hash=hashlib.sha256(content).hexdigest(),
        expected_size=len(content),
        file_type="pdf",
    )
    assert [chunk.page_number for chunk in chunks] == [1, 2]

    with pytest.raises(DocumentProcessingError) as error:
        extract_document_chunks(
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
        extract_document_chunks(
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
