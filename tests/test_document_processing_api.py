from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier
from uuid import UUID

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from backend.app.models import DocumentPage, ProcessingJob, UploadedDocument
from main import app
from services.document import DocumentService
import services.document as document_service
from services.processing_jobs import claim_next_job, fail_job
from utils.exceptions import ConflictException


def _upload(context, content: bytes = b"Queued API document"):
    return context.client.post(
        f"/api/courses/{context.course_id}/documents",
        headers=context.authorization,
        files={"document": ("notes.txt", content, "text/plain")},
    )


def test_upload_atomically_enqueues_one_job_and_status_is_course_scoped(upload_api):
    first = _upload(upload_api)
    duplicate = _upload(upload_api)

    assert first.status_code == 201
    assert duplicate.status_code == 200
    document_id = UUID(first.json()["document"]["id"])
    with upload_api.session_factory() as session:
        job = session.scalar(
            select(ProcessingJob).where(ProcessingJob.document_id == document_id)
        )
        assert job is not None
        assert job.status == "queued"
        assert session.scalar(select(func.count()).select_from(ProcessingJob)) == 1

    response = upload_api.client.get(
        f"/api/courses/{upload_api.course_id}/documents/{document_id}",
        headers=upload_api.authorization,
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["document"]["id"] == str(document_id)
    assert payload["document"]["status"] == "uploaded"
    assert payload["processing_job"]["status"] == "queued"
    assert set(payload["processing_job"]) == {
        "id",
        "status",
        "attempt_count",
        "max_attempts",
        "available_at",
        "started_at",
        "finished_at",
        "last_error_code",
        "last_error_message",
        "processing_stage",
        "failed_stage",
    }

    wrong_course = upload_api.client.get(
        f"/api/courses/{upload_api.other_course_id}/documents/{document_id}",
        headers=upload_api.authorization,
    )
    assert wrong_course.status_code == 404
    unauthenticated = upload_api.client.get(
        f"/api/courses/{upload_api.course_id}/documents/{document_id}"
    )
    assert unauthenticated.status_code == 401


def test_failed_job_retry_is_serialized_and_resets_public_state(upload_api):
    uploaded = _upload(upload_api, b"Retry this document")
    assert uploaded.status_code == 201
    document_id = UUID(uploaded.json()["document"]["id"])

    conflict = upload_api.client.post(
        f"/api/courses/{upload_api.course_id}/documents/{document_id}/retry",
        headers=upload_api.authorization,
    )
    assert conflict.status_code == 409

    with upload_api.session_factory() as session:
        queued_job = session.scalar(
            select(ProcessingJob).where(ProcessingJob.document_id == document_id)
        )
        assert queued_job is not None
        claim_at = queued_job.available_at + timedelta(seconds=1)
    with upload_api.session_factory() as session:
        claim = claim_next_job(
            session,
            "api-test-worker",
            upload_api.storage.provider,
            60,
            now=claim_at,
        )
    assert claim is not None
    with upload_api.session_factory() as session:
        assert (
            fail_job(
                session,
                claim.id,
                claim.claim_token,
                error_code="OCR_REQUIRED",
                error_message="The PDF requires OCR.",
                retryable=False,
                now=claim_at + timedelta(seconds=1),
            )
            == "failed"
        )

    failed = upload_api.client.get(
        f"/api/courses/{upload_api.course_id}/documents/{document_id}",
        headers=upload_api.authorization,
    )
    assert failed.status_code == 200
    assert failed.json()["processing_job"]["last_error_code"] == "OCR_REQUIRED"
    assert failed.json()["processing_job"]["processing_stage"] is None
    assert failed.json()["processing_job"]["failed_stage"] == "validating"

    retried = upload_api.client.post(
        f"/api/courses/{upload_api.course_id}/documents/{document_id}/retry",
        headers=upload_api.authorization,
    )
    assert retried.status_code == 202
    payload = retried.json()
    assert payload["document"]["status"] == "uploaded"
    assert payload["processing_job"]["status"] == "queued"
    assert payload["processing_job"]["attempt_count"] == 0
    assert payload["processing_job"]["last_error_code"] is None

    second_retry = upload_api.client.post(
        f"/api/courses/{upload_api.course_id}/documents/{document_id}/retry",
        headers=upload_api.authorization,
    )
    assert second_retry.status_code == 409


def test_active_document_delete_is_rejected_and_failed_document_can_be_deleted(
    upload_api,
):
    uploaded = _upload(upload_api, b"Delete after processing")
    document_id = UUID(uploaded.json()["document"]["id"])
    path = f"/api/courses/{upload_api.course_id}/documents/{document_id}"

    active_delete = upload_api.client.delete(path, headers=upload_api.authorization)
    assert active_delete.status_code == 409

    with upload_api.session_factory() as session:
        queued_job = session.scalar(
            select(ProcessingJob).where(ProcessingJob.document_id == document_id)
        )
        assert queued_job is not None
        claim_at = queued_job.available_at + timedelta(seconds=1)
    with upload_api.session_factory() as session:
        claim = claim_next_job(
            session,
            "delete-worker",
            upload_api.storage.provider,
            60,
            now=claim_at,
        )
    assert claim is not None
    with upload_api.session_factory() as session:
        assert (
            fail_job(
                session,
                claim.id,
                claim.claim_token,
                error_code="PERMANENT_FAILURE",
                error_message="Document processing failed.",
                retryable=False,
                now=claim_at + timedelta(seconds=1),
            )
            == "failed"
        )

    deleted = upload_api.client.delete(path, headers=upload_api.authorization)
    assert deleted.status_code == 204
    with upload_api.session_factory() as session:
        assert session.get(UploadedDocument, document_id) is None
        assert session.get(ProcessingJob, claim.id) is None


def test_force_delete_discards_a_queued_document_no_worker_has_claimed(upload_api):
    uploaded = _upload(upload_api, b"Stuck in the queue")
    document_id = UUID(uploaded.json()["document"]["id"])
    path = f"/api/courses/{upload_api.course_id}/documents/{document_id}"

    # The plain Remove is still refused for an active job...
    assert (
        upload_api.client.delete(path, headers=upload_api.authorization).status_code
        == 409
    )

    # ...but force clears a queued job that nothing is working on.
    forced = upload_api.client.delete(
        f"{path}?force=true", headers=upload_api.authorization
    )
    assert forced.status_code == 204
    with upload_api.session_factory() as session:
        assert session.get(UploadedDocument, document_id) is None
        assert (
            session.scalar(
                select(ProcessingJob).where(ProcessingJob.document_id == document_id)
            )
            is None
        )


def test_force_delete_discards_a_running_document_whose_worker_lease_expired(
    upload_api,
):
    uploaded = _upload(upload_api, b"Worker crashed mid-read")
    document_id = UUID(uploaded.json()["document"]["id"])
    path = f"/api/courses/{upload_api.course_id}/documents/{document_id}"

    with upload_api.session_factory() as session:
        queued_job = session.scalar(
            select(ProcessingJob).where(ProcessingJob.document_id == document_id)
        )
        claim_at = queued_job.available_at + timedelta(seconds=1)
    with upload_api.session_factory() as session:
        claim = claim_next_job(
            session, "gone-worker", upload_api.storage.provider, 60, now=claim_at
        )
    assert claim is not None

    # Simulate a worker that claimed the job, reported the first stage, then died
    # five minutes ago: the lease is well in the past.
    stale = datetime.now(timezone.utc) - timedelta(minutes=5)
    with upload_api.session_factory() as session:
        job = session.get(ProcessingJob, claim.id)
        job.claimed_at = stale
        job.heartbeat_at = stale + timedelta(seconds=1)
        job.lease_expires_at = stale + timedelta(seconds=2)
        job.processing_stage = "validating"
        session.commit()

    forced = upload_api.client.delete(
        f"{path}?force=true", headers=upload_api.authorization
    )
    assert forced.status_code == 204
    with upload_api.session_factory() as session:
        assert session.get(UploadedDocument, document_id) is None
        assert session.get(ProcessingJob, claim.id) is None


def test_force_delete_discards_a_running_document_even_while_worker_holds_lease(
    upload_api,
):
    uploaded = _upload(upload_api, b"A worker is really on it")
    document_id = UUID(uploaded.json()["document"]["id"])
    path = f"/api/courses/{upload_api.course_id}/documents/{document_id}"

    with upload_api.session_factory() as session:
        queued_job = session.scalar(
            select(ProcessingJob).where(ProcessingJob.document_id == document_id)
        )
        claim_at = queued_job.available_at + timedelta(seconds=1)
    with upload_api.session_factory() as session:
        claim = claim_next_job(
            session, "busy-worker", upload_api.storage.provider, 600, now=claim_at
        )
    assert claim is not None

    forced = upload_api.client.delete(
        f"{path}?force=true", headers=upload_api.authorization
    )
    assert forced.status_code == 204
    with upload_api.session_factory() as session:
        assert session.get(UploadedDocument, document_id) is None
        assert session.get(ProcessingJob, claim.id) is None


def test_storage_delete_failure_retains_tombstone_for_retry(
    upload_api,
    monkeypatch: pytest.MonkeyPatch,
):
    uploaded = _upload(upload_api, b"Retry document cleanup")
    document_id = UUID(uploaded.json()["document"]["id"])
    path = f"/api/courses/{upload_api.course_id}/documents/{document_id}"

    with upload_api.session_factory() as session:
        queued_job = session.scalar(
            select(ProcessingJob).where(ProcessingJob.document_id == document_id)
        )
        assert queued_job is not None
        claim_at = queued_job.available_at + timedelta(seconds=1)
    with upload_api.session_factory() as session:
        claim = claim_next_job(
            session,
            "delete-retry-worker",
            upload_api.storage.provider,
            60,
            now=claim_at,
        )
    assert claim is not None
    with upload_api.session_factory() as session:
        assert (
            fail_job(
                session,
                claim.id,
                claim.claim_token,
                error_code="PERMANENT_FAILURE",
                error_message="Document processing failed.",
                retryable=False,
                now=claim_at + timedelta(seconds=1),
            )
            == "failed"
        )

    original_delete = upload_api.storage.delete
    monkeypatch.setattr(
        upload_api.storage,
        "delete",
        lambda _key: (_ for _ in ()).throw(
            document_service.StorageError("simulated cleanup failure")
        ),
    )
    failed = upload_api.client.delete(path, headers=upload_api.authorization)

    assert failed.status_code == 500
    with upload_api.session_factory() as session:
        document = session.get(UploadedDocument, document_id)
        assert document is not None
        assert document.status == "deleting"

    status_response = upload_api.client.get(path, headers=upload_api.authorization)
    assert status_response.status_code == 404
    duplicate = _upload(upload_api, b"Retry document cleanup")
    assert duplicate.status_code == 409
    assert duplicate.json()["data"]["code"] == "UPLOAD_DOCUMENT_DELETION_IN_PROGRESS"

    monkeypatch.setattr(upload_api.storage, "delete", original_delete)
    retried = upload_api.client.delete(path, headers=upload_api.authorization)
    assert retried.status_code == 204
    with upload_api.session_factory() as session:
        assert session.get(UploadedDocument, document_id) is None


def test_delete_recovers_lost_final_commit_acknowledgement(
    upload_api,
    monkeypatch: pytest.MonkeyPatch,
):
    uploaded = _upload(upload_api, b"Deletion commit acknowledgement")
    document_id = UUID(uploaded.json()["document"]["id"])
    with upload_api.session_factory() as session:
        job = session.scalar(
            select(ProcessingJob).where(ProcessingJob.document_id == document_id)
        )
        assert job is not None
        job.status = "failed"
        job.attempt_count = job.max_attempts
        job.finished_at = job.available_at
        job.last_error_code = "TEST_FAILURE"
        job.last_error_message = "Test failure"
        document = session.get(UploadedDocument, document_id)
        assert document is not None
        document.status = "failed"
        session.commit()

    original_commit = document_service.Session.commit
    commit_count = 0

    def commit_then_fail_once(session) -> None:
        nonlocal commit_count
        commit_count += 1
        original_commit(session)
        if commit_count == 2:
            raise SQLAlchemyError("simulated lost delete acknowledgement")

    monkeypatch.setattr(document_service.Session, "commit", commit_then_fail_once)

    response = upload_api.client.delete(
        f"/api/courses/{upload_api.course_id}/documents/{document_id}",
        headers=upload_api.authorization,
    )

    assert response.status_code == 204
    with upload_api.session_factory() as session:
        assert session.get(UploadedDocument, document_id) is None


def test_hard_delete_cascades_processing_jobs_after_storage_cleanup(upload_api):
    uploaded = _upload(upload_api, b"Delete this queued document")
    assert uploaded.status_code == 201
    document_id = UUID(uploaded.json()["document"]["id"])
    with upload_api.session_factory() as session:
        job_id = session.scalar(
            select(ProcessingJob.id).where(ProcessingJob.document_id == document_id)
        )
        assert job_id is not None

    deleted = upload_api.client.delete(
        f"/api/courses/{upload_api.course_id}",
        headers=upload_api.authorization,
    )
    assert deleted.status_code == 200
    with upload_api.session_factory() as session:
        assert session.get(UploadedDocument, document_id) is None
        assert session.get(ProcessingJob, job_id) is None


def test_concurrent_manual_retries_allow_one_state_transition(upload_api):
    uploaded = _upload(upload_api, b"Concurrent retry document")
    document_id = UUID(uploaded.json()["document"]["id"])
    with upload_api.session_factory() as session:
        queued_job = session.scalar(
            select(ProcessingJob).where(ProcessingJob.document_id == document_id)
        )
        assert queued_job is not None
        claim_at = queued_job.available_at + timedelta(seconds=1)
    with upload_api.session_factory() as session:
        claim = claim_next_job(
            session,
            "retry-race-worker",
            upload_api.storage.provider,
            60,
            now=claim_at,
        )
    assert claim is not None
    with upload_api.session_factory() as session:
        fail_job(
            session,
            claim.id,
            claim.claim_token,
            error_code="PERMANENT_FAILURE",
            error_message="Document processing failed.",
            retryable=False,
            now=claim_at + timedelta(seconds=1),
        )

    barrier = Barrier(2)

    def retry() -> str:
        barrier.wait(timeout=5)
        with upload_api.session_factory() as session:
            try:
                DocumentService.retry_document(
                    session,
                    document_id,
                    upload_api.course_id,
                )
            except ConflictException:
                return "conflict"
        return "retried"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(lambda _index: retry(), range(2)))
    assert sorted(outcomes) == ["conflict", "retried"]


