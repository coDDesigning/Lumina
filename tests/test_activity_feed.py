from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

import routes.quiz as quiz_route
from backend.app.models import GeneratedOutput, Quiz, QuizAttempt, QuizQuestion
from generation_fixtures import RecordingProvider, quiz_payload, seed_ready_material

BASE = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)


def _activity(client, headers, **params) -> list[dict]:
    response = client.get("/api/activity", headers=headers, params=params)
    assert response.status_code == 200, response.text
    return response.json()["data"]


def _output(
    session,
    course_id: int,
    user_id: int | None,
    output_type: str,
    *,
    created_at: datetime,
    settings: str | None = None,
    content: str = "{}",
) -> GeneratedOutput:
    row = GeneratedOutput(
        course_id=course_id,
        user_id=user_id,
        output_type=output_type,
        content=content,
        created_at=created_at,
        generation_settings=settings,
    )
    session.add(row)
    session.flush()
    return row


def _quiz_with_questions(session, course_id: int, count: int = 2) -> Quiz:
    quiz = Quiz(course_id=course_id, title="Activity Quiz")
    session.add(quiz)
    session.flush()

    for index in range(count):
        session.add(
            QuizQuestion(
                quiz_id=quiz.id,
                question_index=index,
                question_text=f"Question {index + 1}?",
                options=["Option A", "Option B", "Option C", "Option D"],
                correct_option_index=0,
                topic="Algebra",
                explanation="Option A is correct.",
            )
        )

    session.commit()
    session.refresh(quiz)
    return quiz


def _question_ids(session, quiz_id: int) -> list[int]:
    return [
        row.id
        for row in session.scalars(
            select(QuizQuestion)
            .where(QuizQuestion.quiz_id == quiz_id)
            .order_by(QuizQuestion.question_index)
        ).all()
    ]


def _submit(api, course_id: int, quiz_id: int, question_ids: list[int]) -> dict:
    response = api.client.post(
        f"/api/courses/{course_id}/quizzes/{quiz_id}/attempts",
        json={
            "answers": [
                {"question_id": question_id, "selected_option_index": 0}
                for question_id in question_ids
            ]
        },
        headers=api.authorization,
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]


def test_activity_combines_generations_and_attempts_newest_first(upload_api) -> None:
    with upload_api.session_factory() as session:
        quiz = _quiz_with_questions(session, upload_api.course_id)
        question_ids = _question_ids(session, quiz.id)
        _output(
            session,
            upload_api.course_id,
            upload_api.user_id,
            "study_guide",
            created_at=BASE,
        )
        _output(
            session,
            upload_api.other_course_id,
            upload_api.user_id,
            "flashcards",
            created_at=BASE + timedelta(days=1),
        )
        session.commit()

    _submit(upload_api, upload_api.course_id, quiz.id, question_ids)

    items = _activity(upload_api.client, upload_api.authorization)

    assert [item["action_type"] for item in items] == [
        "quiz_attempt",
        "flashcards",
        "study_guide",
    ]
    assert [item["kind"] for item in items] == ["attempt", "generation", "generation"]
    assert [item["course_id"] for item in items] == [
        upload_api.course_id,
        upload_api.other_course_id,
        upload_api.course_id,
    ]


def test_every_item_identifies_its_course_and_when_it_happened(upload_api) -> None:
    with upload_api.session_factory() as session:
        _output(
            session,
            upload_api.course_id,
            upload_api.user_id,
            "study_guide",
            created_at=BASE,
            settings='{"topic_focus": "Graph Algorithms"}',
        )
        session.commit()

    item = _activity(upload_api.client, upload_api.authorization)[0]

    assert item["course_id"] == upload_api.course_id
    assert item["course_title"] == "Active Course"
    assert item["action_type"] == "study_guide"
    assert datetime.fromisoformat(item["occurred_at"]) == BASE
    assert item["topic"] == "Graph Algorithms"


def test_a_whole_course_generation_reports_no_topic(upload_api) -> None:
    with upload_api.session_factory() as session:
        _output(
            session,
            upload_api.course_id,
            upload_api.user_id,
            "study_guide",
            created_at=BASE,
            settings='{"topic_focus": "All Topics"}',
        )
        session.commit()

    assert _activity(upload_api.client, upload_api.authorization)[0]["topic"] is None


def test_an_unreadable_settings_document_does_not_fail_the_read(upload_api) -> None:
    with upload_api.session_factory() as session:
        _output(
            session,
            upload_api.course_id,
            upload_api.user_id,
            "study_guide",
            created_at=BASE,
            settings="not json at all",
        )
        session.commit()

    item = _activity(upload_api.client, upload_api.authorization)[0]

    assert item["action_type"] == "study_guide"
    assert item["topic"] is None


def test_an_attempt_carries_what_it_takes_to_reopen_it(upload_api) -> None:
    with upload_api.session_factory() as session:
        quiz = _quiz_with_questions(session, upload_api.course_id)
        question_ids = _question_ids(session, quiz.id)

    attempt = _submit(upload_api, upload_api.course_id, quiz.id, question_ids)

    item = _activity(upload_api.client, upload_api.authorization)[0]

    assert item["kind"] == "attempt"
    assert item["quiz_id"] == quiz.id
    assert item["attempt_id"] == attempt["attempt_id"]
    assert item["course_id"] == upload_api.course_id
    assert item["score"] == pytest.approx(1.0)
    assert item["output_id"] is None


def test_a_stored_generation_carries_its_output_id(upload_api) -> None:
    with upload_api.session_factory() as session:
        row = _output(
            session,
            upload_api.course_id,
            upload_api.user_id,
            "study_guide",
            created_at=BASE,
        )
        session.commit()
        output_id = row.id

    item = _activity(upload_api.client, upload_api.authorization)[0]

    assert item["output_id"] == output_id
    assert item["attempt_id"] is None
    assert item["score"] is None


