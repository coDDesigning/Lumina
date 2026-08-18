import pytest
from sqlalchemy import select

from backend.app.models import (
    Course,
    Quiz,
    QuizAttempt,
    QuizAttemptAnswer,
    QuizQuestion,
)


def _create_quiz(session, course_id: int, topics: list[str]) -> Quiz:
    quiz = Quiz(course_id=course_id, title="Persisted Quiz")
    session.add(quiz)
    session.flush()

    for index, topic in enumerate(topics):
        session.add(
            QuizQuestion(
                quiz_id=quiz.id,
                question_index=index,
                question_text=f"Question {index + 1}?",
                options=["Option A", "Option B", "Option C", "Option D"],
                correct_option_index=0,
                topic=topic,
                explanation="Option A is correct.",
            )
        )

    session.commit()
    session.refresh(quiz)
    return quiz


def _quiz_with_question_ids(upload_api, topics: list[str]) -> tuple[int, list[int]]:
    with upload_api.session_factory() as session:
        quiz = _create_quiz(session, upload_api.course_id, topics)
        question_ids = [
            row.id
            for row in session.scalars(
                select(QuizQuestion)
                .where(QuizQuestion.quiz_id == quiz.id)
                .order_by(QuizQuestion.question_index)
            ).all()
        ]
        return quiz.id, question_ids


def test_submit_attempt_scores_answers_server_side(upload_api) -> None:
    quiz_id, question_ids = _quiz_with_question_ids(upload_api, ["Algebra", "Calculus"])

    response = upload_api.client.post(
        f"/api/courses/{upload_api.course_id}/quizzes/{quiz_id}/attempts",
        json={
            "answers": [
                {"question_id": question_ids[0], "selected_option_index": 0},
                {"question_id": question_ids[1], "selected_option_index": 2},
            ],
            "time_spent_seconds": 91,
        },
        headers=upload_api.authorization,
    )

    assert response.status_code == 201, response.text
    payload = response.json()["data"]

    assert payload["quiz_id"] == quiz_id
    assert payload["correct_count"] == 1
    assert payload["total_questions"] == 2
    assert payload["score"] == pytest.approx(0.5)
    assert payload["time_spent_seconds"] == 91
    assert [answer["is_correct"] for answer in payload["answers"]] == [True, False]
    assert payload["answers"][1]["correct_option_index"] == 0

    with upload_api.session_factory() as session:
        stored = session.scalars(
            select(QuizAttemptAnswer).where(
                QuizAttemptAnswer.attempt_id == payload["attempt_id"]
            )
        ).all()
        attempt = session.get(QuizAttempt, payload["attempt_id"])

    assert len(stored) == 2
    assert attempt is not None
    assert attempt.user_id == upload_api.user_id
    assert attempt.score == pytest.approx(0.5)


def test_submit_attempt_counts_unanswered_questions_as_incorrect(upload_api) -> None:
    quiz_id, question_ids = _quiz_with_question_ids(upload_api, ["Algebra", "Calculus"])

    response = upload_api.client.post(
        f"/api/courses/{upload_api.course_id}/quizzes/{quiz_id}/attempts",
        json={
            "answers": [{"question_id": question_ids[0], "selected_option_index": 0}]
        },
        headers=upload_api.authorization,
    )

    assert response.status_code == 201, response.text
    payload = response.json()["data"]

    assert payload["total_questions"] == 2
    assert payload["correct_count"] == 1
    assert payload["answers"][1]["selected_option_index"] is None
    assert payload["answers"][1]["is_correct"] is False
    assert payload["time_spent_seconds"] is None


@pytest.mark.parametrize(
    "build_body,expected_status",
    [
        pytest.param(
            lambda ids: {"answers": [{"question_id": 999999}]},
            400,
            id="question_from_another_quiz",
        ),
        pytest.param(
            lambda ids: {
                "answers": [{"question_id": ids[0], "selected_option_index": 9}]
            },
            400,
            id="option_out_of_range",
        ),
        pytest.param(
            lambda ids: {
                "answers": [
                    {"question_id": ids[0], "selected_option_index": 0},
                    {"question_id": ids[0], "selected_option_index": 1},
                ]
            },
            400,
            id="duplicate_question",
        ),
        pytest.param(lambda ids: {"answers": []}, 422, id="empty_answers"),
        pytest.param(lambda ids: None, 422, id="missing_body"),
        pytest.param(
            lambda ids: {
                "answers": [{"question_id": ids[0], "selected_option_index": -1}]
            },
            422,
            id="negative_option",
        ),
    ],
)
def test_submit_attempt_rejects_invalid_submissions(
    upload_api,
    build_body,
    expected_status,
) -> None:
    quiz_id, question_ids = _quiz_with_question_ids(upload_api, ["Algebra"])

    response = upload_api.client.post(
        f"/api/courses/{upload_api.course_id}/quizzes/{quiz_id}/attempts",
        json=build_body(question_ids),
        headers=upload_api.authorization,
    )

    assert response.status_code == expected_status, response.text

    with upload_api.session_factory() as session:
        assert session.scalars(select(QuizAttempt)).all() == []


