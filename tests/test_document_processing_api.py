from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from uuid import UUID

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from backend.app.models import ProcessingJob, UploadedDocument
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
        f"/api/courses/{upload_api.course_id}?hard_delete=true",
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
