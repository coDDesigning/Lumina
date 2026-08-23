"""API-level authorization contract for course-scoped routes (SCRUM-63).

These tests drive the whole request path -- routing, authentication,
authorization, service, database -- because a service-level check cannot prove
that a route is actually wired to the boundary. Every denial is also checked for
side effects: a request that returns 404 must leave storage, rows, and job state
exactly as they were.
"""

from pathlib import Path

from fastapi.routing import APIRoute
from sqlalchemy import func, select

from backend.app.models import (
    Conversation,
    ConversationMessage,
    Course,
    DocumentChunk,
    DocumentPage,
    GeneratedOutput,
    ProcessingJob,
    Quiz,
    UploadedDocument,
)
from main import app

COUNTED_TABLES = (
    Course,
    Conversation,
    ConversationMessage,
    UploadedDocument,
    ProcessingJob,
    DocumentChunk,
    DocumentPage,
    GeneratedOutput,
    Quiz,
)


def _stored_files(root: Path) -> list[str]:
    if not root.exists():
        return []
    return sorted(
        path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()
    )


def _snapshot(context) -> dict[str, object]:
    """Capture everything a denied request must not be able to change."""
    with context.session_factory() as session:
        course = session.get(Course, context.a_course_id)
        document = session.get(UploadedDocument, context.a_document_id)
        job = session.scalar(
            select(ProcessingJob).where(
                ProcessingJob.document_id == context.a_document_id
            )
        )
        assert course is not None
        assert document is not None
        assert job is not None
        return {
            "counts": {
                table.__name__: session.scalar(select(func.count()).select_from(table))
                for table in COUNTED_TABLES
            },
            "course_title": course.title,
            "course_is_deleted": course.is_deleted,
            "document_status": document.status,
            "job_status": job.status,
            "job_attempt_count": job.attempt_count,
            "job_available_at": job.available_at,
            "files": _stored_files(context.storage_root),
        }


def _requests_against_owner_a(context) -> list[tuple[str, str, dict]]:
    """Every course-scoped operation aimed at user A's course and document."""
    course_id = context.a_course_id
    document_id = context.a_document_id
    return [
        ("GET", f"/api/courses/{course_id}", {}),
        (
            "PUT",
            f"/api/courses/{course_id}",
            {"json": {"title": "Renamed by an intruder"}},
        ),
        ("DELETE", f"/api/courses/{course_id}", {}),
        ("GET", f"/api/courses/{course_id}/documents", {}),
        ("GET", f"/api/courses/{course_id}/generated-outputs", {}),
        ("GET", f"/api/courses/{course_id}/generated-outputs/1", {}),
        ("GET", f"/api/courses/{course_id}/conversations", {}),
        ("GET", f"/api/courses/{course_id}/conversations/1", {}),
        ("DELETE", f"/api/courses/{course_id}/conversations/1", {}),
        (
            "POST",
            f"/api/courses/{course_id}/documents",
            {"files": {"document": ("intruder.txt", b"intruder", "text/plain")}},
        ),
        ("GET", f"/api/courses/{course_id}/documents/{document_id}", {}),
        ("POST", f"/api/courses/{course_id}/documents/{document_id}/retry", {}),
        ("DELETE", f"/api/courses/{course_id}/documents/{document_id}", {}),
        ("POST", f"/api/courses/{course_id}/study-guide", {}),
        ("POST", f"/api/courses/{course_id}/quiz", {}),
        ("POST", f"/api/courses/{course_id}/flashcards", {}),
        (
            "POST",
            f"/api/courses/{course_id}/ai-tutor",
            {"json": {"question": "What is on the exam?"}},
        ),
        (
            "POST",
            f"/api/courses/{course_id}/qa",
            {"json": {"question": "What is on the exam?"}},
        ),
        ("GET", f"/api/courses/{course_id}/quizzes/1/attempts", {}),
        ("GET", f"/api/courses/{course_id}/quizzes/1/attempts/1", {}),
    ]


def _course_ids(response) -> set[int]:
    return {course["id"] for course in response.json()["data"]}


