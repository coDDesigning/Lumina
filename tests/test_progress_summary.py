import pytest
from sqlalchemy import select

from backend.app.models import Quiz, QuizQuestion


def _quiz_with_questions(session, course_id: int, count: int = 2) -> Quiz:
    quiz = Quiz(course_id=course_id, title="Summary Quiz")
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


def _submit(api, course_id: int, quiz_id: int, question_ids: list[int], correct: int):
    answers = [
        {
            "question_id": question_id,
            "selected_option_index": 0 if position < correct else 2,
        }
        for position, question_id in enumerate(question_ids)
    ]
    response = api.client.post(
        f"/api/courses/{course_id}/quizzes/{quiz_id}/attempts",
        json={"answers": answers},
        headers=api.authorization,
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]


def _summaries(client, headers) -> dict[int, dict]:
    response = client.get("/api/progress", headers=headers)
    assert response.status_code == 200, response.text
    return {row["course_id"]: row for row in response.json()["data"]}


def test_one_request_summarizes_every_owned_course(upload_api) -> None:
    with upload_api.session_factory() as session:
        quiz = _quiz_with_questions(session, upload_api.course_id)
        question_ids = _question_ids(session, quiz.id)

    _submit(upload_api, upload_api.course_id, quiz.id, question_ids, correct=1)
    _submit(upload_api, upload_api.course_id, quiz.id, question_ids, correct=2)

    summaries = _summaries(upload_api.client, upload_api.authorization)

    assert set(summaries) == {upload_api.course_id, upload_api.other_course_id}

    active = summaries[upload_api.course_id]
    assert active["attempts_count"] == 2
    assert active["average_score"] == pytest.approx(0.75)
    assert active["completion"] == pytest.approx(0.75)


def test_course_without_activity_reports_nulls_not_zeros(upload_api) -> None:
    summaries = _summaries(upload_api.client, upload_api.authorization)
    idle = summaries[upload_api.other_course_id]

    assert idle["attempts_count"] == 0
    assert idle["average_score"] is None
    assert idle["completion"] is None
    assert idle["last_activity"] is None


def test_deleted_course_is_not_summarized(upload_api) -> None:
    summaries = _summaries(upload_api.client, upload_api.authorization)
    assert upload_api.deleted_course_id not in summaries


def test_summary_matches_the_single_course_endpoint(upload_api) -> None:
    with upload_api.session_factory() as session:
        quiz = _quiz_with_questions(session, upload_api.course_id)
        question_ids = _question_ids(session, quiz.id)

    _submit(upload_api, upload_api.course_id, quiz.id, question_ids, correct=1)

    summary = _summaries(upload_api.client, upload_api.authorization)[
        upload_api.course_id
    ]
    single = upload_api.client.get(
        f"/api/courses/{upload_api.course_id}/progress",
        headers=upload_api.authorization,
    ).json()["data"]

    assert summary["attempts_count"] == single["attempts_count"]
    assert summary["average_score"] == pytest.approx(single["average_score"])
    assert summary["completion"] == pytest.approx(single["completion"])


def test_another_owner_sees_neither_the_course_nor_its_progress(authz_api) -> None:
    with authz_api.session_factory() as session:
        quiz = _quiz_with_questions(session, authz_api.a_course_id)
        question_ids = _question_ids(session, quiz.id)

    response = authz_api.client.post(
        f"/api/courses/{authz_api.a_course_id}/quizzes/{quiz.id}/attempts",
        json={
            "answers": [
                {"question_id": question_ids[0], "selected_option_index": 0},
                {"question_id": question_ids[1], "selected_option_index": 0},
            ]
        },
        headers=authz_api.authorization_a,
    )
    assert response.status_code == 201, response.text

    owner = _summaries(authz_api.client, authz_api.authorization_a)
    stranger = _summaries(authz_api.client, authz_api.authorization_b)

    assert owner[authz_api.a_course_id]["attempts_count"] == 1
    assert authz_api.a_course_id not in stranger
    assert set(stranger) == {authz_api.b_course_id}


def test_administrator_sees_only_their_own_courses(authz_api) -> None:
    readable = authz_api.client.get(
        "/api/courses/", headers=authz_api.authorization_admin
    )
    assert readable.status_code == 200, readable.text
    assert {course["id"] for course in readable.json()["data"]} >= {
        authz_api.a_course_id,
        authz_api.b_course_id,
    }

    assert _summaries(authz_api.client, authz_api.authorization_admin) == {}


def test_progress_requires_authentication(upload_api) -> None:
    assert upload_api.client.get("/api/progress").status_code == 401
