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


def _create_custom_quiz(session, course_id: int, specs: list[dict]) -> Quiz:
    quiz = Quiz(course_id=course_id, title="Custom Spec Quiz")
    session.add(quiz)
    session.flush()

    for index, spec in enumerate(specs):
        session.add(
            QuizQuestion(
                quiz_id=quiz.id,
                question_index=index,
                question_type=spec.get("question_type", "multiple_choice"),
                question_text=spec.get("question_text", f"Question {index + 1}?"),
                options=spec.get(
                    "options", ["Option A", "Option B", "Option C", "Option D"]
                ),
                correct_option_index=spec.get("correct_option_index", 0),
                topic=spec.get("topic", "General"),
                explanation=spec.get("explanation", "Explanation text."),
            )
        )

    session.commit()
    session.refresh(quiz)
    return quiz


def test_submit_attempt_with_per_question_time_and_topic(upload_api) -> None:
    quiz_id, question_ids = _quiz_with_question_ids(
        upload_api, ["Calculus", "Linear Algebra"]
    )

    response = upload_api.client.post(
        f"/api/courses/{upload_api.course_id}/quizzes/{quiz_id}/attempts",
        json={
            "answers": [
                {
                    "question_id": question_ids[0],
                    "selected_option_index": 0,
                    "time_spent_seconds": 25,
                },
                {
                    "question_id": question_ids[1],
                    "selected_option_index": 1,
                    "time_spent_seconds": 40,
                },
            ],
            "time_spent_seconds": 65,
        },
        headers=upload_api.authorization,
    )

    assert response.status_code == 201, response.text
    payload = response.json()["data"]

    assert payload["answers"][0]["time_spent_seconds"] == 25
    assert payload["answers"][0]["topic"] == "Calculus"
    assert payload["answers"][1]["time_spent_seconds"] == 40
    assert payload["answers"][1]["topic"] == "Linear Algebra"

    with upload_api.session_factory() as session:
        stored = session.scalars(
            select(QuizAttemptAnswer)
            .where(QuizAttemptAnswer.attempt_id == payload["attempt_id"])
            .order_by(QuizAttemptAnswer.id)
        ).all()
        assert len(stored) == 2
        assert stored[0].time_spent_seconds == 25
        assert stored[0].topic == "Calculus"
        assert stored[1].time_spent_seconds == 40
        assert stored[1].topic == "Linear Algebra"


def test_submit_attempt_true_false_grading(upload_api) -> None:
    with upload_api.session_factory() as session:
        quiz = _create_custom_quiz(
            session,
            upload_api.course_id,
            [
                {
                    "question_type": "true_false",
                    "options": ["True", "False"],
                    "correct_option_index": 0,  # True is correct
                    "topic": "Logic",
                },
                {
                    "question_type": "true_false",
                    "options": ["True", "False"],
                    "correct_option_index": 1,  # False is correct
                    "topic": "Logic",
                },
            ],
        )
        quiz_id = quiz.id
        q_ids = [q.id for q in quiz.questions]

    response = upload_api.client.post(
        f"/api/courses/{upload_api.course_id}/quizzes/{quiz_id}/attempts",
        json={
            "answers": [
                {"question_id": q_ids[0], "selected_option_index": 0},  # Correct (True)
                {
                    "question_id": q_ids[1],
                    "selected_option_index": 0,
                },  # Incorrect (True instead of False)
            ],
        },
        headers=upload_api.authorization,
    )

    assert response.status_code == 201, response.text
    payload = response.json()["data"]
    assert payload["score"] == pytest.approx(0.5)
    assert payload["correct_count"] == 1
    assert payload["total_questions"] == 2
    assert payload["answers"][0]["is_correct"] is True
    assert payload["answers"][1]["is_correct"] is False


def test_submit_attempt_written_questions_are_stored_ungraded(upload_api) -> None:
    with upload_api.session_factory() as session:
        quiz = _create_custom_quiz(
            session,
            upload_api.course_id,
            [
                {
                    "question_type": "short_answer",
                    "options": None,
                    "correct_option_index": None,
                    "topic": "Operating Systems",
                    "question_text": "Explain virtual memory.",
                },
                {
                    "question_type": "open_ended",
                    "options": None,
                    "correct_option_index": None,
                    "topic": "Operating Systems",
                    "question_text": "Compare paging vs segmentation.",
                },
            ],
        )
        quiz_id = quiz.id
        q_ids = [q.id for q in quiz.questions]

    response = upload_api.client.post(
        f"/api/courses/{upload_api.course_id}/quizzes/{quiz_id}/attempts",
        json={
            "answers": [
                {
                    "question_id": q_ids[0],
                    "text_response": "Virtual memory maps virtual addresses to physical pages.",
                    "time_spent_seconds": 45,
                },
                {
                    "question_id": q_ids[1],
                    "text_response": "Paging uses fixed-size blocks while segmentation uses variable sizes.",
                    "time_spent_seconds": 60,
                },
            ],
        },
        headers=upload_api.authorization,
    )

    assert response.status_code == 201, response.text
    payload = response.json()["data"]
    assert payload["score"] == 0.0  # Documented rule: 0.0 when 0 gradable questions
    assert payload["correct_count"] == 0
    assert payload["total_questions"] == 2
    assert payload["answers"][0]["is_correct"] is None  # Explicit ungraded state
    assert (
        payload["answers"][0]["text_response"]
        == "Virtual memory maps virtual addresses to physical pages."
    )
    assert payload["answers"][1]["is_correct"] is None
    assert (
        payload["answers"][1]["text_response"]
        == "Paging uses fixed-size blocks while segmentation uses variable sizes."
    )

    with upload_api.session_factory() as session:
        stored = session.scalars(
            select(QuizAttemptAnswer)
            .where(QuizAttemptAnswer.attempt_id == payload["attempt_id"])
            .order_by(QuizAttemptAnswer.id)
        ).all()
        assert len(stored) == 2
        assert stored[0].is_correct is None
        assert (
            stored[0].text_response
            == "Virtual memory maps virtual addresses to physical pages."
        )
        assert stored[1].is_correct is None