def test_unauthenticated_course_requests_are_rejected_without_side_effects(authz_api):
    before = _snapshot(authz_api)

    unauthenticated = [
        ("GET", "/api/courses/", {}),
        ("POST", "/api/courses/", {"json": {"title": "Anonymous course"}}),
        *_requests_against_owner_a(authz_api),
    ]
    for method, url, kwargs in unauthenticated:
        response = authz_api.client.request(method, url, **kwargs)
        assert response.status_code == 401, f"{method} {url} -> {response.status_code}"

    assert _snapshot(authz_api) == before


def test_another_user_cannot_reach_a_course_or_its_documents(authz_api):
    before = _snapshot(authz_api)

    for method, url, kwargs in _requests_against_owner_a(authz_api):
        response = authz_api.client.request(
            method, url, headers=authz_api.authorization_b, **kwargs
        )
        assert response.status_code == 404, f"{method} {url} -> {response.status_code}"
        assert response.json() == {"detail": "Course not found"}

    assert _snapshot(authz_api) == before


def test_missing_and_other_owner_courses_are_indistinguishable(authz_api):
    """Absent, tombstoned, and other-owner courses must all answer alike."""
    absent = authz_api.client.get(
        "/api/courses/999999", headers=authz_api.authorization_b
    )
    other_owner = authz_api.client.get(
        f"/api/courses/{authz_api.a_course_id}", headers=authz_api.authorization_b
    )
    tombstoned = authz_api.client.get(
        f"/api/courses/{authz_api.a_deleted_course_id}",
        headers=authz_api.authorization_a,
    )

    assert absent.status_code == other_owner.status_code == tombstoned.status_code
    assert absent.json() == other_owner.json() == tombstoned.json()
    assert absent.json() == {"detail": "Course not found"}


def test_course_listing_is_scoped_to_the_caller(authz_api):
    owner_a = authz_api.client.get("/api/courses/", headers=authz_api.authorization_a)
    owner_b = authz_api.client.get("/api/courses/", headers=authz_api.authorization_b)

    assert owner_a.status_code == 200
    assert owner_b.status_code == 200
    # The tombstoned course is excluded even from its own owner's listing.
    assert _course_ids(owner_a) == {authz_api.a_course_id}
    assert _course_ids(owner_b) == {authz_api.b_course_id}
    assert all(
        course["owner_id"] == authz_api.user_a_id for course in owner_a.json()["data"]
    )


def test_administrator_lists_every_course_with_its_owner(authz_api):
    listed = authz_api.client.get(
        "/api/courses/", headers=authz_api.authorization_admin
    )

    assert listed.status_code == 200
    assert _course_ids(listed) == {authz_api.a_course_id, authz_api.b_course_id}
    owners = {course["id"]: course["owner_id"] for course in listed.json()["data"]}
    assert owners[authz_api.a_course_id] == authz_api.user_a_id
    assert owners[authz_api.b_course_id] == authz_api.user_b_id


def test_administrator_may_read_another_owners_course_and_documents(authz_api):
    course = authz_api.client.get(
        f"/api/courses/{authz_api.a_course_id}", headers=authz_api.authorization_admin
    )
    documents = authz_api.client.get(
        f"/api/courses/{authz_api.a_course_id}/documents",
        headers=authz_api.authorization_admin,
    )
    status = authz_api.client.get(
        f"/api/courses/{authz_api.a_course_id}/documents/{authz_api.a_document_id}",
        headers=authz_api.authorization_admin,
    )

    assert course.status_code == 200
    assert course.json()["data"]["owner_id"] == authz_api.user_a_id
    assert documents.status_code == 200
    assert [document["id"] for document in documents.json()["data"]] == [
        str(authz_api.a_document_id)
    ]
    assert status.status_code == 200
    assert status.json()["document"]["status"] == "failed"


