import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

from backend.app.models import (
    Course,
    DocumentChunk,
    ProcessingJob,
    UploadedDocument,
    User,
)
from tests.conftest import ApiHarness


def _register_and_login(
    harness: ApiHarness,
    email: str,
    *,
    name: str = "Student",
) -> tuple[dict[str, str], dict]:
    password = "password123"
    registration = harness.client.post(
        "/api/auth/register",
        json={"name": name, "email": email, "password": password},
    )
    assert registration.status_code == 200, registration.text
    login = harness.client.post(
        "/api/auth/login",
        data={"username": email.lower(), "password": password},
    )
    assert login.status_code == 200, login.text
    return (
        {"Authorization": f"Bearer {login.json()['access_token']}"},
        registration.json(),
    )


def _create_course(harness: ApiHarness, headers: dict[str, str], title: str) -> int:
    response = harness.client.post(
        "/api/courses/",
        headers=headers,
        json={
            "title": title,
            "description": "Private notes",
            "instructor": "Course owner",
            "price": 12.5,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]["id"]


def _upload_document(
    harness: ApiHarness,
    headers: dict[str, str],
    course_id: int,
    content: bytes = b"Durable course notes",
) -> dict:
    response = harness.client.post(
        f"/api/courses/{course_id}/documents",
        headers={**headers, "Idempotency-Key": "persistence-upload"},
        files={"document": ("notes.txt", content, "text/plain")},
    )
    assert response.status_code == 202, response.text
    return response.json()


def test_registration_is_persistent_normalized_and_role_seeded(api_harness):
    owner_headers, owner = _register_and_login(
        api_harness, "OWNER@Example.COM", name="Owner"
    )
    _, student = _register_and_login(api_harness, "student@example.com")

    assert owner["role"] == "admin"
    assert student["role"] == "user"
    assert (
        api_harness.client.get("/api/auth/me", headers=owner_headers).json()["email"]
        == "owner@example.com"
    )

    duplicate = api_harness.client.post(
        "/api/auth/register",
        json={
            "name": "Duplicate",
            "email": "owner@example.com",
            "password": "password123",
        },
    )
    assert duplicate.status_code == 400
    assert duplicate.json()["detail"] == "Email already registered"

    with api_harness.session_factory() as session:
        users = session.scalars(select(User).order_by(User.id)).all()
        assert len(users) == 2
        assert users[0].password_hash != "password123"
        assert not hasattr(users[0], "password")


def test_courses_are_private_to_their_owner(api_harness):
    owner_headers, _ = _register_and_login(api_harness, "owner@example.com")
    intruder_headers, _ = _register_and_login(api_harness, "other@example.com")
    course_id = _create_course(api_harness, owner_headers, "Owner course")

    owner_list = api_harness.client.get("/api/courses/", headers=owner_headers)
    intruder_list = api_harness.client.get("/api/courses/", headers=intruder_headers)
    assert [course["id"] for course in owner_list.json()["data"]] == [course_id]
    assert intruder_list.json()["data"] == []
    assert (
        api_harness.client.get(
            f"/api/courses/{course_id}", headers=intruder_headers
        ).status_code
        == 404
    )
    assert (
        api_harness.client.put(
            f"/api/courses/{course_id}",
            headers=intruder_headers,
            json={"title": "Stolen"},
        ).status_code
        == 404
    )

    deleted = api_harness.client.delete(
        f"/api/courses/{course_id}", headers=owner_headers
    )
    assert deleted.status_code == 200
    assert deleted.json()["data"]["is_deleted"] is True
    assert (
        api_harness.client.get("/api/courses/", headers=owner_headers).json()["data"]
        == []
    )

    restored = api_harness.client.put(
        f"/api/courses/{course_id}",
        headers=owner_headers,
        json={"is_deleted": False},
    )
    assert restored.status_code == 200
    assert restored.json()["data"]["is_deleted"] is False


def test_chunk_course_constraint_and_database_cascades(api_harness):
    owner_headers, _ = _register_and_login(api_harness, "owner@example.com")
    first_course_id = _create_course(api_harness, owner_headers, "First")
    second_course_id = _create_course(api_harness, owner_headers, "Second")
    upload = _upload_document(api_harness, owner_headers, first_course_id)

    with api_harness.session_factory() as session:
        session.add(
            DocumentChunk(
                document_id=upload["document_id"],
                course_id=second_course_id,
                chunk_index=0,
                text="Cross-course chunk",
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()

        session.add(
            DocumentChunk(
                document_id=upload["document_id"],
                course_id=first_course_id,
                chunk_index=0,
                text="Valid chunk",
            )
        )
        session.commit()
        session.execute(delete(Course).where(Course.id == first_course_id))
        session.commit()

        assert (
            session.scalar(
                select(func.count(UploadedDocument.id)).where(
                    UploadedDocument.id == upload["document_id"]
                )
            )
            == 0
        )
        assert (
            session.scalar(
                select(func.count(ProcessingJob.id)).where(
                    ProcessingJob.id == upload["job_id"]
                )
            )
            == 0
        )
        assert session.scalar(select(func.count(DocumentChunk.id))) == 0


def test_course_document_upload_is_idempotent_and_owner_scoped(api_harness):
    owner_headers, _ = _register_and_login(api_harness, "owner@example.com")
    intruder_headers, _ = _register_and_login(api_harness, "other@example.com")
    course_id = _create_course(api_harness, owner_headers, "Private uploads")
    url = f"/api/courses/{course_id}/documents"
    headers = {**owner_headers, "Idempotency-Key": "same-request"}

    first = api_harness.client.post(
        url,
        headers=headers,
        files={"document": ("notes.txt", b"Same notes", "text/plain")},
    )
    replay = api_harness.client.post(
        url,
        headers=headers,
        files={"document": ("notes.txt", b"Same notes", "text/plain")},
    )
    assert first.status_code == replay.status_code == 202
    assert first.json()["document_id"] == replay.json()["document_id"]
    assert first.json()["job_id"] == replay.json()["job_id"]
    assert len(list(api_harness.upload_directory.iterdir())) == 1

    conflict = api_harness.client.post(
        url,
        headers=headers,
        files={"document": ("notes.txt", b"Different notes", "text/plain")},
    )
    assert conflict.status_code == 409
    assert "different document content" in conflict.json()["detail"]

    hidden = api_harness.client.post(
        url,
        headers={**intruder_headers, "Idempotency-Key": "intruder"},
        files={"document": ("notes.txt", b"Intruder", "text/plain")},
    )
    assert hidden.status_code == 404
    assert len(list(api_harness.upload_directory.iterdir())) == 1

    status_response = api_harness.client.get(
        f"{url}/{first.json()['document_id']}", headers=owner_headers
    )
    assert status_response.status_code == 200
    assert status_response.json()["status"] == "queued"
    serialized = str(status_response.json())
    assert "storage_key" not in serialized
    assert "claim_token" not in serialized

    hard_delete = api_harness.client.delete(
        f"/api/courses/{course_id}?hard_delete=true", headers=owner_headers
    )
    assert hard_delete.status_code == 409
    assert "external storage cleanup" in hard_delete.json()["detail"]