def test_submit_attempt_hides_a_quiz_from_another_course(upload_api) -> None:
    with upload_api.session_factory() as session:
        quiz = _create_quiz(session, upload_api.other_course_id, ["Algebra"])
        foreign_quiz_id = quiz.id

    response = upload_api.client.post(
        f"/api/courses/{upload_api.course_id}/quizzes/{foreign_quiz_id}/attempts",
        json={"answers": [{"question_id": 1, "selected_option_index": 0}]},
        headers=upload_api.authorization,
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Quiz not found"


def test_submit_attempt_requires_authentication(upload_api) -> None:
    quiz_id, question_ids = _quiz_with_question_ids(upload_api, ["Algebra"])

    response = upload_api.client.post(
        f"/api/courses/{upload_api.course_id}/quizzes/{quiz_id}/attempts",
        json={
            "answers": [{"question_id": question_ids[0], "selected_option_index": 0}]
        },
    )

    assert response.status_code == 401


def test_progress_is_empty_without_attempts(upload_api) -> None:
    response = upload_api.client.get(
        f"/api/courses/{upload_api.course_id}/progress",
        headers=upload_api.authorization,
    )

    assert response.status_code == 200, response.text
    payload = response.json()["data"]

    assert payload["attempts_count"] == 0
    assert payload["average_score"] is None
    assert payload["topic_mastery"] == []


def test_progress_aggregates_real_topic_mastery(upload_api) -> None:
    quiz_id, question_ids = _quiz_with_question_ids(
        upload_api,
        ["Algebra", "Algebra", "Calculus"],
    )

    upload_api.client.post(
        f"/api/courses/{upload_api.course_id}/quizzes/{quiz_id}/attempts",
        json={
            "answers": [
                {"question_id": question_ids[0], "selected_option_index": 0},
                {"question_id": question_ids[1], "selected_option_index": 0},
                {"question_id": question_ids[2], "selected_option_index": 3},
            ]
        },
        headers=upload_api.authorization,
    )

    response = upload_api.client.get(
        f"/api/courses/{upload_api.course_id}/progress",
        headers=upload_api.authorization,
    )

    assert response.status_code == 200, response.text
    payload = response.json()["data"]

    assert payload["attempts_count"] == 1
    assert payload["average_score"] == pytest.approx(2 / 3)

    mastery = {entry["topic"]: entry for entry in payload["topic_mastery"]}
    assert set(mastery) == {"Algebra", "Calculus"}
    assert mastery["Algebra"]["questions_answered"] == 2
    assert mastery["Algebra"]["questions_correct"] == 2
    assert mastery["Algebra"]["mastery_percentage"] == 100
    assert mastery["Algebra"]["status"] == "Mastered"
    assert mastery["Calculus"]["mastery_percentage"] == 0
    assert mastery["Calculus"]["status"] == "Needs Review"


def test_progress_labels_questions_without_a_topic(upload_api) -> None:
    quiz_id, question_ids = _quiz_with_question_ids(upload_api, [None])

    upload_api.client.post(
        f"/api/courses/{upload_api.course_id}/quizzes/{quiz_id}/attempts",
        json={
            "answers": [{"question_id": question_ids[0], "selected_option_index": 0}]
        },
        headers=upload_api.authorization,
    )

    response = upload_api.client.get(
        f"/api/courses/{upload_api.course_id}/progress",
        headers=upload_api.authorization,
    )

    payload = response.json()["data"]
    assert payload["topic_mastery"][0]["topic"] == "Untagged"


def test_progress_is_scoped_to_the_requesting_user(authz_api) -> None:
    with authz_api.session_factory() as session:
        course = session.get(Course, authz_api.a_course_id)
        assert course is not None
        quiz = _create_quiz(session, course.id, ["Algebra"])
        quiz_id = quiz.id
        question_id = (
            session.scalars(select(QuizQuestion).where(QuizQuestion.quiz_id == quiz_id))
            .one()
            .id
        )

    recorded = authz_api.client.post(
        f"/api/courses/{authz_api.a_course_id}/quizzes/{quiz_id}/attempts",
        json={"answers": [{"question_id": question_id, "selected_option_index": 0}]},
        headers=authz_api.authorization_a,
    )
    assert recorded.status_code == 201, recorded.text

    owner_progress = authz_api.client.get(
        f"/api/courses/{authz_api.a_course_id}/progress",
        headers=authz_api.authorization_a,
    )
    assert owner_progress.json()["data"]["attempts_count"] == 1

    admin_progress = authz_api.client.get(
        f"/api/courses/{authz_api.a_course_id}/progress",
        headers=authz_api.authorization_admin,
    )
    assert admin_progress.status_code == 200, admin_progress.text
    assert admin_progress.json()["data"]["attempts_count"] == 0
    assert admin_progress.json()["data"]["topic_mastery"] == []

    stranger_progress = authz_api.client.get(
        f"/api/courses/{authz_api.a_course_id}/progress",
        headers=authz_api.authorization_b,
    )
    assert stranger_progress.status_code == 404
