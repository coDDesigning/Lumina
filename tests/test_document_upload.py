import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from io import BytesIO
from pathlib import Path
from threading import Barrier, Event
from types import SimpleNamespace
from typing import BinaryIO
from uuid import UUID, uuid4

import pytest
from fastapi import UploadFile
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from backend.app.models import Course, ProcessingJob, UploadedDocument
from backend.app.repositories.document import DocumentRepository
from main import app
from services import document as document_service
from services import document_validation
from services.course import CourseService
from services.document import DocumentRegistrationError, DocumentService
from services.processing_jobs import enqueue_document_job
from storage.base import StorageError
from storage.local import LocalStorage


def upload_document(
    context,
    filename: str,
    content: bytes,
    content_type: str = "application/octet-stream",
    *,
    course_id: int | None = None,
    authenticated: bool = True,
):
    headers = context.authorization if authenticated else {}
    return context.client.post(
        f"/api/courses/{course_id or context.course_id}/documents",
        headers=headers,
        files={"document": (filename, content, content_type)},
    )


def stored_files(root: Path) -> list[Path]:
    return sorted(
        (path for path in root.rglob("*") if path.is_file()),
        key=lambda path: path.as_posix(),
    )


def document_count(context) -> int:
    with context.session_factory() as session:
        return session.scalar(select(func.count()).select_from(UploadedDocument)) or 0


def test_first_upload_returns_201_uploaded_document_with_trusted_metadata(
    upload_api,
) -> None:
    content = b"Deterministic course notes"

    response = upload_document(
        upload_api,
        "NOTES.TXT",
        content,
        "text/html",
    )

    assert response.status_code == 201
    payload = response.json()
    document = payload["document"]
    document_id = UUID(document["id"])
    assert payload["duplicate"] is False
    assert document == {
        "id": str(document_id),
        "original_file_name": "NOTES.TXT",
        "file_type": "txt",
        "mime_type": "text/plain",
        "material_kind": "unspecified",
        "file_size": len(content),
        "course_id": upload_api.course_id,
        "status": "uploaded",
        "created_at": document["created_at"],
        "updated_at": document["updated_at"],
    }
    datetime.fromisoformat(document["created_at"])
    datetime.fromisoformat(document["updated_at"])

    with upload_api.session_factory() as session:
        persisted = session.get(UploadedDocument, document_id)
        assert persisted is not None
        assert persisted.file_hash == hashlib.sha256(content).hexdigest()
        assert persisted.user_id == upload_api.user_id
        assert persisted.storage_provider == upload_api.storage.provider
        assert upload_api.storage.read(persisted.storage_key) == content
    assert len(stored_files(upload_api.storage_root)) == 1


def test_same_bytes_in_same_course_including_renamed_file_are_deduplicated(
    upload_api,
) -> None:
    content = b"The same lesson under two names"
    first = upload_document(upload_api, "lesson.txt", content, "text/plain")
    second = upload_document(upload_api, "renamed.md", content, "text/markdown")

    assert first.status_code == 201
    assert second.status_code == 200
    assert first.json()["duplicate"] is False
    assert second.json()["duplicate"] is True
    assert second.json()["document"]["id"] == first.json()["document"]["id"]
    assert second.json()["document"]["original_file_name"] == "lesson.txt"
    assert second.json()["document"]["file_type"] == "txt"
    assert document_count(upload_api) == 1
    assert len(stored_files(upload_api.storage_root)) == 1


def test_missing_duplicate_storage_is_repaired_without_replacing_metadata(
    upload_api,
) -> None:
    content = b"Recover missing stored content"
    first = upload_document(upload_api, "lesson.txt", content)
    assert first.status_code == 201

    with upload_api.session_factory() as session:
        persisted = session.scalar(select(UploadedDocument))
        assert persisted is not None
        upload_api.storage.delete(persisted.storage_key)

    replacement = upload_document(upload_api, "lesson-renamed.txt", content)

    assert replacement.status_code == 200
    assert replacement.json()["duplicate"] is True
    assert replacement.json()["document"]["id"] == first.json()["document"]["id"]
    assert document_count(upload_api) == 1
    assert len(stored_files(upload_api.storage_root)) == 1