def test_administrator_cannot_write_to_another_owners_course(authz_api):
    before = _snapshot(authz_api)

    writes = [
        entry for entry in _requests_against_owner_a(authz_api) if entry[0] != "GET"
    ]
    # Generated-output and conversation endpoints are reads, so they are absent.
    assert len(writes) == 11
    for method, url, kwargs in writes:
        response = authz_api.client.request(
            method, url, headers=authz_api.authorization_admin, **kwargs
        )
        assert response.status_code == 404, f"{method} {url} -> {response.status_code}"

    assert _snapshot(authz_api) == before


def test_course_creation_takes_ownership_from_the_token(authz_api):
    created = authz_api.client.post(
        "/api/courses/",
        headers=authz_api.authorization_b,
        json={
            "title": "Owner B second course",
            # A client-supplied owner must never be honoured.
            "owner_id": authz_api.user_a_id,
        },
    )

    assert created.status_code == 201
    assert created.json()["data"]["owner_id"] == authz_api.user_b_id
    with authz_api.session_factory() as session:
        course = session.get(Course, created.json()["data"]["id"])
        assert course is not None
        assert course.owner_id == authz_api.user_b_id


def test_documents_cannot_escape_their_authorized_course(authz_api):
    """An authorized course must not expose a document that belongs elsewhere."""
    other = authz_api.client.post(
        "/api/courses/",
        headers=authz_api.authorization_a,
        json={"title": "Owner A second course"},
    )
    assert other.status_code == 201
    other_course_id = other.json()["data"]["id"]
    before = _snapshot(authz_api)

    document_id = authz_api.a_document_id
    for method, url in (
        ("GET", f"/api/courses/{other_course_id}/documents/{document_id}"),
        ("POST", f"/api/courses/{other_course_id}/documents/{document_id}/retry"),
        ("DELETE", f"/api/courses/{other_course_id}/documents/{document_id}"),
    ):
        response = authz_api.client.request(
            method, url, headers=authz_api.authorization_a
        )
        assert response.status_code == 404, f"{method} {url} -> {response.status_code}"
        assert response.json() == {"detail": "Document not found"}

    assert _snapshot(authz_api) == before


def test_owner_keeps_the_full_document_workflow(authz_api):
    course_url = f"/api/courses/{authz_api.a_course_id}"

    course = authz_api.client.get(course_url, headers=authz_api.authorization_a)
    assert course.status_code == 200
    assert course.json()["data"]["owner_id"] == authz_api.user_a_id

    listed = authz_api.client.get(
        f"{course_url}/documents", headers=authz_api.authorization_a
    )
    assert listed.status_code == 200
    assert [document["id"] for document in listed.json()["data"]] == [
        str(authz_api.a_document_id)
    ]

    status = authz_api.client.get(
        f"{course_url}/documents/{authz_api.a_document_id}",
        headers=authz_api.authorization_a,
    )
    assert status.status_code == 200
    assert status.json()["processing_job"]["status"] == "failed"

    retried = authz_api.client.post(
        f"{course_url}/documents/{authz_api.a_document_id}/retry",
        headers=authz_api.authorization_a,
    )
    assert retried.status_code == 202
    assert retried.json()["document"]["status"] == "uploaded"
    assert retried.json()["processing_job"]["status"] == "queued"

    uploaded = authz_api.client.post(
        f"{course_url}/documents",
        headers=authz_api.authorization_a,
        files={"document": ("second.txt", b"Owner A second document", "text/plain")},
    )
    assert uploaded.status_code == 201

    renamed = authz_api.client.put(
        course_url,
        headers=authz_api.authorization_a,
        json={"title": "Owner A renamed course"},
    )
    assert renamed.status_code == 200
    assert renamed.json()["data"]["title"] == "Owner A renamed course"

    deleted = authz_api.client.delete(course_url, headers=authz_api.authorization_a)
    assert deleted.status_code == 200
    with authz_api.session_factory() as session:
        assert session.get(Course, authz_api.a_course_id) is None
    assert _stored_files(authz_api.storage_root) == []


