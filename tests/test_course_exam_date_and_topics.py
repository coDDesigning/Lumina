"""Contract for SCRUM-154: exam_date is a real date and topics are rows.

``exam_date`` carries a database date, so the API rejects text that is not an
ISO date and course listing orders chronologically. ``topics`` is a child table
rather than a comma-joined blob, so a topic containing a comma survives the
round trip and topic lookups are ordinary course-scoped SQL.
"""

from datetime import date

from sqlalchemy import event, select

from backend.app.models import Course, CourseTopic

COMMA_TOPIC = "Trees, Heaps and Priority Queues"


def _create_course(authz_api, **fields) -> dict:
    response = authz_api.client.post(
        "/api/courses/",
        headers=authz_api.authorization_a,
        json={"title": "Owner A workspace", **fields},
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]


def test_exam_date_round_trips_as_an_iso_date(authz_api):
    created = _create_course(authz_api, exam_date="2026-12-17")
    assert created["exam_date"] == "2026-12-17"

    with authz_api.session_factory() as session:
        course = session.get(Course, created["id"])
        assert course is not None
        assert course.exam_date == date(2026, 12, 17)


def test_a_bare_year_exam_date_is_rejected(authz_api):
    response = authz_api.client.post(
        "/api/courses/",
        headers=authz_api.authorization_a,
        json={"title": "Loose date", "exam_date": "2026"},
    )
    assert response.status_code == 422, response.text


def test_a_blank_exam_date_is_stored_as_null(authz_api):
    created = _create_course(authz_api, exam_date="")
    assert created["exam_date"] is None

    with authz_api.session_factory() as session:
        course = session.get(Course, created["id"])
        assert course is not None
        assert course.exam_date is None