def test_failed_missing_storage_repair_preserves_document_row(
    upload_api,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    content = b"Preserve document metadata"
    first = upload_document(upload_api, "lesson.txt", content)
    assert first.status_code == 201

    with upload_api.session_factory() as session:
        persisted = session.scalar(select(UploadedDocument))
        assert persisted is not None
        upload_api.storage.delete(persisted.storage_key)

    def fail_save(_key: str, _source: BinaryIO) -> None:
        raise StorageError("simulated repair failure")

    monkeypatch.setattr(upload_api.storage, "save", fail_save)
    failed = upload_document(upload_api, "lesson.txt", content)

    assert failed.status_code == 500
    assert document_count(upload_api) == 1


def test_same_bytes_in_different_courses_create_distinct_rows_and_files(
    upload_api,
) -> None:
    content = b"Shared source material"
    first = upload_document(upload_api, "shared.txt", content)
    second = upload_document(
        upload_api,
        "shared.txt",
        content,
        course_id=upload_api.other_course_id,
    )

    assert first.status_code == second.status_code == 201
    assert first.json()["document"]["id"] != second.json()["document"]["id"]
    assert first.json()["duplicate"] is second.json()["duplicate"] is False
    assert document_count(upload_api) == 2
    assert len(stored_files(upload_api.storage_root)) == 2
    with upload_api.session_factory() as session:
        hashes = set(session.scalars(select(UploadedDocument.file_hash)).all())
        assert hashes == {hashlib.sha256(content).hexdigest()}


def test_upload_requires_authentication_and_creates_nothing(upload_api) -> None:
    response = upload_document(
        upload_api,
        "notes.txt",
        b"Course notes",
        authenticated=False,
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Not authenticated"
    assert response.headers["www-authenticate"] == "Bearer"
    assert document_count(upload_api) == 0
    assert stored_files(upload_api.storage_root) == []


@pytest.mark.parametrize("course_kind", ["missing", "deleted"])
def test_missing_or_deleted_course_returns_404_without_side_effects(
    upload_api,
    course_kind: str,
) -> None:
    course_id = 999_999 if course_kind == "missing" else upload_api.deleted_course_id

    response = upload_document(
        upload_api,
        "notes.txt",
        b"Course notes",
        course_id=course_id,
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "Course not found"}
    assert document_count(upload_api) == 0
    assert stored_files(upload_api.storage_root) == []


@pytest.mark.parametrize(
    ("filename", "content", "expected_status", "expected_code"),
    [
        ("notes.docx", b"unsupported", 415, "UPLOAD_UNSUPPORTED_FILE_TYPE"),
        ("empty.txt", b"", 422, "UPLOAD_EMPTY_FILE"),
    ],
    ids=["unsupported", "empty"],
)
def test_invalid_uploads_create_no_rows_or_files(
    upload_api,
    filename: str,
    content: bytes,
    expected_status: int,
    expected_code: str,
) -> None:
    response = upload_document(upload_api, filename, content)

    assert response.status_code == expected_status
    assert response.json()["success"] is False
    assert response.json()["data"] == {"code": expected_code}
    assert document_count(upload_api) == 0
    assert stored_files(upload_api.storage_root) == []


@pytest.mark.parametrize(
    ("filename", "content"),
    [
        ("broken.pdf", b"%PDF-1.7\ntruncated"),
        ("binary.txt", b"\x89PNG\r\n\x1a\nnot text"),
    ],
    ids=["corrupt-pdf", "binary-text"],
)
def test_deep_validation_failures_are_queued_for_worker(
    upload_api,
    filename: str,
    content: bytes,
) -> None:
    response = upload_document(upload_api, filename, content)

    assert response.status_code == 201
    assert response.json()["document"]["status"] == "uploaded"
    with upload_api.session_factory() as session:
        document = session.scalar(select(UploadedDocument))
        job = session.scalar(select(ProcessingJob))
        assert document is not None
        assert job is not None
        assert job.status == "queued"
        assert upload_api.storage.read(document.storage_key) == content


def test_oversized_upload_returns_413_without_side_effects(
    upload_api,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        document_validation,
        "settings",
        SimpleNamespace(max_upload_size_bytes=8),
    )

    response = upload_document(upload_api, "large.txt", b"123456789")

    assert response.status_code == 413
    assert response.json()["data"] == {"code": "UPLOAD_FILE_TOO_LARGE"}
    assert document_count(upload_api) == 0
    assert stored_files(upload_api.storage_root) == []


@pytest.mark.parametrize("limit_kind", ["count", "bytes"])
def test_course_document_limits_do_not_leave_extra_rows_or_files(
    upload_api,
    monkeypatch: pytest.MonkeyPatch,
    limit_kind: str,
) -> None:
    first_content = b"first document"
    first = upload_document(upload_api, "first.txt", first_content)
    assert first.status_code == 201

    limits = (
        SimpleNamespace(max_documents_per_course=1, max_course_storage_bytes=10_000)
        if limit_kind == "count"
        else SimpleNamespace(
            max_documents_per_course=10,
            max_course_storage_bytes=len(first_content) + 1,
        )
    )
    monkeypatch.setattr(document_service, "settings", limits)

    rejected = upload_document(upload_api, "second.txt", b"second document")

    assert rejected.status_code == 409
    assert rejected.json()["data"] == {"code": "UPLOAD_COURSE_DOCUMENT_LIMIT"}
    assert document_count(upload_api) == 1
    assert len(stored_files(upload_api.storage_root)) == 1


def test_storage_failure_creates_no_database_row(
    upload_api,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_save(_key: str, _source: object) -> None:
        raise StorageError("simulated unavailable storage")

    monkeypatch.setattr(upload_api.storage, "save", fail_save)

    response = upload_document(upload_api, "notes.txt", b"Course notes")

    assert response.status_code == 500
    assert response.json()["data"] == {"code": "UPLOAD_FAILED"}
    assert document_count(upload_api) == 0
    assert stored_files(upload_api.storage_root) == []


def test_database_insert_failure_deletes_the_stored_file(
    upload_api,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_create(_db: Session, **_values: object) -> UploadedDocument:
        raise SQLAlchemyError("simulated insert failure")

    monkeypatch.setattr(DocumentRepository, "create", fail_create)

    response = upload_document(upload_api, "notes.txt", b"Course notes")

    assert response.status_code == 500
    assert response.json()["data"] == {"code": "UPLOAD_FAILED"}
    assert document_count(upload_api) == 0
    assert stored_files(upload_api.storage_root) == []


def test_job_enqueue_failure_rolls_back_document_and_storage(
    upload_api,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_enqueue(*_args, **_kwargs):
        raise SQLAlchemyError("simulated enqueue failure")

    monkeypatch.setattr(document_service, "enqueue_document_job", fail_enqueue)

    response = upload_document(upload_api, "notes.txt", b"Course notes")

    assert response.status_code == 500
    assert document_count(upload_api) == 0
    with upload_api.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(ProcessingJob)) == 0
    assert stored_files(upload_api.storage_root) == []


def test_unknown_commit_outcome_preserves_content_for_reconciliation(
    db_session: Session,
    model_graph,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = LocalStorage(tmp_path / "commit-failure")

    def fail_commit() -> None:
        raise SQLAlchemyError("simulated pre-commit failure")

    monkeypatch.setattr(db_session, "commit", fail_commit)

    with pytest.raises(DocumentRegistrationError):
        DocumentService.register(
            db=db_session,
            storage=storage,
            upload=UploadFile(BytesIO(b"commit failure"), filename="notes.txt"),
            course_id=model_graph.course.id,
            user_id=model_graph.user.id,
        )

    assert len(stored_files(storage.root)) == 1
    assert db_session.scalar(select(func.count()).select_from(UploadedDocument)) == 0
    assert db_session.scalar(select(func.count()).select_from(ProcessingJob)) == 0


def test_committed_row_and_file_are_preserved_when_acknowledgement_fails(
    db_session: Session,
    model_graph,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = LocalStorage(tmp_path / "commit-acknowledgement")
    original_commit = db_session.commit

    def commit_then_fail() -> None:
        original_commit()
        raise SQLAlchemyError("simulated lost commit acknowledgement")

    monkeypatch.setattr(db_session, "commit", commit_then_fail)

    result = DocumentService.register(
        db=db_session,
        storage=storage,
        upload=UploadFile(BytesIO(b"committed content"), filename="notes.txt"),
        course_id=model_graph.course.id,
        user_id=model_graph.user.id,
    )

    assert result.duplicate is False
    assert len(stored_files(storage.root)) == 1
    assert db_session.scalar(select(func.count()).select_from(UploadedDocument)) == 1
    assert db_session.scalar(select(func.count()).select_from(ProcessingJob)) == 1


def test_rollback_failure_still_removes_unregistered_file(
    db_session: Session,
    model_graph,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = LocalStorage(tmp_path / "rollback-failure")
    original_rollback = db_session.rollback

    def fail_create(_db: Session, **_values: object) -> UploadedDocument:
        raise SQLAlchemyError("simulated insert failure")

    def fail_rollback() -> None:
        raise SQLAlchemyError("simulated rollback failure")

    monkeypatch.setattr(DocumentRepository, "create", fail_create)
    monkeypatch.setattr(db_session, "rollback", fail_rollback)

    with pytest.raises(DocumentRegistrationError):
        DocumentService.register(
            db=db_session,
            storage=storage,
            upload=UploadFile(BytesIO(b"rollback failure"), filename="notes.txt"),
            course_id=model_graph.course.id,
            user_id=model_graph.user.id,
        )

    assert stored_files(storage.root) == []
    monkeypatch.setattr(db_session, "rollback", original_rollback)


def test_unique_race_returns_winner_and_deletes_losing_file(
    db_session: Session,
    model_graph,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    content = b"concurrent upload content"
    file_hash = hashlib.sha256(content).hexdigest()
    storage = LocalStorage(tmp_path / "race-storage", chunk_size=5)
    winner_id = uuid4()
    winner_key = storage.generate_key(
        model_graph.course.id,
        winner_id,
        "txt",
    )
    storage.save(winner_key, BytesIO(content))
    winner = DocumentRepository.create(
        db_session,
        id=winner_id,
        original_file_name="winner.txt",
        file_type="txt",
        mime_type="text/plain",
        file_size=len(content),
        file_hash=file_hash,
        user_id=model_graph.user.id,
        course_id=model_graph.course.id,
        storage_provider=storage.provider,
        storage_key=winner_key,
        status="uploaded",
    )
    enqueue_document_job(db_session, winner)
    db_session.commit()
    lookup_count = 0

    def race_lookup(
        _db: Session,
        _course_id: int,
        _file_hash: str,
    ) -> UploadedDocument | None:
        nonlocal lookup_count
        lookup_count += 1
        return None if lookup_count == 1 else winner

    def lose_insert(_db: Session, **_values: object) -> UploadedDocument:
        raise IntegrityError(
            "INSERT INTO uploaded_documents ...",
            {},
            Exception("unique constraint race"),
        )

    monkeypatch.setattr(
        DocumentRepository,
        "get_by_course_and_hash",
        race_lookup,
    )
    monkeypatch.setattr(DocumentRepository, "create", lose_insert)

    result = DocumentService.register(
        db=db_session,
        storage=storage,
        upload=UploadFile(BytesIO(content), filename="loser.txt"),
        course_id=model_graph.course.id,
        user_id=model_graph.user.id,
    )

    assert result.duplicate is True
    assert result.document.id == winner_id
    assert lookup_count == 2
    assert db_session.in_transaction() is False
    assert storage.read(winner_key) == content
    assert stored_files(storage.root) == [storage.root.joinpath(*winner_key.split("/"))]
    assert db_session.scalar(select(func.count()).select_from(UploadedDocument)) == 1
    assert db_session.scalar(select(func.count()).select_from(ProcessingJob)) == 1


def test_duplicate_service_return_releases_database_transaction(
    db_session: Session,
    model_graph,
    tmp_path: Path,
) -> None:
    storage = LocalStorage(tmp_path / "duplicate-transaction")
    content = b"duplicate transaction"
    first = DocumentService.register(
        db=db_session,
        storage=storage,
        upload=UploadFile(BytesIO(content), filename="notes.txt"),
        course_id=model_graph.course.id,
        user_id=model_graph.user.id,
    )
    second = DocumentService.register(
        db=db_session,
        storage=storage,
        upload=UploadFile(BytesIO(content), filename="renamed.txt"),
        course_id=model_graph.course.id,
        user_id=model_graph.user.id,
    )

    assert first.document.id == second.document.id
    assert second.duplicate is True
    assert db_session.in_transaction() is False


def test_simultaneous_uploads_use_database_constraint_and_one_file(
    session_factory,
    model_graph,
    tmp_path: Path,
) -> None:
    content = b"real concurrent duplicate"
    storage = LocalStorage(tmp_path / "concurrent-storage", chunk_size=5)
    start_barrier = Barrier(2)

    def register() -> tuple[UUID, bool]:
        start_barrier.wait(timeout=5)
        with session_factory() as session:
            result = DocumentService.register(
                db=session,
                storage=storage,
                upload=UploadFile(BytesIO(content), filename="notes.txt"),
                course_id=model_graph.course.id,
                user_id=model_graph.user.id,
            )
            return result.document.id, result.duplicate

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _index: register(), range(2)))

    assert {duplicate for _document_id, duplicate in results} == {False, True}
    assert len({document_id for document_id, _duplicate in results}) == 1
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(UploadedDocument)) == 1
        assert session.scalar(select(func.count()).select_from(ProcessingJob)) == 1
    assert len(stored_files(storage.root)) == 1


def test_missing_multipart_document_is_rejected_before_service(upload_api) -> None:
    response = upload_api.client.post(
        f"/api/courses/{upload_api.course_id}/documents",
        headers=upload_api.authorization,
    )

    assert response.status_code == 422
    assert response.json()["data"] == {"code": "UPLOAD_DOCUMENT_REQUIRED"}
    assert document_count(upload_api) == 0
    assert stored_files(upload_api.storage_root) == []


def test_malformed_multipart_uses_controlled_error_response(upload_api) -> None:
    response = upload_api.client.post(
        f"/api/courses/{upload_api.course_id}/documents",
        headers={
            **upload_api.authorization,
            "Content-Type": "multipart/form-data",
        },
        content=b"malformed multipart body",
    )

    assert response.status_code == 400
    assert response.json()["data"] == {"code": "UPLOAD_INVALID_MULTIPART"}
    assert document_count(upload_api) == 0
    assert stored_files(upload_api.storage_root) == []


def test_non_file_request_validation_keeps_fastapi_error_contract(upload_api) -> None:
    response = upload_api.client.post(
        "/api/courses/not-an-integer/documents",
        headers=upload_api.authorization,
        files={"document": ("notes.txt", b"notes", "text/plain")},
    )

    assert response.status_code == 422
    assert isinstance(response.json()["detail"], list)


def test_hard_course_delete_removes_registered_document_file(upload_api) -> None:
    uploaded = upload_document(upload_api, "notes.txt", b"Course notes")
    assert uploaded.status_code == 201
    assert len(stored_files(upload_api.storage_root)) == 1

    deleted = upload_api.client.delete(
        f"/api/courses/{upload_api.course_id}",
        headers=upload_api.authorization,
    )

    assert deleted.status_code == 200
    assert deleted.json()["message"] == "Course permanently deleted"
    assert stored_files(upload_api.storage_root) == []
    assert document_count(upload_api) == 0


def test_failed_hard_delete_retains_metadata_and_can_be_retried(
    upload_api,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    uploaded = upload_document(upload_api, "notes.txt", b"Course notes")
    assert uploaded.status_code == 201
    original_delete = upload_api.storage.delete

    def fail_delete(_key: str) -> None:
        raise StorageError("simulated cleanup failure")

    monkeypatch.setattr(upload_api.storage, "delete", fail_delete)
    failed = upload_api.client.delete(
        f"/api/courses/{upload_api.course_id}",
        headers=upload_api.authorization,
    )

    assert failed.status_code == 500
    with upload_api.session_factory() as session:
        course = session.get(Course, upload_api.course_id)
        assert course is not None
        assert course.is_deleted is True
        assert session.scalar(select(func.count()).select_from(UploadedDocument)) == 1
    assert len(stored_files(upload_api.storage_root)) == 1

    monkeypatch.setattr(upload_api.storage, "delete", original_delete)
    retried = upload_api.client.delete(
        f"/api/courses/{upload_api.course_id}",
        headers=upload_api.authorization,
    )

    assert retried.status_code == 200
    assert stored_files(upload_api.storage_root) == []
    assert document_count(upload_api) == 0


def test_course_deleted_during_file_work_cleans_stored_file(
    upload_api,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_save = upload_api.storage.save

    def save_then_delete_course(key: str, source: BinaryIO) -> None:
        original_save(key, source)
        with upload_api.session_factory() as session:
            course = session.get(Course, upload_api.course_id)
            assert course is not None
            course.is_deleted = True
            session.commit()

    monkeypatch.setattr(upload_api.storage, "save", save_then_delete_course)

    response = upload_document(upload_api, "notes.txt", b"Course notes")

    assert response.status_code == 404
    assert document_count(upload_api) == 0
    assert stored_files(upload_api.storage_root) == []


def test_hard_delete_waits_for_upload_final_write_section(
    session_factory,
    model_graph,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = LocalStorage(tmp_path / "delete-upload-race")
    upload_at_insert = Event()
    delete_started = Event()
    allow_insert = Event()
    original_create = DocumentRepository.create

    def paused_create(db: Session, **values: object) -> UploadedDocument:
        upload_at_insert.set()
        assert allow_insert.wait(timeout=5)
        return original_create(db, **values)

    monkeypatch.setattr(DocumentRepository, "create", paused_create)

    def upload() -> None:
        with session_factory() as session:
            DocumentService.register(
                db=session,
                storage=storage,
                upload=UploadFile(BytesIO(b"racing content"), filename="notes.txt"),
                course_id=model_graph.course.id,
                user_id=model_graph.user.id,
            )

    def hard_delete() -> None:
        delete_started.set()
        with session_factory() as session:
            stored_documents = CourseService.prepare_hard_delete(
                session,
                model_graph.course.id,
            )
            for _provider, storage_key in stored_documents:
                storage.delete(storage_key)
            CourseService.finalize_hard_delete(session, model_graph.course.id)

    with ThreadPoolExecutor(max_workers=2) as executor:
        upload_future = executor.submit(upload)
        assert upload_at_insert.wait(timeout=5)
        delete_future = executor.submit(hard_delete)
        assert delete_started.wait(timeout=5)
        allow_insert.set()
        upload_future.result(timeout=10)
        delete_future.result(timeout=10)

    with session_factory() as session:
        assert session.get(Course, model_graph.course.id) is None
        assert session.scalar(select(func.count()).select_from(UploadedDocument)) == 0
    assert stored_files(storage.root) == []


def test_hard_delete_recovers_lost_commit_acknowledgement(
    db_session: Session,
    model_graph,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    CourseService.prepare_hard_delete(db_session, model_graph.course.id)
    original_commit = db_session.commit

    def commit_then_fail() -> None:
        original_commit()
        raise SQLAlchemyError("simulated lost delete acknowledgement")

    monkeypatch.setattr(db_session, "commit", commit_then_fail)

    CourseService.finalize_hard_delete(db_session, model_graph.course.id)

    assert db_session.get(Course, model_graph.course.id) is None


def test_main_app_openapi_describes_document_upload_contract() -> None:
    operation = app.openapi()["paths"]["/api/courses/{course_id}/documents"]["post"]

    assert {
        "200",
        "201",
        "400",
        "401",
        "403",
        "404",
        "409",
        "413",
        "415",
        "422",
        "500",
    } <= set(operation["responses"])
    assert operation["security"] == [{"OAuth2PasswordBearer": []}]


def test_corrupted_and_encrypted_content_is_admitted_at_upload_time(
    upload_api,
) -> None:
    """Request-time validation does not deep-parse content; deep validation is asynchronous."""
    corrupt_pdf = b"%PDF-1.7\ntruncated-corrupt-data"
    response = upload_document(
        upload_api,
        "corrupted.pdf",
        corrupt_pdf,
        "application/pdf",
    )
    assert response.status_code == 201
    payload = response.json()
    assert payload["duplicate"] is False
    assert payload["document"]["status"] == "uploaded"
    assert payload["document"]["file_type"] == "pdf"
    assert payload["document"]["file_size"] == len(corrupt_pdf)


def test_document_upload_persists_incoming_correlation_id_to_processing_job(
    upload_api,
) -> None:
    content = b"%PDF-1.4 sample pdf content"
    headers = dict(upload_api.authorization)
    headers["X-Request-ID"] = "req-corr-trace-777"

    response = upload_api.client.post(
        f"/api/courses/{upload_api.course_id}/documents",
        headers=headers,
        files={"document": ("correlated.pdf", content, "application/pdf")},
    )

    assert response.status_code == 201
    assert response.headers["X-Request-ID"] == "req-corr-trace-777"
    document_id = UUID(response.json()["document"]["id"])

    with upload_api.session_factory() as session:
        job = session.scalar(
            select(ProcessingJob).where(ProcessingJob.document_id == document_id)
        )
        assert job is not None
        assert job.correlation_id == "req-corr-trace-777"