def test_owner_can_delete_their_own_document(authz_api):
    deleted = authz_api.client.delete(
        f"/api/courses/{authz_api.a_course_id}/documents/{authz_api.a_document_id}",
        headers=authz_api.authorization_a,
    )

    assert deleted.status_code == 204
    with authz_api.session_factory() as session:
        assert session.get(UploadedDocument, authz_api.a_document_id) is None
    assert _stored_files(authz_api.storage_root) == []


def test_owner_can_still_purge_a_tombstoned_course(authz_api):
    """Hard deletion stays reachable after a course has been tombstoned."""
    purged = authz_api.client.delete(
        f"/api/courses/{authz_api.a_deleted_course_id}",
        headers=authz_api.authorization_a,
    )

    assert purged.status_code == 200
    with authz_api.session_factory() as session:
        assert session.get(Course, authz_api.a_deleted_course_id) is None


def test_another_user_cannot_purge_a_tombstoned_course(authz_api):
    purged = authz_api.client.delete(
        f"/api/courses/{authz_api.a_deleted_course_id}",
        headers=authz_api.authorization_b,
    )

    assert purged.status_code == 404
    with authz_api.session_factory() as session:
        assert session.get(Course, authz_api.a_deleted_course_id) is not None


def test_every_course_route_requires_authentication_in_openapi():
    schema = app.openapi()
    operations = [
        (method, path, operation)
        for path, methods in schema["paths"].items()
        if path.startswith("/api/courses")
        for method, operation in methods.items()
    ]

    assert len(operations) == 28
    for method, path, operation in operations:
        assert operation["security"] == [{"OAuth2PasswordBearer": []}], (
            f"{method.upper()} {path} is not documented as authenticated"
        )
        assert "401" in operation["responses"], f"{method.upper()} {path} lacks 401"


def test_denial_snapshot_is_sensitive_to_real_changes(authz_api):
    """Guard the regression tests above: the snapshot must notice mutations.

    If the fixture started empty, or the snapshot looked at the wrong storage
    root, every "nothing changed" assertion would pass without proving
    anything. This test pins both halves down.
    """
    before = _snapshot(authz_api)

    assert before["counts"]["Course"] == 3
    assert before["counts"]["UploadedDocument"] == 1
    assert before["counts"]["ProcessingJob"] == 1
    assert before["counts"]["Conversation"] == 0
    assert before["counts"]["ConversationMessage"] == 0
    assert before["files"] != []
    assert before["document_status"] == "failed"
    assert before["job_status"] == "failed"

    uploaded = authz_api.client.post(
        f"/api/courses/{authz_api.a_course_id}/documents",
        headers=authz_api.authorization_a,
        files={"document": ("sensitive.txt", b"a genuine change", "text/plain")},
    )

    assert uploaded.status_code == 201
    after = _snapshot(authz_api)
    assert after != before
    assert after["counts"]["UploadedDocument"] == 2
    assert len(after["files"]) == len(before["files"]) + 1


def test_course_scoped_routes_cannot_bypass_the_boundary():
    """Structural guard on the wiring, not just the behavior of current routes.

    A future endpoint that trusts its path parameter instead of the shared
    dependency would reintroduce exactly the vulnerability this ticket closes,
    so the dependency graph itself is asserted.
    """
    boundary = {
        "require_course_access",
        "require_course_owner",
        "require_course_deletion",
    }

    def api_routes(node):
        for route in getattr(node, "routes", []):
            if isinstance(route, APIRoute):
                yield route
            elif hasattr(route, "original_router"):
                yield from api_routes(route.original_router)

    def dependency_names(dependant):
        names = set()
        for sub in dependant.dependencies:
            if sub.call is not None:
                names.add(getattr(sub.call, "__name__", type(sub.call).__name__))
            names |= dependency_names(sub)
        return names

    course_routes = [
        route for route in api_routes(app) if route.path.startswith("/api/courses")
    ]
    assert len(course_routes) == 28

    for route in course_routes:
        names = dependency_names(route.dependant)
        assert "get_current_user" in names, f"{route.path} is unauthenticated"
        if "{course_id}" in route.path:
            assert names & boundary, (
                f"{route.path} does not use the course authorization boundary"
            )