def test_processing_status_and_retry_are_documented_in_openapi():
    schema = app.openapi()
    status_operation = schema["paths"][
        "/api/courses/{course_id}/documents/{document_id}"
    ]["get"]
    retry_operation = schema["paths"][
        "/api/courses/{course_id}/documents/{document_id}/retry"
    ]["post"]

    assert status_operation["security"] == [{"OAuth2PasswordBearer": []}]
    assert "200" in status_operation["responses"]
    assert retry_operation["security"] == [{"OAuth2PasswordBearer": []}]
    assert "202" in retry_operation["responses"]
    assert "409" in retry_operation["responses"]


def test_visual_analysis_status_rollup_and_api_contract(upload_api):
    uploaded = _upload(upload_api, b"%PDF-1.4 visual test document")
    document_id = UUID(uploaded.json()["document"]["id"])

    with upload_api.session_factory() as session:
        doc = session.get(UploadedDocument, document_id)
        assert doc is not None
        doc.file_type = "pdf"
        doc.status = "ready"
        session.commit()

    test_scenarios = [
        # (pages_spec: list of (has_visual_content, visual_analysis_status), expected_rollup)
        # 1. No visual pages
        ([(False, "not_applicable"), (False, "not_applicable")], "not_applicable"),
        # 2. All completed
        ([(True, "completed"), (True, "completed")], "completed"),
        # 3. All not_configured
        ([(True, "not_configured"), (True, "not_configured")], "not_configured"),
        # 4. All failed
        ([(True, "failed"), (True, "failed")], "failed"),
        # 5. Any pending
        ([(True, "completed"), (True, "pending")], "pending"),
        ([(True, "failed"), (True, "pending")], "pending"),
        # 6. Mixed completed + not_configured -> partial
        ([(True, "completed"), (True, "not_configured")], "partial"),
        # 7. Mixed completed + failed -> partial
        ([(True, "completed"), (True, "failed")], "partial"),
        # 8. Mixed failed + not_configured -> partial
        ([(True, "failed"), (True, "not_configured")], "partial"),
        # 9. Individual page partial -> partial
        ([(True, "partial"), (False, "not_applicable")], "partial"),
        # 10. Completed visual page + non-visual page -> completed
        ([(True, "completed"), (False, "not_applicable")], "completed"),
    ]

    for pages_spec, expected_status in test_scenarios:
        with upload_api.session_factory() as session:
            session.query(DocumentPage).filter(
                DocumentPage.document_id == document_id
            ).delete()
            for idx, (has_visual, visual_status) in enumerate(pages_spec):
                session.add(
                    DocumentPage(
                        document_id=document_id,
                        course_id=upload_api.course_id,
                        content_index=idx,
                        page_number=idx + 1,
                        raw_text=f"Page {idx + 1}",
                        text=f"Page {idx + 1}",
                        has_visual_content=has_visual,
                        visual_analysis_status=visual_status,
                    )
                )
            session.commit()

        # Check single document status API
        response = upload_api.client.get(
            f"/api/courses/{upload_api.course_id}/documents/{document_id}",
            headers=upload_api.authorization,
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["document"]["visual_analysis_status"] == expected_status

        # Check document list API
        list_response = upload_api.client.get(
            f"/api/courses/{upload_api.course_id}/documents",
            headers=upload_api.authorization,
        )
        assert list_response.status_code == 200
        docs = list_response.json()["data"]
        matching_doc = next(d for d in docs if d["id"] == str(document_id))
        assert matching_doc["visual_analysis_status"] == expected_status


def test_visual_analysis_status_backward_compatibility_legacy_documents(upload_api):
    # Older ready PDF with 0 DocumentPage records -> not_applicable (never false completed)
    uploaded = _upload(upload_api, b"%PDF-1.4 legacy pdf")
    pdf_id = UUID(uploaded.json()["document"]["id"])
    with upload_api.session_factory() as session:
        doc = session.get(UploadedDocument, pdf_id)
        assert doc is not None
        doc.file_type = "pdf"
        doc.status = "ready"
        session.commit()

    response = upload_api.client.get(
        f"/api/courses/{upload_api.course_id}/documents/{pdf_id}",
        headers=upload_api.authorization,
    )
    assert response.status_code == 200
    assert response.json()["document"]["visual_analysis_status"] == "not_applicable"

    # In-flight (uploaded/processing) PDF with 0 DocumentPage records -> pending
    with upload_api.session_factory() as session:
        doc = session.get(UploadedDocument, pdf_id)
        assert doc is not None
        doc.status = "processing"
        session.commit()

    response = upload_api.client.get(
        f"/api/courses/{upload_api.course_id}/documents/{pdf_id}",
        headers=upload_api.authorization,
    )
    assert response.status_code == 200
    assert response.json()["document"]["visual_analysis_status"] == "pending"

    # Text document with 0 DocumentPage records -> not_applicable
    text_uploaded = _upload(upload_api, b"plain text legacy")
    text_id = UUID(text_uploaded.json()["document"]["id"])
    with upload_api.session_factory() as session:
        doc = session.get(UploadedDocument, text_id)
        assert doc is not None
        doc.file_type = "text"
        doc.status = "ready"
        session.commit()

    response = upload_api.client.get(
        f"/api/courses/{upload_api.course_id}/documents/{text_id}",
        headers=upload_api.authorization,
    )
    assert response.status_code == 200
    assert response.json()["document"]["visual_analysis_status"] == "not_applicable"