def test_score_calculation_only_counts_gradable_questions(upload_api) -> None:
    """A quiz with mixed objective and written questions computes score only from gradable ones."""
    with upload_api.session_factory() as session:
        quiz = _create_custom_quiz(
            session,
            upload_api.course_id,
            [
                {
                    "question_type": "multiple_choice",
                    "options": ["A", "B", "C", "D"],
                    "correct_option_index": 0,
                    "topic": "Algorithms",
                },
                {
                    "question_type": "multiple_choice",
                    "options": ["A", "B", "C", "D"],
                    "correct_option_index": 1,
                    "topic": "Algorithms",
                },
                {
                    "question_type": "short_answer",
                    "options": None,
                    "correct_option_index": None,
                    "topic": "Algorithms",
                },
                {
                    "question_type": "open_ended",
                    "options": None,
                    "correct_option_index": None,
                    "topic": "Algorithms",
                },
            ],
        )
        quiz_id = quiz.id
        q_ids = [q.id for q in quiz.questions]

    response = upload_api.client.post(
        f"/api/courses/{upload_api.course_id}/quizzes/{quiz_id}/attempts",
        json={
            "answers": [
                {"question_id": q_ids[0], "selected_option_index": 0},  # Correct
                {
                    "question_id": q_ids[1],
                    "selected_option_index": 0,
                },  # Incorrect (selected 0, correct 1)
                {"question_id": q_ids[2], "text_response": "QuickSort is O(N log N)"},
                {
                    "question_id": q_ids[3],
                    "text_response": "Dynamic programming caches subproblems",
                },
            ],
        },
        headers=upload_api.authorization,
    )

    assert response.status_code == 201, response.text
    payload = response.json()["data"]

    # 1 correct out of 2 gradable questions = 0.5 (NOT 1/4 = 0.25)
    assert payload["score"] == pytest.approx(0.5)
    assert payload["correct_count"] == 1
    assert payload["total_questions"] == 4
    assert payload["answers"][0]["is_correct"] is True
    assert payload["answers"][1]["is_correct"] is False
    assert payload["answers"][2]["is_correct"] is None
    assert payload["answers"][3]["is_correct"] is None


def test_repeat_submissions_update_existing_progress_record_without_duplicates(
    upload_api,
) -> None:
    quiz_id, question_ids = _quiz_with_question_ids(upload_api, ["Algebra", "Calculus"])

    # Attempt 1: 2/2 correct (100%)
    res1 = upload_api.client.post(
        f"/api/courses/{upload_api.course_id}/quizzes/{quiz_id}/attempts",
        json={
            "answers": [
                {"question_id": question_ids[0], "selected_option_index": 0},
                {"question_id": question_ids[1], "selected_option_index": 0},
            ]
        },
        headers=upload_api.authorization,
    )
    assert res1.status_code == 201
    assert res1.json()["data"]["score"] == 1.0

    # Attempt 2: 0/2 correct (0%)
    res2 = upload_api.client.post(
        f"/api/courses/{upload_api.course_id}/quizzes/{quiz_id}/attempts",
        json={
            "answers": [
                {"question_id": question_ids[0], "selected_option_index": 1},
                {"question_id": question_ids[1], "selected_option_index": 1},
            ]
        },
        headers=upload_api.authorization,
    )
    assert res2.status_code == 201
    assert res2.json()["data"]["score"] == 0.0

    # Verify exactly ONE Progress row exists in DB
    with upload_api.session_factory() as session:
        from backend.app.models import Progress

        progress_rows = session.scalars(
            select(Progress).where(
                Progress.user_id == upload_api.user_id,
                Progress.course_id == upload_api.course_id,
            )
        ).all()
        assert len(progress_rows) == 1
        prog = progress_rows[0]
        assert prog.quizzes_completed == 2
        assert prog.correct_answers_count == 2
        assert prog.incorrect_answers_count == 2
        assert prog.total_questions_answered == 4
        assert prog.completion == pytest.approx(0.5)
        assert len(prog.quiz_history) == 2

    # Verify Progress endpoint returns updated aggregated values
    prog_res = upload_api.client.get(
        f"/api/courses/{upload_api.course_id}/progress",
        headers=upload_api.authorization,
    )
    assert prog_res.status_code == 200
    prog_data = prog_res.json()["data"]
    assert prog_data["quizzes_completed"] == 2
    assert prog_data["attempts_count"] == 2
    assert prog_data["average_score"] == pytest.approx(0.5)
    assert prog_data["correct_count"] == 2
    assert prog_data["incorrect_count"] == 2
    assert prog_data["total_questions_answered"] == 4
    assert len(prog_data["quiz_history"]) == 2


