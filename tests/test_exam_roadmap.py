"""The study roadmap: arithmetic over a plan, free, and the same twice."""

import pytest
from sqlalchemy import select

import routes.exam_mode as exam_mode_route
from backend.app.models import OUTPUT_TYPE_EXAM_ROADMAP, GeneratedOutput, User
from services.exam_roadmap import (
    DEFAULT_DAYS,
    MAX_DAYS,
    RoadmapTopic,
    build_roadmap,
    resolve_day_count,
)
from tests.test_exam_mode import (  # noqa: F401 - fixtures
    FUTURE_EXAM_DATE,
    create_plan,
    exam_course,
    run_analysis,
    set_exam_date,
)
from utils.ai_errors import AiErrorCode


def topic(index: int) -> RoadmapTopic:
    return RoadmapTopic(
        topic_key=f"topic-{index}",
        display_label=f"Topic {index}",
        rank=index,
        priority_band="high" if index == 1 else "medium",
        is_high_priority=index == 1,
    )


def balance_of(session_factory, user_id: int):
    with session_factory() as session:
        return session.get(User, user_id).credits


@pytest.fixture
def planned_course(authz_api, exam_course, monkeypatch):  # noqa: F811
    response, _ = run_analysis(authz_api, monkeypatch)
    analysis_id = response.json()["data"]["analysis"]["generated_output_id"]
    created = create_plan(
        authz_api,
        {
            "analysis_output_id": analysis_id,
            "selected_topic_keys": ["graph-traversal", "dynamic-programming"],
        },
    )
    assert created.status_code == 200, created.text
    return {
        **exam_course,
        "analysis_id": analysis_id,
        "plan_id": created.json()["data"]["generated_output_id"],
    }


def ask(authz_api, *, json=None, headers=None):
    return authz_api.client.post(
        f"/api/courses/{authz_api.a_course_id}/exam-mode/roadmap",
        json=json if json is not None else {},
        headers=headers if headers is not None else authz_api.authorization_a,
    )


# --------------------------------------------------------------- arithmetic


def test_topics_are_front_loaded_and_the_last_day_is_review() -> None:
    roadmap = build_roadmap([topic(index) for index in range(1, 6)], day_count=4)

    assert [len(day.topics) for day in roadmap.days] == [2, 2, 1, 0]
    assert roadmap.days[-1].is_review is True
    assert roadmap.days[0].topics[0].display_label == "Topic 1"


def test_every_topic_lands_on_exactly_one_day() -> None:
    topics = [topic(index) for index in range(1, 8)]

    roadmap = build_roadmap(topics, day_count=5)

    scheduled = [entry.topic_key for day in roadmap.days for entry in day.topics]
    assert sorted(scheduled) == sorted(entry.topic_key for entry in topics)
    assert len(scheduled) == len(set(scheduled))
    assert roadmap.unscheduled_topics == ()


def test_a_single_day_is_studied_rather_than_reviewed() -> None:
    """With one day there is nothing to have forgotten yet."""
    roadmap = build_roadmap([topic(1), topic(2)], day_count=1)

    assert roadmap.day_count == 1
    assert roadmap.days[0].is_review is False
    assert len(roadmap.days[0].topics) == 2


def test_spare_days_stay_empty_rather_than_being_filled() -> None:
    roadmap = build_roadmap([topic(1), topic(2)], day_count=6)

    assert [len(day.topics) for day in roadmap.days] == [1, 1, 0, 0, 0, 0]
    assert roadmap.days[2].title == "Catch up"


def test_a_plan_with_no_topics_still_yields_a_readable_schedule() -> None:
    roadmap = build_roadmap([], day_count=3)

    assert roadmap.topic_count == 0
    assert len(roadmap.days) == 3
    assert roadmap.days[-1].is_review is True


def test_the_same_plan_yields_the_same_roadmap_twice() -> None:
    topics = [topic(index) for index in range(1, 6)]

    assert build_roadmap(topics, day_count=4) == build_roadmap(
        list(reversed(topics)), day_count=4
    )


def test_the_day_count_is_bounded_and_a_request_wins() -> None:
    assert resolve_day_count(None, None) == DEFAULT_DAYS
    assert resolve_day_count(0, None) == 1
    assert resolve_day_count(400, None) == MAX_DAYS
    assert resolve_day_count(30, 3) == 3
    assert resolve_day_count(None, 400) == MAX_DAYS


