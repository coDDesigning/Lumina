"""The student workspace journey, and the administrator override as stated policy.

``tests/test_course_authorization.py`` already proves the denials. This module
proves the positive path a student is meant to walk with no administrator
involved, and pins the override predicates so a future edit to them fails here.
"""

from sqlalchemy import select

from backend.app.models import Course
from schemas.user import Role, UserResponse
from utils.authorization import can_read_any_course, can_write_any_course


def _user(role: Role) -> UserResponse:
    return UserResponse(
        id=1,
        name="Policy probe",
        email="policy-probe@example.com",
        role=role,
        is_banned=False,
        credits=None,
        preferred_model="gpt-4o-mini",
    )


def test_a_student_creates_several_workspaces_without_an_administrator(authz_api):
    titles = ["CS 201", "MATH 102", "PHYS 110"]
    created_ids = []
    for title in titles:
        response = authz_api.client.post(
            "/api/courses/",
            headers=authz_api.authorization_b,
            json={"title": title, "semester": "Fall 2026"},
        )
        assert response.status_code == 201, response.text
        payload = response.json()["data"]
        assert payload["owner_id"] == authz_api.user_b_id
        created_ids.append(payload["id"])

    with authz_api.session_factory() as session:
        owners = session.scalars(
            select(Course.owner_id).where(Course.id.in_(created_ids))
        ).all()
    assert set(owners) == {authz_api.user_b_id}

    listed = authz_api.client.get("/api/courses/", headers=authz_api.authorization_b)
    assert listed.status_code == 200, listed.text
    listed_titles = {course["title"] for course in listed.json()["data"]}
    assert set(titles) <= listed_titles
    assert all(
        course["owner_id"] == authz_api.user_b_id for course in listed.json()["data"]
    )


def test_a_student_edits_and_deletes_their_own_workspace(authz_api):
    created = authz_api.client.post(
        "/api/courses/",
        headers=authz_api.authorization_b,
        json={"title": "Owner B disposable course"},
    )
    assert created.status_code == 201, created.text
    course_id = created.json()["data"]["id"]

    updated = authz_api.client.put(
        f"/api/courses/{course_id}",
        headers=authz_api.authorization_b,
        json={"title": "Owner B renamed course", "syllabus": "Week 1: Fundamentals"},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["data"]["title"] == "Owner B renamed course"
    assert updated.json()["data"]["syllabus"] == "Week 1: Fundamentals"

    deleted = authz_api.client.delete(
        f"/api/courses/{course_id}", headers=authz_api.authorization_b
    )
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["data"]["is_deleted"] is True

    gone = authz_api.client.get(
        f"/api/courses/{course_id}", headers=authz_api.authorization_b
    )
    assert gone.status_code == 404
    assert gone.json() == {"detail": "Course not found"}


def test_a_student_cannot_edit_or_delete_another_students_workspace(authz_api):
    course_url = f"/api/courses/{authz_api.a_course_id}"

    updated = authz_api.client.put(
        course_url,
        headers=authz_api.authorization_b,
        json={"syllabus": "Injected by another student"},
    )
    assert updated.status_code == 404
    assert updated.json() == {"detail": "Course not found"}

    deleted = authz_api.client.delete(course_url, headers=authz_api.authorization_b)
    assert deleted.status_code == 404

    with authz_api.session_factory() as session:
        course = session.get(Course, authz_api.a_course_id)
        assert course is not None
        assert course.syllabus is None
        assert course.is_deleted is False


def test_the_administrator_override_is_read_only_by_policy():
    """The override lives in two predicates; both are asserted here on purpose.

    Widening administrator power should require editing this test, not merely
    editing ``utils/authorization.py``.
    """
    administrator = _user(Role.ADMIN)
    student = _user(Role.USER)

    assert can_read_any_course(administrator) is True
    assert can_write_any_course(administrator) is False
    assert can_read_any_course(student) is False
    assert can_write_any_course(student) is False


def test_the_administrator_override_reads_but_does_not_write(authz_api):
    course_url = f"/api/courses/{authz_api.a_course_id}"

    read = authz_api.client.get(course_url, headers=authz_api.authorization_admin)
    assert read.status_code == 200, read.text
    assert read.json()["data"]["owner_id"] == authz_api.user_a_id

    updated = authz_api.client.put(
        course_url,
        headers=authz_api.authorization_admin,
        json={"syllabus": "Injected by an administrator"},
    )
    assert updated.status_code == 404

    deleted = authz_api.client.delete(course_url, headers=authz_api.authorization_admin)
    assert deleted.status_code == 404

    with authz_api.session_factory() as session:
        course = session.get(Course, authz_api.a_course_id)
        assert course is not None
        assert course.syllabus is None
        assert course.is_deleted is False