def test_progress_aggregates_multiple_quizzes_and_identifies_weak_topics(
    upload_api,
) -> None:
    with upload_api.session_factory() as session:
        quiz1 = _create_custom_quiz(
            session,
            upload_api.course_id,
            [
                {"topic": "Calculus", "correct_option_index": 0},
                {"topic": "Calculus", "correct_option_index": 0},
            ],
        )
        quiz2 = _create_custom_quiz(
            session,
            upload_api.course_id,
            [
                {"topic": "Calculus", "correct_option_index": 0},
                {"topic": "Algebra", "correct_option_index": 0},
                {"topic": "Algebra", "correct_option_index": 0},
            ],
        )
        q1_ids = [q.id for q in quiz1.questions]
        q2_ids = [q.id for q in quiz2.questions]
        quiz1_id = quiz1.id
        quiz2_id = quiz2.id

    # Quiz 1 attempt: Calculus 1/2 correct (50%)
    upload_api.client.post(
        f"/api/courses/{upload_api.course_id}/quizzes/{quiz1_id}/attempts",
        json={
            "answers": [
                {"question_id": q1_ids[0], "selected_option_index": 0},
                {"question_id": q1_ids[1], "selected_option_index": 1},
            ]
        },
        headers=upload_api.authorization,
    )

    # Quiz 2 attempt: Calculus 0/1 correct, Algebra 2/2 correct
    upload_api.client.post(
        f"/api/courses/{upload_api.course_id}/quizzes/{quiz2_id}/attempts",
        json={
            "answers": [
                {
                    "question_id": q2_ids[0],
                    "selected_option_index": 1,
                },  # Calculus incorrect
                {
                    "question_id": q2_ids[1],
                    "selected_option_index": 0,
                },  # Algebra correct
                {
                    "question_id": q2_ids[2],
                    "selected_option_index": 0,
                },  # Algebra correct
            ]
        },
        headers=upload_api.authorization,
    )

    res = upload_api.client.get(
        f"/api/courses/{upload_api.course_id}/progress",
        headers=upload_api.authorization,
    )
    assert res.status_code == 200
    data = res.json()["data"]

    assert data["quizzes_completed"] == 2
    assert data["total_questions_answered"] == 5
    assert data["correct_count"] == 3
    assert data["incorrect_count"] == 2

    # Calculus: 1 correct / 3 answered = 33% (< 60%) -> Needs Review
    # Algebra: 2 correct / 2 answered = 100% (>= 80%) -> Mastered
    mastery_map = {m["topic"]: m for m in data["topic_mastery"]}
    assert mastery_map["Calculus"]["mastery_percentage"] == 33
    assert mastery_map["Calculus"]["status"] == "Needs Review"
    assert mastery_map["Algebra"]["mastery_percentage"] == 100
    assert mastery_map["Algebra"]["status"] == "Mastered"

    assert data["weak_topics"] == ["Calculus"]
    assert len(data["quiz_history"]) == 2


def test_transaction_rollback_on_failure_leaves_no_orphaned_state(
    upload_api, monkeypatch
) -> None:
    quiz_id, question_ids = _quiz_with_question_ids(upload_api, ["Algebra"])

    from services.quiz_attempt import QuizAttemptService

    def broken_progress_update(*args, **kwargs):
        raise RuntimeError("Simulated transaction failure during progress update")

    monkeypatch.setattr(
        QuizAttemptService,
        "_update_course_progress_transactional",
        broken_progress_update,
    )

    with pytest.raises(RuntimeError, match="Simulated transaction failure"):
        upload_api.client.post(
            f"/api/courses/{upload_api.course_id}/quizzes/{quiz_id}/attempts",
            json={
                "answers": [
                    {"question_id": question_ids[0], "selected_option_index": 0}
                ]
            },
            headers=upload_api.authorization,
        )

    # Verify that the attempt was rolled back and NOT saved
    with upload_api.session_factory() as session:
        from backend.app.models import Progress, QuizAttempt, QuizAttemptAnswer

        assert session.scalars(select(QuizAttempt)).all() == []
        assert session.scalars(select(QuizAttemptAnswer)).all() == []
        assert session.scalars(select(Progress)).all() == []
