"""Tests for user-scoped profile-knowledge document uploads, processing, isolation, and lifecycle."""

import io
from uuid import UUID, uuid4
from sqlalchemy import select

from backend.app.models import (
    Course,
    ProfileDocument,
    ProfileDocumentChunk,
    ProfileProcessingJob,
)
from services.profile_knowledge import (
    load_profile_knowledge_for_generation,
)


def test_upload_and_list_profile_documents(authz_api):
    client = authz_api.client
    headers = authz_api.authorization_a

    file_content = b"This is a personal background syllabus on quantum mechanics."
    response = client.post(
        "/api/profile-documents",
        headers=headers,
        files={"document": ("syllabus.txt", io.BytesIO(file_content), "text/plain")},
    )
    assert response.status_code == 201, response.text
    data = response.json()["data"]
    assert data["duplicate"] is False
    doc_id = data["document"]["id"]
    assert data["document"]["original_file_name"] == "syllabus.txt"
    assert data["document"]["file_type"] == "txt"
    assert data["document"]["status"] == "uploaded"

    # Listing documents returns the uploaded document
    list_res = client.get("/api/profile-documents", headers=headers)
    assert list_res.status_code == 200
    docs = list_res.json()["data"]
    assert len(docs) >= 1
    assert any(d["id"] == doc_id for d in docs)

    # Status route returns document and job
    status_res = client.get(f"/api/profile-documents/{doc_id}", headers=headers)
    assert status_res.status_code == 200
    status_data = status_res.json()["data"]
    assert status_data["document"]["id"] == doc_id
    assert status_data["processing_job"] is not None
    assert status_data["processing_job"]["status"] == "queued"


def test_upload_duplicate_profile_document(authz_api):
    client = authz_api.client
    headers = authz_api.authorization_a

    file_content = b"Unique content for duplicate testing 12345."
    res1 = client.post(
        "/api/profile-documents",
        headers=headers,
        files={"document": ("file.txt", io.BytesIO(file_content), "text/plain")},
    )
    assert res1.status_code == 201
    assert res1.json()["data"]["duplicate"] is False

    # Second upload with same content returns duplicate: True
    res2 = client.post(
        "/api/profile-documents",
        headers=headers,
        files={
            "document": ("file_renamed.txt", io.BytesIO(file_content), "text/plain")
        },
    )
    assert res2.status_code == 201
    assert res2.json()["data"]["duplicate"] is True
    assert (
        res2.json()["data"]["document"]["id"] == res1.json()["data"]["document"]["id"]
    )


def test_cross_user_isolation(authz_api):
    client = authz_api.client
    headers1 = authz_api.authorization_a
    headers2 = authz_api.authorization_b

    file_content = b"Secret personal background notes for User 1 only."
    res = client.post(
        "/api/profile-documents",
        headers=headers1,
        files={"document": ("secret.txt", io.BytesIO(file_content), "text/plain")},
    )
    assert res.status_code == 201
    doc_id = res.json()["data"]["document"]["id"]

    # User 2 cannot access or list User 1's profile document
    res_other_list = client.get("/api/profile-documents", headers=headers2)
    assert res_other_list.status_code == 200
    assert not any(d["id"] == doc_id for d in res_other_list.json()["data"])

    res_other_get = client.get(f"/api/profile-documents/{doc_id}", headers=headers2)
    assert res_other_get.status_code == 404

    res_other_delete = client.delete(
        f"/api/profile-documents/{doc_id}", headers=headers2
    )
    assert res_other_delete.status_code == 404


def test_course_deletion_never_deletes_profile_documents(authz_api):
    client = authz_api.client
    headers = authz_api.authorization_a
    user_id = authz_api.user_a_id

    # Upload profile document
    file_content = b"Permanent profile context document."
    res = client.post(
        "/api/profile-documents",
        headers=headers,
        files={"document": ("permanent.txt", io.BytesIO(file_content), "text/plain")},
    )
    assert res.status_code == 201
    doc_id = UUID(res.json()["data"]["document"]["id"])

    # Create a course for this user
    with authz_api.session_factory() as session:
        course = Course(
            title="Physics 101",
            description="Intro to Physics",
            owner_id=user_id,
        )
        session.add(course)
        session.commit()
        course_id = course.id

    # Delete the course
    del_res = client.delete(f"/api/courses/{course_id}", headers=headers)
    assert del_res.status_code == 200

    # Profile document is completely untouched
    with authz_api.session_factory() as session:
        doc = session.scalar(
            select(ProfileDocument).where(ProfileDocument.id == doc_id)
        )
        assert doc is not None
        assert doc.user_id == user_id


def test_profile_document_retry_and_delete(authz_api):
    client = authz_api.client
    headers = authz_api.authorization_a

    # Upload document
    file_content = b"Retry and delete test content."
    res = client.post(
        "/api/profile-documents",
        headers=headers,
        files={"document": ("test.txt", io.BytesIO(file_content), "text/plain")},
    )
    doc_id = res.json()["data"]["document"]["id"]
    doc_uuid = UUID(doc_id)

    # Mark job and document as failed directly in DB
    with authz_api.session_factory() as session:
        doc = session.scalar(
            select(ProfileDocument).where(ProfileDocument.id == doc_uuid)
        )
        job = session.scalar(
            select(ProfileProcessingJob).where(
                ProfileProcessingJob.document_id == doc_uuid
            )
        )
        doc.status = "failed"
        doc.processing_error = "SIMULATED_FAILURE"
        job.status = "failed"
        job.last_error_code = "SIMULATED_FAILURE"
        job.finished_at = job.updated_at
        session.commit()

    # Retry route resets job to queued
    retry_res = client.post(f"/api/profile-documents/{doc_id}/retry", headers=headers)
    assert retry_res.status_code == 200
    assert retry_res.json()["data"]["document"]["status"] == "uploaded"
    assert retry_res.json()["data"]["processing_job"]["status"] == "queued"

    # Delete profile document removes row, storage, and vectors
    delete_res = client.delete(f"/api/profile-documents/{doc_id}", headers=headers)
    assert delete_res.status_code == 200

    # Verification that document is deleted from DB
    with authz_api.session_factory() as session:
        deleted_doc = session.scalar(
            select(ProfileDocument).where(ProfileDocument.id == doc_uuid)
        )
        assert deleted_doc is None


