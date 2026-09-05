import os
import subprocess
import sys
import textwrap
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker

from backend.app.models import (
    Course,
    DocumentChunk,
    DocumentGenerationLock,
    ProcessingJob,
    Role,
    UploadedDocument,
    User,
)
from services.document import DocumentActiveError, DocumentService
from services.document_lock import (
    acquire_generation_locks,
    is_document_locked_for_generation,
    release_expired_generation_locks,
    reset_generation_locks,
)
from storage.local import LocalStorage

pytestmark = pytest.mark.database_contract

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# A holder in its own process, which is the only place a generation ever runs
# relative to the delete that must be blocked by it.
OUT_OF_PROCESS_HOLDER = """
import os
import sys
import time
from pathlib import Path
from uuid import UUID

os.environ.setdefault("OLLAMA_BASE_URL", "http://localhost:11434")
os.environ["DATABASE_URL"] = sys.argv[1]

from sqlalchemy.orm import Session

from backend.app.database_engine import create_database_engine
from services.document_lock import acquire_generation_locks

document_id = UUID(sys.argv[2])
held = Path(sys.argv[3])
release = Path(sys.argv[4])

engine = create_database_engine(sys.argv[1])
with Session(engine) as session:
    with acquire_generation_locks(session, [document_id]):
        held.write_text("held", encoding="utf-8")
        deadline = time.monotonic() + 60
        while not release.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
engine.dispose()
"""


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


def _other_session(db_session: Session) -> Session:
    """A session that shares nothing with the holder but the database itself."""
    return Session(bind=db_session.get_bind())


def test_a_hold_is_visible_to_a_session_that_did_not_take_it(db_session):
    doc_1 = uuid4()
    doc_2 = uuid4()

    with _other_session(db_session) as observer:
        assert not is_document_locked_for_generation(observer, doc_1)

    with acquire_generation_locks(db_session, [doc_1, doc_2]):
        with _other_session(db_session) as observer:
            assert is_document_locked_for_generation(observer, doc_1)
            assert is_document_locked_for_generation(observer, doc_2)

    with _other_session(db_session) as observer:
        assert not is_document_locked_for_generation(observer, doc_1)
        assert not is_document_locked_for_generation(observer, doc_2)


def test_shared_holds_on_one_document_release_independently(db_session):
    doc_id = uuid4()

    with acquire_generation_locks(db_session, [doc_id]):
        with acquire_generation_locks(db_session, [doc_id]):
            assert is_document_locked_for_generation(db_session, doc_id)
        # One generation finishing does not release the document for the other.
        assert is_document_locked_for_generation(db_session, doc_id)

    assert not is_document_locked_for_generation(db_session, doc_id)


def test_an_empty_document_set_takes_no_lock(db_session):
    with acquire_generation_locks(db_session, []):
        pass

    assert db_session.scalar(select(DocumentGenerationLock)) is None


def test_holds_are_released_when_the_generation_raises(db_session):
    doc_id = uuid4()

    with pytest.raises(RuntimeError, match="synthetic generation error"):
        with acquire_generation_locks(db_session, [doc_id]):
            assert is_document_locked_for_generation(db_session, doc_id)
            raise RuntimeError("synthetic generation error")

    assert not is_document_locked_for_generation(db_session, doc_id)


def test_an_expired_lease_no_longer_holds_the_document(db_session):
    doc_id = uuid4()
    expired = datetime.now(timezone.utc) - timedelta(minutes=1)
    db_session.add(
        DocumentGenerationLock(
            document_id=doc_id,
            holder_token=uuid4(),
            holder="worker-that-was-killed:1",
            acquired_at=expired - timedelta(minutes=30),
            expires_at=expired,
        )
    )
    db_session.commit()

    # A process that died holding the lock cannot release its own row, so the
    # lease is what stops it from blocking this document forever.
    assert not is_document_locked_for_generation(db_session, doc_id)
    assert release_expired_generation_locks(db_session) == 1
    assert db_session.scalar(select(DocumentGenerationLock)) is None