def test_days_are_labelled_generically_and_never_by_date() -> None:
    """A roadmap must survive a student starting late and the exam passing."""
    roadmap = build_roadmap([topic(1), topic(2)], day_count=3)

    assert [day.label for day in roadmap.days] == ["Day 1", "Day 2", "Day 3"]
    for day in roadmap.days:
        assert "-" not in day.label
        assert not any(character.isdigit() for character in day.label.split()[0])


# --------------------------------------------------------------- the endpoint


def test_a_roadmap_is_created_free_and_with_no_model_attribution(
    authz_api, planned_course, monkeypatch
) -> None:
    before = balance_of(authz_api.session_factory, authz_api.user_a_id)

    def forbidden(*args, **kwargs):
        raise AssertionError("a roadmap must never reach a provider")

    monkeypatch.setattr(exam_mode_route, "get_text_generation_provider", forbidden)
    response = ask(authz_api)

    assert response.status_code == 200, response.text
    data = response.json()["data"]["roadmap"]
    assert data["plan_output_id"] == planned_course["plan_id"]
    assert data["topic_count"] == 2
    assert balance_of(authz_api.session_factory, authz_api.user_a_id) == before

    with authz_api.session_factory() as session:
        output = session.scalars(
            select(GeneratedOutput).where(
                GeneratedOutput.course_id == authz_api.a_course_id,
                GeneratedOutput.output_type == OUTPUT_TYPE_EXAM_ROADMAP,
            )
        ).one()
        assert output.model_used is None


def test_the_roadmap_is_deliberately_absent_from_the_price_table(authz_api) -> None:
    policy = authz_api.client.get(
        "/api/users/me/credits", headers=authz_api.authorization_a
    ).json()["data"]

    assert "exam_roadmap" not in policy["generation_costs"]


def test_a_requested_day_count_is_honoured(authz_api, planned_course) -> None:
    response = ask(authz_api, json={"day_count": 3})

    data = response.json()["data"]["roadmap"]
    assert data["day_count"] == 3
    assert [day["label"] for day in data["days"]] == ["Day 1", "Day 2", "Day 3"]


def test_it_still_renders_after_the_exam_date_has_passed(
    authz_api, planned_course
) -> None:
    """A roadmap is a study resource; it does not expire with its exam."""
    from datetime import date, timedelta

    set_exam_date(
        authz_api.session_factory,
        authz_api.a_course_id,
        date.today() - timedelta(days=3),
    )

    response = ask(authz_api)

    assert response.status_code == 200, response.text
    data = response.json()["data"]["roadmap"]
    assert data["day_count"] >= 1
    assert all(day["label"].startswith("Day ") for day in data["days"])


def test_a_course_with_no_plan_is_told_to_make_one(
    authz_api,
    exam_course,  # noqa: F811
) -> None:
    response = ask(authz_api)

    assert response.status_code == 409
    assert response.headers["X-Error-Code"] == AiErrorCode.EXAM_PLAN_REQUIRED


def test_it_is_reopened_without_a_provider(authz_api, planned_course, monkeypatch):
    ask(authz_api)

    def forbidden(*args, **kwargs):
        raise AssertionError("reopening a roadmap must never reach a provider")

    monkeypatch.setattr(exam_mode_route, "get_text_generation_provider", forbidden)
    response = authz_api.client.get(
        f"/api/courses/{authz_api.a_course_id}/exam-mode/roadmap",
        headers=authz_api.authorization_a,
    )

    assert response.status_code == 200, response.text
    assert response.json()["data"]["days"]


def test_a_stranger_and_an_administrator_are_both_refused_a_write(
    authz_api, planned_course
) -> None:
    for headers in (authz_api.authorization_b, authz_api.authorization_admin):
        assert ask(authz_api, headers=headers).status_code == 404

    with authz_api.session_factory() as session:
        assert (
            session.scalars(
                select(GeneratedOutput).where(
                    GeneratedOutput.course_id == authz_api.a_course_id,
                    GeneratedOutput.output_type == OUTPUT_TYPE_EXAM_ROADMAP,
                )
            ).all()
            == []
        )
