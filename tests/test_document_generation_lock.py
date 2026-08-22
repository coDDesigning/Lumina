from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from io import BytesIO
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import select

from backend.app.models import (
    Course,
    DocumentChunk,
    ProcessingJob,
    Role,
    UploadedDocument,
    User,
)
from services.document import DocumentActiveError, DocumentService
from services.document_lock import (
    DocumentLockManager,
    acquire_generation_locks,
    is_document_locked_for_generation,
    reset_generation_locks,
)
from storage.local import LocalStorage

pytestmark = pytest.mark.database_contract


@pytest.fixture(autouse=True)
def _clean_locks():
    reset_generation_locks()
    yield
    reset_generation_locks()


def test_lock_manager_acquire_release_and_query():
    manager = DocumentLockManager()
    doc_1 = uuid4()
    doc_2 = uuid4()

    assert not manager.is_locked(doc_1)
    assert not manager.is_locked(doc_2)

    manager.acquire([doc_1, doc_2])
    assert manager.is_locked(doc_1)
    assert manager.is_locked(doc_2)

    manager.acquire([doc_1])
    assert manager.is_locked(doc_1)

    manager.release([doc_1])
    assert manager.is_locked(doc_1)  # Still locked by first acquire

    manager.release([doc_1, doc_2])
    assert not manager.is_locked(doc_1)
    assert not manager.is_locked(doc_2)

    # Empty iterable is safe
    manager.acquire([])
    manager.release([])
    assert not manager.is_locked(doc_1)


def test_acquire_generation_locks_context_manager():
    doc_1 = uuid4()
    doc_2 = uuid4()

    assert not is_document_locked_for_generation(doc_1)

    with acquire_generation_locks([doc_1, doc_2]):
        assert is_document_locked_for_generation(doc_1)
        assert is_document_locked_for_generation(doc_2)

    assert not is_document_locked_for_generation(doc_1)
    assert not is_document_locked_for_generation(doc_2)


def test_acquire_generation_locks_releases_on_exception():
    doc_id = uuid4()

    with pytest.raises(RuntimeError, match="synthetic generation error"):
        with acquire_generation_locks([doc_id]):
            assert is_document_locked_for_generation(doc_id)
            raise RuntimeError("synthetic generation error")

    assert not is_document_locked_for_generation(doc_id)


def test_concurrent_generation_locks_on_same_document():
    doc_id = uuid4()
    barrier = Barrier(3)
    results = []

    def worker():
        with acquire_generation_locks([doc_id]):
            results.append(is_document_locked_for_generation(doc_id))
            barrier.wait()
            # Wait for all to observe lock held
            results.append(is_document_locked_for_generation(doc_id))

    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = [executor.submit(worker) for _ in range(3)]
        for f in futures:
            f.result()

    assert len(results) == 6
    assert all(results)
    assert not is_document_locked_for_generation(doc_id)


def _seed_document(db_session, storage, email: str, *, status: str = "ready"):
    user = User(
        name="Lock Test User",
        email=email,
        password_hash="x",
        role=db_session.scalar(select(Role).where(Role.name == "user")),
    )
    course = Course(
        title="Lock Test Course",
        owner=user,
    )

    doc_id = uuid4()
    storage_key = f"{doc_id}.txt"
    storage.save(storage_key, BytesIO(b"Content for document generation lock test."))

    document = UploadedDocument(
        id=doc_id,
        course=course,
        uploader=user,
        original_file_name="test.txt",
        file_type="txt",
        mime_type="text/plain",
        file_size=30,
        file_hash=f"{doc_id.hex[:32]}00000000000000000000000000000000",
        storage_provider=storage.provider,
        storage_key=storage_key,
        status=status,
    )

    now = datetime.now(timezone.utc)
    job = ProcessingJob(
        document=document,
        course_id=course.id,
        job_type="extract_document",
        status="succeeded" if status == "ready" else status,
        attempt_count=1,
        max_attempts=3,
        available_at=now,
        finished_at=now if status == "ready" else None,
    )
    chunk = DocumentChunk(
        document=document,
        course=course,
        chunk_index=0,
        text="Content for document generation lock test.",
    )
    db_session.add_all([user, course, document, job, chunk])
    db_session.commit()
    return course, document, user


def test_delete_document_blocked_while_locked_for_generation(db_session, tmp_path):
    storage = LocalStorage(tmp_path / "lock-uploads", namespace="lock-test")
    course, document, _ = _seed_document(
        db_session, storage, "locked-delete@example.com"
    )

    with acquire_generation_locks([document.id]):
        with pytest.raises(DocumentActiveError):
            DocumentService.delete_document(
                db_session,
                storage,
                document.id,
                course.id,
            )

    # After lock release, deletion must succeed cleanly
    DocumentService.delete_document(
        db_session,
        storage,
        document.id,
        course.id,
    )
    assert db_session.get(UploadedDocument, document.id) is None


def test_delete_document_http_409_when_locked_for_generation(authz_api):
    doc_id = authz_api.a_document_id

    with acquire_generation_locks([doc_id]):
        response = authz_api.client.delete(
            f"/api/courses/{authz_api.a_course_id}/documents/{doc_id}",
            headers=authz_api.authorization_a,
        )
        assert response.status_code == 409
        assert (
            "cannot be deleted while it is being processed" in response.json()["detail"]
        )

    # Deleting without lock succeeds
    response = authz_api.client.delete(
        f"/api/courses/{authz_api.a_course_id}/documents/{doc_id}",
        headers=authz_api.authorization_a,
    )
    assert response.status_code == 204
