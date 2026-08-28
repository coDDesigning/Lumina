"""Timed sittings: the server owns the clock, and the deadline costs no answers.

A countdown a candidate's browser keeps is a number they can edit. These tests
hold the server to owning the deadline, to never losing work saved before it,
and to grading one sitting exactly once.
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from backend.app.models import (
    QUIZ_PURPOSE_EXAM_MOCK_EXAM,
    Progress,
    Role,
    Quiz,
    QuizAttempt,
    QuizQuestion,
    QuizSession,
    QuizSessionAnswer,
    User,
)

TIME_LIMIT_SECONDS = 3600


def create_timed_quiz(
    session, course_id: int, *, time_limit_seconds: int | None = TIME_LIMIT_SECONDS
) -> Quiz:
    quiz = Quiz(
        course_id=course_id,
        title="Mock exam",
        purpose=QUIZ_PURPOSE_EXAM_MOCK_EXAM if time_limit_seconds else None,
        time_limit_seconds=time_limit_seconds,
    )
    session.add(quiz)
    session.flush()

    for index, topic in enumerate(["Graph Traversal", "Dynamic Programming"]):
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


@pytest.fixture
def timed_quiz(upload_api):
    with upload_api.session_factory() as session:
        quiz = create_timed_quiz(session, upload_api.course_id)
        question_ids = [
            row.id
            for row in session.scalars(
                select(QuizQuestion)
                .where(QuizQuestion.quiz_id == quiz.id)
                .order_by(QuizQuestion.question_index)
            ).all()
        ]
        return {"quiz_id": quiz.id, "question_ids": question_ids}


def base_url(upload_api, quiz_id: int) -> str:
    return f"/api/courses/{upload_api.course_id}/quizzes/{quiz_id}"


def start(upload_api, quiz_id: int, *, headers=None):
    return upload_api.client.post(
        f"{base_url(upload_api, quiz_id)}/sessions",
        headers=headers or upload_api.authorization,
    )


def save_answer(upload_api, quiz_id, session_id, question_id, body, *, headers=None):
    return upload_api.client.put(
        f"{base_url(upload_api, quiz_id)}/sessions/{session_id}/answers/{question_id}",
        json=body,
        headers=headers or upload_api.authorization,
    )


def submit(upload_api, quiz_id, session_id, *, headers=None):
    return upload_api.client.post(
        f"{base_url(upload_api, quiz_id)}/sessions/{session_id}/submit",
        headers=headers or upload_api.authorization,
    )


def wind_clock_back(session_factory, session_id: int, seconds: int) -> None:
    """Move a sitting into the past, the way the rate-limit suite ages a bucket.

    Rewriting the row rather than sleeping keeps the test instant and keeps the
    comparison the service actually makes -- against ``expires_at`` -- honest.
    """
    with session_factory() as session:
        row = session.get(QuizSession, session_id)
        row.started_at = row.started_at - timedelta(seconds=seconds)
        row.expires_at = row.expires_at - timedelta(seconds=seconds)
        session.commit()


def row_count(session_factory, model, **filters) -> int:
    with session_factory() as session:
        statement = select(func.count()).select_from(model)
        for column, value in filters.items():
            statement = statement.where(getattr(model, column) == value)
        return session.scalar(statement)


# --------------------------------------------------------------- starting


def test_starting_a_sitting_records_the_server_s_own_deadline(
    upload_api, timed_quiz
) -> None:
    before = datetime.now(timezone.utc)

    response = start(upload_api, timed_quiz["quiz_id"])

    assert response.status_code == 201, response.text
    data = response.json()["data"]["session"]
    started = datetime.fromisoformat(data["started_at"])
    expires = datetime.fromisoformat(data["expires_at"])

    assert data["status"] == "active"
    assert data["time_limit_seconds"] == TIME_LIMIT_SECONDS
    assert (expires - started).total_seconds() == TIME_LIMIT_SECONDS
    assert started >= before - timedelta(seconds=5)
    assert data["seconds_remaining"] <= TIME_LIMIT_SECONDS


def test_the_paper_is_served_with_its_answers_hidden(upload_api, timed_quiz) -> None:
    response = start(upload_api, timed_quiz["quiz_id"])

    for question in response.json()["data"]["quiz"]["questions"]:
        assert question["correct_option_index"] is None
        assert question["correct_answer"] is None
        assert question["explanation"] == ""


def test_reloading_rejoins_the_same_sitting_rather_than_starting_a_second(
    upload_api, timed_quiz
) -> None:
    """Two clocks for one paper would split the drafts between them."""
    first = start(upload_api, timed_quiz["quiz_id"])
    second = start(upload_api, timed_quiz["quiz_id"])

    assert (
        first.json()["data"]["session"]["session_id"]
        == (second.json()["data"]["session"]["session_id"])
    )
    assert row_count(upload_api.session_factory, QuizSession) == 1


def test_an_untimed_quiz_has_no_sitting_to_start(upload_api) -> None:
    with upload_api.session_factory() as session:
        quiz = create_timed_quiz(session, upload_api.course_id, time_limit_seconds=None)
        quiz_id = quiz.id

    response = start(upload_api, quiz_id)

    assert response.status_code == 400
    assert row_count(upload_api.session_factory, QuizSession) == 0


def test_a_quiz_from_another_course_is_answered_as_a_missing_one(
    upload_api, timed_quiz
) -> None:
    response = upload_api.client.post(
        f"/api/courses/{upload_api.other_course_id}"
        f"/quizzes/{timed_quiz['quiz_id']}/sessions",
        headers=upload_api.authorization,
    )

    assert response.status_code == 404


# --------------------------------------------------------------- saving answers


def test_saving_an_answer_twice_replaces_it_rather_than_appending(
    upload_api, timed_quiz
) -> None:
    session_id = start(upload_api, timed_quiz["quiz_id"]).json()["data"]["session"][
        "session_id"
    ]
    question_id = timed_quiz["question_ids"][0]

    save_answer(
        upload_api,
        timed_quiz["quiz_id"],
        session_id,
        question_id,
        {"question_id": question_id, "selected_option_index": 1},
    )
    final = save_answer(
        upload_api,
        timed_quiz["quiz_id"],
        session_id,
        question_id,
        {"question_id": question_id, "selected_option_index": 0},
    )

    assert final.status_code == 200, final.text
    assert final.json()["data"]["answered_count"] == 1
    assert row_count(upload_api.session_factory, QuizSessionAnswer) == 1

    with upload_api.session_factory() as session:
        draft = session.scalars(select(QuizSessionAnswer)).one()
        assert draft.selected_option_index == 0


def test_an_answer_to_a_question_from_another_quiz_is_refused(
    upload_api, timed_quiz
) -> None:
    session_id = start(upload_api, timed_quiz["quiz_id"]).json()["data"]["session"][
        "session_id"
    ]

    response = save_answer(
        upload_api,
        timed_quiz["quiz_id"],
        session_id,
        999_999,
        {"question_id": 999_999, "selected_option_index": 0},
    )

    assert response.status_code == 400
    assert row_count(upload_api.session_factory, QuizSessionAnswer) == 0


def test_an_answer_in_the_wrong_form_is_refused_when_it_is_saved(
    upload_api, timed_quiz
) -> None:
    """Accepting a draft the attempt would later reject would cost a whole sitting."""
    session_id = start(upload_api, timed_quiz["quiz_id"]).json()["data"]["session"][
        "session_id"
    ]
    question_id = timed_quiz["question_ids"][0]

    response = save_answer(
        upload_api,
        timed_quiz["quiz_id"],
        session_id,
        question_id,
        {"question_id": question_id, "text_response": "written, for a choice question"},
    )

    assert response.status_code == 400
    assert row_count(upload_api.session_factory, QuizSessionAnswer) == 0


def test_an_unauthenticated_caller_can_neither_read_nor_change_a_sitting(
    upload_api, timed_quiz
) -> None:
    session_id = start(upload_api, timed_quiz["quiz_id"]).json()["data"]["session"][
        "session_id"
    ]
    question_id = timed_quiz["question_ids"][0]
    stranger = {"Authorization": "Bearer not-a-real-token"}

    read = upload_api.client.get(
        f"{base_url(upload_api, timed_quiz['quiz_id'])}/sessions/{session_id}",
        headers=stranger,
    )
    written = save_answer(
        upload_api,
        timed_quiz["quiz_id"],
        session_id,
        question_id,
        {"question_id": question_id, "selected_option_index": 0},
        headers=stranger,
    )

    assert read.status_code == 401
    assert written.status_code == 401
    assert row_count(upload_api.session_factory, QuizSessionAnswer) == 0


def test_someone_else_s_sitting_is_answered_as_a_missing_one(
    upload_api, timed_quiz
) -> None:
    """Scoped by user in the query, so a stranger's sitting is missing, not forbidden.

    Telling a caller that a session exists but is not theirs would confirm which
    identifiers are real.
    """
    with upload_api.session_factory() as session:
        other = User(
            name="Someone Else",
            email="someone-else@example.com",
            password_hash="x",
            role_id=session.scalars(select(Role).where(Role.name == "user")).one().id,
        )
        session.add(other)
        session.flush()
        foreign = QuizSession(
            quiz_id=timed_quiz["quiz_id"],
            user_id=other.id,
            status="active",
            time_limit_seconds=TIME_LIMIT_SECONDS,
            started_at=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc)
            + timedelta(seconds=TIME_LIMIT_SECONDS),
        )
        session.add(foreign)
        session.commit()
        foreign_id = foreign.id

    response = upload_api.client.get(
        f"{base_url(upload_api, timed_quiz['quiz_id'])}/sessions/{foreign_id}",
        headers=upload_api.authorization,
    )

    assert response.status_code == 404


# --------------------------------------------------------------- the deadline


def test_a_read_reports_a_sitting_as_over_without_writing_anything(
    upload_api, timed_quiz
) -> None:
    """Expiry is derived on read, so nothing has to sweep the table."""
    session_id = start(upload_api, timed_quiz["quiz_id"]).json()["data"]["session"][
        "session_id"
    ]
    wind_clock_back(upload_api.session_factory, session_id, TIME_LIMIT_SECONDS + 60)

    response = upload_api.client.get(
        f"{base_url(upload_api, timed_quiz['quiz_id'])}/sessions/{session_id}",
        headers=upload_api.authorization,
    )

    assert response.status_code == 200, response.text
    assert response.json()["data"]["status"] == "expired"
    assert response.json()["data"]["seconds_remaining"] == 0

    with upload_api.session_factory() as session:
        assert session.get(QuizSession, session_id).status == "active", (
            "a read must not write"
        )


def test_an_answer_cannot_be_changed_after_the_deadline(upload_api, timed_quiz) -> None:
    session_id = start(upload_api, timed_quiz["quiz_id"]).json()["data"]["session"][
        "session_id"
    ]
    question_id = timed_quiz["question_ids"][0]
    save_answer(
        upload_api,
        timed_quiz["quiz_id"],
        session_id,
        question_id,
        {"question_id": question_id, "selected_option_index": 1},
    )
    wind_clock_back(upload_api.session_factory, session_id, TIME_LIMIT_SECONDS + 60)

    response = save_answer(
        upload_api,
        timed_quiz["quiz_id"],
        session_id,
        question_id,
        {"question_id": question_id, "selected_option_index": 0},
    )

    assert response.status_code == 409
    assert response.headers["X-Error-Code"] == "timed_session_expired"

    with upload_api.session_factory() as session:
        draft = session.scalars(select(QuizSessionAnswer)).one()
        assert draft.selected_option_index == 1, "the saved answer is untouched"
        assert session.get(QuizSession, session_id).status == "expired"


def test_answers_saved_before_the_deadline_survive_it_and_are_still_graded(
    upload_api, timed_quiz
) -> None:
    """The whole point of drafts: a late final request costs no marks."""
    session_id = start(upload_api, timed_quiz["quiz_id"]).json()["data"]["session"][
        "session_id"
    ]
    for question_id, option in zip(timed_quiz["question_ids"], [0, 2]):
        save_answer(
            upload_api,
            timed_quiz["quiz_id"],
            session_id,
            question_id,
            {"question_id": question_id, "selected_option_index": option},
        )
    wind_clock_back(upload_api.session_factory, session_id, TIME_LIMIT_SECONDS + 120)

    response = submit(upload_api, timed_quiz["quiz_id"], session_id)

    assert response.status_code == 201, response.text
    payload = response.json()["data"]
    assert payload["total_questions"] == 2
    assert payload["correct_count"] == 1
    assert payload["score"] == pytest.approx(0.5)


def test_a_late_submission_reports_exactly_the_time_the_paper_allowed(
    upload_api, timed_quiz
) -> None:
    session_id = start(upload_api, timed_quiz["quiz_id"]).json()["data"]["session"][
        "session_id"
    ]
    question_id = timed_quiz["question_ids"][0]
    save_answer(
        upload_api,
        timed_quiz["quiz_id"],
        session_id,
        question_id,
        {"question_id": question_id, "selected_option_index": 0},
    )
    wind_clock_back(upload_api.session_factory, session_id, TIME_LIMIT_SECONDS * 3)

    response = submit(upload_api, timed_quiz["quiz_id"], session_id)

    assert response.json()["data"]["time_spent_seconds"] == TIME_LIMIT_SECONDS


def test_a_submission_within_the_time_reports_a_server_measured_elapsed(
    upload_api, timed_quiz
) -> None:
    """The client never says how long it took; it does not own the clock."""
    session_id = start(upload_api, timed_quiz["quiz_id"]).json()["data"]["session"][
        "session_id"
    ]
    question_id = timed_quiz["question_ids"][0]
    save_answer(
        upload_api,
        timed_quiz["quiz_id"],
        session_id,
        question_id,
        {"question_id": question_id, "selected_option_index": 0},
    )
    wind_clock_back(upload_api.session_factory, session_id, 300)

    response = submit(upload_api, timed_quiz["quiz_id"], session_id)

    elapsed = response.json()["data"]["time_spent_seconds"]
    assert 295 <= elapsed <= 360
    assert elapsed < TIME_LIMIT_SECONDS


def test_a_sitting_with_no_saved_answers_ends_truthfully_without_an_attempt(
    upload_api, timed_quiz
) -> None:
    session_id = start(upload_api, timed_quiz["quiz_id"]).json()["data"]["session"][
        "session_id"
    ]
    wind_clock_back(upload_api.session_factory, session_id, TIME_LIMIT_SECONDS + 60)

    response = submit(upload_api, timed_quiz["quiz_id"], session_id)

    assert response.status_code == 409
    assert response.headers["X-Error-Code"] == "timed_session_empty"
    assert row_count(upload_api.session_factory, QuizAttempt) == 0


# --------------------------------------------------------------- submitting once


def test_submitting_the_same_sitting_twice_returns_the_same_attempt(
    upload_api, timed_quiz
) -> None:
    """One sitting produces one attempt. A retry is not a second sitting."""
    session_id = start(upload_api, timed_quiz["quiz_id"]).json()["data"]["session"][
        "session_id"
    ]
    for question_id in timed_quiz["question_ids"]:
        save_answer(
            upload_api,
            timed_quiz["quiz_id"],
            session_id,
            question_id,
            {"question_id": question_id, "selected_option_index": 0},
        )

    first = submit(upload_api, timed_quiz["quiz_id"], session_id)
    second = submit(upload_api, timed_quiz["quiz_id"], session_id)

    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text
    assert first.json()["data"]["attempt_id"] == second.json()["data"]["attempt_id"]
    assert row_count(upload_api.session_factory, QuizAttempt) == 1


def test_progress_moves_once_however_many_times_the_submission_is_retried(
    upload_api, timed_quiz
) -> None:
    session_id = start(upload_api, timed_quiz["quiz_id"]).json()["data"]["session"][
        "session_id"
    ]
    for question_id in timed_quiz["question_ids"]:
        save_answer(
            upload_api,
            timed_quiz["quiz_id"],
            session_id,
            question_id,
            {"question_id": question_id, "selected_option_index": 0},
        )

    submit(upload_api, timed_quiz["quiz_id"], session_id)
    submit(upload_api, timed_quiz["quiz_id"], session_id)
    submit(upload_api, timed_quiz["quiz_id"], session_id)

    with upload_api.session_factory() as session:
        progress = session.scalars(
            select(Progress).where(Progress.course_id == upload_api.course_id)
        ).one()

    assert progress.quizzes_completed == 1
    assert row_count(upload_api.session_factory, QuizAttempt) == 1


def test_the_finished_sitting_names_the_attempt_it_produced(
    upload_api, timed_quiz
) -> None:
    session_id = start(upload_api, timed_quiz["quiz_id"]).json()["data"]["session"][
        "session_id"
    ]
    question_id = timed_quiz["question_ids"][0]
    save_answer(
        upload_api,
        timed_quiz["quiz_id"],
        session_id,
        question_id,
        {"question_id": question_id, "selected_option_index": 0},
    )

    attempt_id = submit(upload_api, timed_quiz["quiz_id"], session_id).json()["data"][
        "attempt_id"
    ]

    with upload_api.session_factory() as session:
        row = session.get(QuizSession, session_id)
        assert row.status == "submitted"
        assert row.attempt_id == attempt_id
        assert row.submitted_at is not None


def test_an_answer_cannot_be_saved_once_the_sitting_is_submitted(
    upload_api, timed_quiz
) -> None:
    session_id = start(upload_api, timed_quiz["quiz_id"]).json()["data"]["session"][
        "session_id"
    ]
    question_id = timed_quiz["question_ids"][0]
    save_answer(
        upload_api,
        timed_quiz["quiz_id"],
        session_id,
        question_id,
        {"question_id": question_id, "selected_option_index": 0},
    )
    submit(upload_api, timed_quiz["quiz_id"], session_id)

    response = save_answer(
        upload_api,
        timed_quiz["quiz_id"],
        session_id,
        question_id,
        {"question_id": question_id, "selected_option_index": 3},
    )

    assert response.status_code == 409
    assert response.headers["X-Error-Code"] == "timed_session_already_submitted"


def test_a_retake_after_submitting_starts_a_new_sitting(upload_api, timed_quiz) -> None:
    first_id = start(upload_api, timed_quiz["quiz_id"]).json()["data"]["session"][
        "session_id"
    ]
    question_id = timed_quiz["question_ids"][0]
    save_answer(
        upload_api,
        timed_quiz["quiz_id"],
        first_id,
        question_id,
        {"question_id": question_id, "selected_option_index": 0},
    )
    submit(upload_api, timed_quiz["quiz_id"], first_id)

    second_id = start(upload_api, timed_quiz["quiz_id"]).json()["data"]["session"][
        "session_id"
    ]

    assert second_id != first_id
    assert row_count(upload_api.session_factory, QuizSession) == 2


# --------------------------------------------------------------- the ordinary path


def test_a_timed_quiz_cannot_be_attempted_around_its_clock(
    upload_api, timed_quiz
) -> None:
    """Posting straight to the attempt endpoint would let the client time itself."""
    response = upload_api.client.post(
        f"{base_url(upload_api, timed_quiz['quiz_id'])}/attempts",
        json={
            "answers": [
                {
                    "question_id": timed_quiz["question_ids"][0],
                    "selected_option_index": 0,
                }
            ],
            "time_spent_seconds": 5,
        },
        headers=upload_api.authorization,
    )

    assert response.status_code == 409
    assert response.headers["X-Error-Code"] == "timed_session_required"
    assert row_count(upload_api.session_factory, QuizAttempt) == 0


def test_an_untimed_quiz_still_uses_the_ordinary_attempt_endpoint(upload_api) -> None:
    """The untimed path is untouched: no session, no new requirement."""
    with upload_api.session_factory() as session:
        quiz = create_timed_quiz(session, upload_api.course_id, time_limit_seconds=None)
        quiz_id = quiz.id
        question_id = (
            session.scalars(select(QuizQuestion).where(QuizQuestion.quiz_id == quiz_id))
            .first()
            .id
        )

    response = upload_api.client.post(
        f"{base_url(upload_api, quiz_id)}/attempts",
        json={
            "answers": [{"question_id": question_id, "selected_option_index": 0}],
            "time_spent_seconds": 42,
        },
        headers=upload_api.authorization,
    )

    assert response.status_code == 201, response.text
    assert response.json()["data"]["time_spent_seconds"] == 42


# --------------------------------------------------------------- telling modes apart


def test_the_attempt_says_which_mode_it_belongs_to_and_that_it_was_timed(
    upload_api, timed_quiz
) -> None:
    """A client must not have to parse generation settings to know what it read."""
    session_id = start(upload_api, timed_quiz["quiz_id"]).json()["data"]["session"][
        "session_id"
    ]
    question_id = timed_quiz["question_ids"][0]
    save_answer(
        upload_api,
        timed_quiz["quiz_id"],
        session_id,
        question_id,
        {"question_id": question_id, "selected_option_index": 0},
    )

    payload = submit(upload_api, timed_quiz["quiz_id"], session_id).json()["data"]

    assert payload["quiz_purpose"] == QUIZ_PURPOSE_EXAM_MOCK_EXAM
    assert payload["timed"] is True
    assert payload["expired"] is False


def test_an_attempt_finalised_after_the_deadline_says_so(
    upload_api, timed_quiz
) -> None:
    session_id = start(upload_api, timed_quiz["quiz_id"]).json()["data"]["session"][
        "session_id"
    ]
    question_id = timed_quiz["question_ids"][0]
    save_answer(
        upload_api,
        timed_quiz["quiz_id"],
        session_id,
        question_id,
        {"question_id": question_id, "selected_option_index": 0},
    )
    wind_clock_back(upload_api.session_factory, session_id, TIME_LIMIT_SECONDS + 60)

    payload = submit(upload_api, timed_quiz["quiz_id"], session_id).json()["data"]

    assert payload["expired"] is True
    assert payload["time_spent_seconds"] == TIME_LIMIT_SECONDS

    history = upload_api.client.get(
        f"{base_url(upload_api, timed_quiz['quiz_id'])}/attempts",
        headers=upload_api.authorization,
    )
    assert history.json()["data"][0]["expired"] is True
    assert history.json()["data"][0]["timed"] is True


def test_the_quiz_list_distinguishes_a_mock_exam_from_an_ordinary_quiz(
    upload_api, timed_quiz
) -> None:
    with upload_api.session_factory() as session:
        create_timed_quiz(session, upload_api.course_id, time_limit_seconds=None)

    response = upload_api.client.get(
        f"/api/courses/{upload_api.course_id}/quizzes",
        headers=upload_api.authorization,
    )

    assert response.status_code == 200, response.text
    modes = {
        row["quiz_id"]: (row["quiz_purpose"], row["timed"], row["time_limit_seconds"])
        for row in response.json()["data"]
    }
    assert modes[timed_quiz["quiz_id"]] == (
        QUIZ_PURPOSE_EXAM_MOCK_EXAM,
        True,
        TIME_LIMIT_SECONDS,
    )
    untimed = [key for key in modes if key != timed_quiz["quiz_id"]]
    assert modes[untimed[0]] == (None, False, None)


def test_reading_a_mock_exam_directly_does_not_reveal_its_answers(
    upload_api, timed_quiz
) -> None:
    """The generic quiz read is the same rows, so it needs the same rule.

    Without it a candidate could skip the sitting and read the answers off the
    quiz endpoint instead.
    """
    response = upload_api.client.get(
        base_url(upload_api, timed_quiz["quiz_id"]),
        headers=upload_api.authorization,
    )

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["answers_hidden"] is True
    for question in data["questions"]:
        assert question["correct_option_index"] is None
        assert question["correct_answer"] is None
        assert question["explanation"] == ""


def test_reading_an_ordinary_quiz_still_shows_its_answers(upload_api) -> None:
    """Practice keeps immediate feedback; only assessments withhold."""
    with upload_api.session_factory() as session:
        quiz = create_timed_quiz(session, upload_api.course_id, time_limit_seconds=None)
        quiz_id = quiz.id

    response = upload_api.client.get(
        base_url(upload_api, quiz_id), headers=upload_api.authorization
    )

    data = response.json()["data"]
    assert data["answers_hidden"] is False
    assert data["questions"][0]["correct_option_index"] == 0
    assert data["questions"][0]["explanation"] == "Option A is correct."
