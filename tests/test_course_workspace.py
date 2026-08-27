"""Round-trip contract for the course workspace fields added by SCRUM-64.

``syllabus`` must survive create, read and update unchanged, and ``updated_at``
must move when a course changes while ``created_at`` stays put.
"""

from datetime import datetime, timezone

from backend.app.models import Course

MULTILINE_SYLLABUS = (
    "Week 1: Introduction\nWeek 2: Sorting\nWeek 3: Trees\nWeek 4: Graphs"
)
BACKDATED = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)


def _as_utc(value: str) -> datetime:
    """Normalize an API timestamp that SQLite returns naive and PostgreSQL aware."""
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _create_course(authz_api, **fields) -> dict:
    response = authz_api.client.post(
        "/api/courses/",
        headers=authz_api.authorization_a,
        json={"title": "Owner A workspace", **fields},
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]


def test_syllabus_round_trips_through_create_and_read(authz_api):
    created = _create_course(authz_api, syllabus="Week 1: Fundamentals")
    assert created["syllabus"] == "Week 1: Fundamentals"

    fetched = authz_api.client.get(
        f"/api/courses/{created['id']}", headers=authz_api.authorization_a
    )
    assert fetched.status_code == 200, fetched.text
    assert fetched.json()["data"]["syllabus"] == "Week 1: Fundamentals"


def test_multiline_syllabus_survives_the_round_trip(authz_api):
    created = _create_course(authz_api, syllabus=MULTILINE_SYLLABUS)

    fetched = authz_api.client.get(
        f"/api/courses/{created['id']}", headers=authz_api.authorization_a
    )
    assert fetched.status_code == 200, fetched.text
    assert fetched.json()["data"]["syllabus"] == MULTILINE_SYLLABUS


def test_a_course_created_without_a_syllabus_stores_null(authz_api):
    created = _create_course(authz_api)
    assert created["syllabus"] is None

    with authz_api.session_factory() as session:
        course = session.get(Course, created["id"])
        assert course is not None
        assert course.syllabus is None


def test_syllabus_round_trips_through_update(authz_api):
    created = _create_course(authz_api, syllabus="Week 1: Fundamentals")

    updated = authz_api.client.put(
        f"/api/courses/{created['id']}",
        headers=authz_api.authorization_a,
        json={"syllabus": MULTILINE_SYLLABUS},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["data"]["syllabus"] == MULTILINE_SYLLABUS

    fetched = authz_api.client.get(
        f"/api/courses/{created['id']}", headers=authz_api.authorization_a
    )
    assert fetched.json()["data"]["syllabus"] == MULTILINE_SYLLABUS


def test_updating_only_the_syllabus_leaves_other_fields_alone(authz_api):
    created = _create_course(
        authz_api,
        semester="Fall 2026",
        exam_date="2026-12-17",
        topics=["trees", "graphs"],
        syllabus="Week 1: Fundamentals",
    )

    updated = authz_api.client.put(
        f"/api/courses/{created['id']}",
        headers=authz_api.authorization_a,
        json={"syllabus": "Week 1: Revised"},
    )
    assert updated.status_code == 200, updated.text
    payload = updated.json()["data"]
    assert payload["syllabus"] == "Week 1: Revised"
    assert payload["semester"] == "Fall 2026"
    assert payload["exam_date"] == "2026-12-17"
    assert payload["topics"] == ["trees", "graphs"]
    assert payload["title"] == "Owner A workspace"


def test_a_syllabus_can_be_cleared(authz_api):
    created = _create_course(authz_api, syllabus="Week 1: Fundamentals")

    updated = authz_api.client.put(
        f"/api/courses/{created['id']}",
        headers=authz_api.authorization_a,
        json={"syllabus": None},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["data"]["syllabus"] is None


def test_creation_reports_updated_at(authz_api):
    created = _create_course(authz_api)
    assert "updated_at" in created
    assert _as_utc(created["updated_at"]) == _as_utc(created["created_at"])


def test_updating_a_course_advances_updated_at_without_moving_created_at(authz_api):
    """Backdate the row rather than sleeping: SQLite timestamps resolve to a second."""
    created = _create_course(authz_api, syllabus="Week 1: Fundamentals")

    with authz_api.session_factory() as session:
        course = session.get(Course, created["id"])
        assert course is not None
        course.created_at = BACKDATED
        course.updated_at = BACKDATED
        session.commit()

    updated = authz_api.client.put(
        f"/api/courses/{created['id']}",
        headers=authz_api.authorization_a,
        json={"syllabus": "Week 1: Revised"},
    )
    assert updated.status_code == 200, updated.text
    payload = updated.json()["data"]
    assert _as_utc(payload["created_at"]) == BACKDATED
    assert _as_utc(payload["updated_at"]) > BACKDATED

    fetched = authz_api.client.get(
        f"/api/courses/{created['id']}", headers=authz_api.authorization_a
    )
    assert fetched.json()["data"]["updated_at"] == payload["updated_at"]