def test_an_exam_date_can_be_cleared(authz_api):
    created = _create_course(authz_api, exam_date="2026-12-17")

    updated = authz_api.client.put(
        f"/api/courses/{created['id']}",
        headers=authz_api.authorization_a,
        json={"exam_date": None},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["data"]["exam_date"] is None


def test_course_listing_orders_by_exam_date_with_undated_courses_last(authz_api):
    _create_course(authz_api, title="Later", exam_date="2026-12-17")
    _create_course(authz_api, title="Undated")
    _create_course(authz_api, title="Sooner", exam_date="2026-09-04")

    listed = authz_api.client.get("/api/courses/", headers=authz_api.authorization_a)
    assert listed.status_code == 200, listed.text
    mine = {"Sooner", "Later", "Undated"}
    titles = [
        course["title"] for course in listed.json()["data"] if course["title"] in mine
    ]

    assert titles == ["Sooner", "Later", "Undated"]


def test_a_topic_containing_a_comma_survives_the_round_trip(authz_api):
    created = _create_course(authz_api, topics=[COMMA_TOPIC, "Shortest Paths"])
    assert created["topics"] == [COMMA_TOPIC, "Shortest Paths"]

    fetched = authz_api.client.get(
        f"/api/courses/{created['id']}", headers=authz_api.authorization_a
    )
    assert fetched.status_code == 200, fetched.text
    assert fetched.json()["data"]["topics"] == [COMMA_TOPIC, "Shortest Paths"]


def test_topics_keep_the_order_they_were_written_in(authz_api):
    ordered = ["Graphs", "Arrays", "Heaps", "Tries"]
    created = _create_course(authz_api, topics=ordered)

    with authz_api.session_factory() as session:
        course = session.get(Course, created["id"])
        assert course is not None
        assert course.topics == ordered


def test_a_course_created_without_topics_has_an_empty_list(authz_api):
    created = _create_course(authz_api)
    assert created["topics"] == []


def test_topics_are_deduplicated_case_insensitively_keeping_the_first_casing(
    authz_api,
):
    created = _create_course(authz_api, topics=["Graphs", "graphs", "GRAPHS"])
    assert created["topics"] == ["Graphs"]


def test_blank_and_whitespace_only_topics_are_dropped(authz_api):
    created = _create_course(authz_api, topics=["  Graphs  ", "", "   ", "Trees"])
    assert created["topics"] == ["Graphs", "Trees"]


def test_topics_round_trip_through_update(authz_api):
    created = _create_course(authz_api, topics=["Graphs"])

    updated = authz_api.client.put(
        f"/api/courses/{created['id']}",
        headers=authz_api.authorization_a,
        json={"topics": [COMMA_TOPIC, "Graphs"]},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["data"]["topics"] == [COMMA_TOPIC, "Graphs"]


def test_topics_can_be_cleared_with_an_empty_list(authz_api):
    created = _create_course(authz_api, topics=["Graphs", "Trees"])

    updated = authz_api.client.put(
        f"/api/courses/{created['id']}",
        headers=authz_api.authorization_a,
        json={"topics": []},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["data"]["topics"] == []

    with authz_api.session_factory() as session:
        remaining = session.scalars(
            select(CourseTopic).where(CourseTopic.course_id == created["id"])
        ).all()
        assert remaining == []


def test_an_update_that_omits_topics_leaves_them_unchanged(authz_api):
    created = _create_course(authz_api, topics=["Graphs", "Trees"])

    updated = authz_api.client.put(
        f"/api/courses/{created['id']}",
        headers=authz_api.authorization_a,
        json={"semester": "Fall 2026"},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["data"]["topics"] == ["Graphs", "Trees"]


def test_a_topic_over_one_hundred_characters_is_rejected(authz_api):
    response = authz_api.client.post(
        "/api/courses/",
        headers=authz_api.authorization_a,
        json={"title": "Long topic", "topics": ["x" * 101]},
    )
    assert response.status_code == 422, response.text


def test_a_topic_containing_a_nul_character_is_rejected(authz_api):
    response = authz_api.client.post(
        "/api/courses/",
        headers=authz_api.authorization_a,
        json={"title": "NUL topic", "topics": ["Graphs\x00"]},
    )
    assert response.status_code == 422, response.text


def test_more_than_fifty_topics_are_rejected(authz_api):
    response = authz_api.client.post(
        "/api/courses/",
        headers=authz_api.authorization_a,
        json={"title": "Too many", "topics": [f"Topic {n}" for n in range(51)]},
    )
    assert response.status_code == 422, response.text


def test_a_topic_query_is_scoped_to_one_course(authz_api):
    mine = _create_course(authz_api, title="Mine", topics=[COMMA_TOPIC, "Graphs"])
    other = _create_course(authz_api, title="Other", topics=[COMMA_TOPIC])

    with authz_api.session_factory() as session:
        matches = session.scalars(
            select(CourseTopic.name)
            .where(
                CourseTopic.course_id == mine["id"],
                CourseTopic.name == COMMA_TOPIC,
            )
            .order_by(CourseTopic.position)
        ).all()
        assert list(matches) == [COMMA_TOPIC]

        courses_with_topic = session.scalars(
            select(Course.title)
            .join(CourseTopic, CourseTopic.course_id == Course.id)
            .where(CourseTopic.name == COMMA_TOPIC)
            .order_by(Course.id)
        ).all()
        assert list(courses_with_topic) == ["Mine", "Other"]

        assert (
            session.scalar(
                select(CourseTopic.name).where(
                    CourseTopic.course_id == other["id"],
                    CourseTopic.name == "Graphs",
                )
            )
            is None
        )


def test_deleting_a_course_deletes_its_topics(authz_api):
    created = _create_course(authz_api, topics=["Graphs", "Trees"])

    deleted = authz_api.client.delete(
        f"/api/courses/{created['id']}", headers=authz_api.authorization_a
    )
    assert deleted.status_code == 200, deleted.text

    with authz_api.session_factory() as session:
        remaining = session.scalars(
            select(CourseTopic).where(CourseTopic.course_id == created["id"])
        ).all()
        assert remaining == []


def test_listing_courses_loads_every_topic_set_in_one_query(authz_api, database_engine):
    for index in range(4):
        _create_course(
            authz_api,
            title=f"Course {index}",
            topics=[f"Topic {index}a", f"Topic {index}b"],
        )

    statements: list[str] = []

    def record(conn, cursor, statement, parameters, context, executemany):
        if "course_topics" in statement and statement.lstrip().upper().startswith(
            "SELECT"
        ):
            statements.append(statement)

    event.listen(database_engine, "before_cursor_execute", record)
    try:
        listed = authz_api.client.get(
            "/api/courses/", headers=authz_api.authorization_a
        )
    finally:
        event.remove(database_engine, "before_cursor_execute", record)

    assert listed.status_code == 200, listed.text
    assert len(statements) == 1, (
        f"listing issued {len(statements)} topic queries; a per-course lazy load "
        "is an N+1 that grows with the number of courses"
    )