def test_a_generated_quiz_is_one_event_not_two(
    upload_api, retrieval_env, monkeypatch
) -> None:
    with upload_api.session_factory() as session:
        seed_ready_material(
            session,
            upload_api.course_id,
            ["Activity feed quiz material"],
            file_hash="a1" + "1" * 62,
            retrieval_env=retrieval_env,
        )

    monkeypatch.setattr(
        quiz_route,
        "get_text_generation_provider",
        lambda **_: RecordingProvider(quiz_payload()),
    )

    response = upload_api.client.post(
        f"/api/courses/{upload_api.course_id}/quiz",
        json={
            "question_count": 2,
            "question_types": ["multiple_choice"],
            "difficulty": "medium",
            "topic_focus": "Graph Algorithms",
        },
        headers=upload_api.authorization,
    )
    assert response.status_code == 200, response.text
    generated_quiz_id = response.json()["data"]["quiz"]["quiz_id"]

    with upload_api.session_factory() as session:
        assert session.scalars(select(Quiz)).all()
        assert session.scalars(select(GeneratedOutput)).all()

    items = _activity(upload_api.client, upload_api.authorization)

    assert len(items) == 1
    assert items[0]["kind"] == "generation"
    assert items[0]["action_type"] == "quiz"
    assert items[0]["topic"] == "Graph Algorithms"
    assert items[0]["quiz_id"] == generated_quiz_id


def test_a_retake_is_its_own_event(upload_api) -> None:
    with upload_api.session_factory() as session:
        quiz = _quiz_with_questions(session, upload_api.course_id)
        question_ids = _question_ids(session, quiz.id)

    first = _submit(upload_api, upload_api.course_id, quiz.id, question_ids)
    second = _submit(upload_api, upload_api.course_id, quiz.id, question_ids)

    items = _activity(upload_api.client, upload_api.authorization)

    assert [item["attempt_id"] for item in items] == [
        second["attempt_id"],
        first["attempt_id"],
    ]


def test_activity_stops_at_the_requested_limit(upload_api) -> None:
    with upload_api.session_factory() as session:
        for day in range(5):
            _output(
                session,
                upload_api.course_id,
                upload_api.user_id,
                "study_guide",
                created_at=BASE + timedelta(days=day),
            )
        session.commit()

    items = _activity(upload_api.client, upload_api.authorization, limit=2)

    assert len(items) == 2
    assert datetime.fromisoformat(items[0]["occurred_at"]) == BASE + timedelta(days=4)
    assert datetime.fromisoformat(items[1]["occurred_at"]) == BASE + timedelta(days=3)


def test_the_limit_is_bounded(upload_api) -> None:
    assert (
        upload_api.client.get(
            "/api/activity", headers=upload_api.authorization, params={"limit": 0}
        ).status_code
        == 422
    )
    assert (
        upload_api.client.get(
            "/api/activity", headers=upload_api.authorization, params={"limit": 500}
        ).status_code
        == 422
    )


def test_a_deleted_course_reports_no_activity(upload_api) -> None:
    with upload_api.session_factory() as session:
        _output(
            session,
            upload_api.deleted_course_id,
            upload_api.user_id,
            "study_guide",
            created_at=BASE,
        )
        session.commit()

    assert _activity(upload_api.client, upload_api.authorization) == []


def test_a_legacy_generation_without_an_owner_is_not_reported(upload_api) -> None:
    with upload_api.session_factory() as session:
        _output(
            session,
            upload_api.course_id,
            None,
            "study_guide",
            created_at=BASE,
        )
        session.commit()

    assert _activity(upload_api.client, upload_api.authorization) == []


def test_activity_requires_authentication(upload_api) -> None:
    assert upload_api.client.get("/api/activity").status_code == 401


def test_another_owner_sees_none_of_this_work(authz_api) -> None:
    with authz_api.session_factory() as session:
        quiz = _quiz_with_questions(session, authz_api.a_course_id)
        question_ids = _question_ids(session, quiz.id)
        _output(
            session,
            authz_api.a_course_id,
            authz_api.user_a_id,
            "study_guide",
            created_at=BASE,
        )
        session.commit()

    response = authz_api.client.post(
        f"/api/courses/{authz_api.a_course_id}/quizzes/{quiz.id}/attempts",
        json={
            "answers": [
                {"question_id": question_id, "selected_option_index": 0}
                for question_id in question_ids
            ]
        },
        headers=authz_api.authorization_a,
    )
    assert response.status_code == 201, response.text

    owner = _activity(authz_api.client, authz_api.authorization_a)
    stranger = _activity(authz_api.client, authz_api.authorization_b)

    assert len(owner) == 2
    assert stranger == []


def test_an_administrator_sees_only_their_own_activity(authz_api) -> None:
    with authz_api.session_factory() as session:
        _output(
            session,
            authz_api.a_course_id,
            authz_api.user_a_id,
            "study_guide",
            created_at=BASE,
        )
        session.commit()

    assert _activity(authz_api.client, authz_api.authorization_admin) == []


def test_an_attempt_on_another_owners_quiz_is_not_this_users_activity(
    authz_api,
) -> None:
    with authz_api.session_factory() as session:
        quiz = _quiz_with_questions(session, authz_api.a_course_id)
        session.add(
            QuizAttempt(
                user_id=authz_api.user_b_id,
                quiz_id=quiz.id,
                score=1.0,
                created_at=BASE,
            )
        )
        session.commit()

    assert _activity(authz_api.client, authz_api.authorization_a) == []