def test_a_hold_is_refused_for_a_document_already_being_deleted(db_session, tmp_path):
    storage = LocalStorage(tmp_path / "lock-uploads", namespace="lock-test")
    _, document, _ = _seed_document(db_session, storage, "tombstoned-hold@example.com")
    document.status = "deleting"
    db_session.commit()

    with acquire_generation_locks(db_session, [document.id]):
        # The deleter is already past its own check, so this hold must not
        # exist: it would block a deletion that cannot be called off.
        assert not is_document_locked_for_generation(db_session, document.id)


def test_reset_generation_locks_clears_every_hold(db_session):
    doc_id = uuid4()

    with acquire_generation_locks(db_session, [doc_id]):
        reset_generation_locks(db_session)
        assert not is_document_locked_for_generation(db_session, doc_id)


def test_delete_document_blocked_while_locked_for_generation(db_session, tmp_path):
    storage = LocalStorage(tmp_path / "lock-uploads", namespace="lock-test")
    course, document, _ = _seed_document(
        db_session, storage, "locked-delete@example.com"
    )

    with acquire_generation_locks(db_session, [document.id]):
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


def test_delete_is_blocked_by_a_lock_held_in_another_process(
    db_session: Session,
    database_engine: Engine,
    session_factory: sessionmaker[Session],
    tmp_path,
):
    """BUG-004: generation runs in the worker process, deletion in the API's."""
    storage = LocalStorage(tmp_path / "lock-uploads", namespace="lock-test")
    course, document, _ = _seed_document(
        db_session, storage, "cross-process-delete@example.com"
    )
    course_id = course.id
    document_id = document.id
    db_session.commit()
    db_session.close()

    holder_script = tmp_path / "generation_holder.py"
    holder_script.write_text(textwrap.dedent(OUT_OF_PROCESS_HOLDER), encoding="utf-8")
    held = tmp_path / "held.marker"
    release = tmp_path / "release.marker"
    database_url = database_engine.url.render_as_string(hide_password=False)

    holder = subprocess.Popen(
        [
            sys.executable,
            str(holder_script),
            database_url,
            str(document_id),
            str(held),
            str(release),
        ],
        cwd=str(PROJECT_ROOT),
        env={**os.environ, "PYTHONPATH": str(PROJECT_ROOT)},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = datetime.now(timezone.utc) + timedelta(seconds=60)
        while not held.exists() and datetime.now(timezone.utc) < deadline:
            if holder.poll() is not None:
                _, errors = holder.communicate()
                raise AssertionError(f"generation holder exited early: {errors}")
        assert held.exists(), "the other process never reported holding the lock"

        with session_factory() as api_session:
            assert is_document_locked_for_generation(api_session, document_id)
            with pytest.raises(DocumentActiveError):
                DocumentService.delete_document(
                    api_session,
                    storage,
                    document_id,
                    course_id,
                )
    finally:
        release.write_text("release", encoding="utf-8")
        stdout, errors = holder.communicate(timeout=60)
    assert holder.returncode == 0, f"{stdout}\n{errors}"

    # The document survived the blocked delete untouched, and once the other
    # process let go it can be deleted normally.
    with session_factory() as api_session:
        surviving = api_session.get(UploadedDocument, document_id)
        assert surviving is not None
        assert surviving.status == "ready"
        assert not is_document_locked_for_generation(api_session, document_id)
        DocumentService.delete_document(
            api_session,
            storage,
            document_id,
            course_id,
        )
        assert api_session.get(UploadedDocument, document_id) is None


def test_delete_document_http_409_when_locked_for_generation(authz_api):
    doc_id = authz_api.a_document_id

    with authz_api.session_factory() as holder_session:
        with acquire_generation_locks(holder_session, [doc_id]):
            response = authz_api.client.delete(
                f"/api/courses/{authz_api.a_course_id}/documents/{doc_id}",
                headers=authz_api.authorization_a,
            )
            assert response.status_code == 409
            assert (
                "cannot be deleted while it is being processed"
                in response.json()["detail"]
            )

    # Deleting without lock succeeds
    response = authz_api.client.delete(
        f"/api/courses/{authz_api.a_course_id}/documents/{doc_id}",
        headers=authz_api.authorization_a,
    )
    assert response.status_code == 204