def test_retrieval_priority_and_opt_out(authz_api):
    user_id = authz_api.user_a_id

    # Add ready profile document chunk
    doc_uuid = uuid4()
    with authz_api.session_factory() as session:
        doc = ProfileDocument(
            id=doc_uuid,
            original_file_name="background.txt",
            file_type="txt",
            mime_type="text/plain",
            file_size=100,
            file_hash=uuid4().hex * 2,
            user_id=user_id,
            storage_provider="local:default",
            storage_key=f"users/{user_id}/documents/{doc_uuid}/source.txt",
            status="ready",
        )
        session.add(doc)
        session.flush()

        chunk = ProfileDocumentChunk(
            document_id=doc_uuid,
            user_id=user_id,
            chunk_index=0,
            page_number=1,
            end_page_number=1,
            text="Student prefers visual diagrams and real-world engineering examples.",
        )
        session.add(chunk)
        session.commit()

    # Opted-out (use_profile_knowledge=False) returns empty profile context
    with authz_api.session_factory() as session:
        context_opt_out = load_profile_knowledge_for_generation(
            session, user_id, opted_in=False
        )
        assert context_opt_out.is_empty
        assert context_opt_out.text == ""

        # Opted-in (use_profile_knowledge=True) loads profile document chunk
        context_opt_in = load_profile_knowledge_for_generation(
            session, user_id, opted_in=True
        )
        assert not context_opt_in.is_empty
        assert "visual diagrams" in context_opt_in.text
        assert context_opt_in.items_used >= 1


class DeterministicEmbeddingProvider:
    provider = "fake-provider"
    model = "fake-model"

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[0.1] * 768 for _ in texts]

    def embed_query(self, text: str) -> list[float]:
        return [0.1] * 768


def test_worker_processes_profile_document_to_ready(authz_api):
    from workers.document_processor import process_next_job

    client = authz_api.client
    headers = authz_api.authorization_a

    # Drain any existing fixture jobs first
    while process_next_job(
        session_factory=authz_api.session_factory,
        storage=authz_api.storage,
        embedding_provider=DeterministicEmbeddingProvider(),
    ):
        pass

    file_content = b"Background notes on advanced mathematical physics and partial differential equations."
    res = client.post(
        "/api/profile-documents",
        headers=headers,
        files={"document": ("physics.txt", io.BytesIO(file_content), "text/plain")},
    )
    assert res.status_code == 201
    doc_id = UUID(res.json()["data"]["document"]["id"])

    # Worker processes the queued job
    processed = process_next_job(
        session_factory=authz_api.session_factory,
        storage=authz_api.storage,
        embedding_provider=DeterministicEmbeddingProvider(),
    )
    assert processed is True

    # Assert document is now ready and job is succeeded
    with authz_api.session_factory() as session:
        doc = session.scalar(
            select(ProfileDocument).where(ProfileDocument.id == doc_id)
        )
        job = session.scalar(
            select(ProfileProcessingJob).where(
                ProfileProcessingJob.document_id == doc_id
            )
        )
        chunks = session.scalars(
            select(ProfileDocumentChunk).where(
                ProfileDocumentChunk.document_id == doc_id
            )
        ).all()
        assert doc.status == "ready"
        assert doc.processing_error is None
        assert job.status == "succeeded"
        assert len(chunks) >= 1
        assert "mathematical physics" in chunks[0].text


def test_worker_recovers_expired_profile_job(authz_api):
    from datetime import datetime, timedelta, timezone
    from services.processing_jobs import recover_expired_jobs

    client = authz_api.client
    headers = authz_api.authorization_a

    file_content = b"Content for expired lease recovery test."
    res = client.post(
        "/api/profile-documents",
        headers=headers,
        files={"document": ("lease.txt", io.BytesIO(file_content), "text/plain")},
    )
    assert res.status_code == 201
    doc_id = UUID(res.json()["data"]["document"]["id"])

    # Simulate expired running job with valid lease fields matching check constraint
    past_time = datetime.now(timezone.utc) - timedelta(hours=2)
    with authz_api.session_factory() as session:
        doc = session.scalar(
            select(ProfileDocument).where(ProfileDocument.id == doc_id)
        )
        job = session.scalar(
            select(ProfileProcessingJob).where(
                ProfileProcessingJob.document_id == doc_id
            )
        )
        doc.status = "processing"
        job.status = "running"
        job.attempt_count = 1
        job.lease_owner = "worker-test"
        job.claim_token = "expired-token"
        job.claimed_at = past_time
        job.heartbeat_at = past_time
        job.lease_expires_at = past_time + timedelta(minutes=5)
        session.commit()

    with authz_api.session_factory() as session:
        recovered = recover_expired_jobs(session, now=past_time + timedelta(minutes=10))
        assert recovered >= 1

    with authz_api.session_factory() as session:
        doc = session.scalar(
            select(ProfileDocument).where(ProfileDocument.id == doc_id)
        )
        job = session.scalar(
            select(ProfileProcessingJob).where(
                ProfileProcessingJob.document_id == doc_id
            )
        )
        assert doc.status == "uploaded"
        assert job.status == "queued"
