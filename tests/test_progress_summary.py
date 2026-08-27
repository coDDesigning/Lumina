from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from backend.app.models import (
    Conversation,
    Course,
    GeneratedOutput,
    Quiz,
    QuizAttempt,
    QuizQuestion,
    UploadedDocument,
)
from schemas.conversation import ConversationType
from services.conversation import ConversationService


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


def _submit(
    api,
    course_id: int,
    quiz_id: int,
    question_ids: list[int],
    correct: int,
    time_spent_seconds: int | None = None,
):
    answers = [
        {
            "question_id": question_id,
            "selected_option_index": 0 if position < correct else 2,
        }
        for position, question_id in enumerate(question_ids)
    ]
    payload: dict = {"answers": answers}
    if time_spent_seconds is not None:
        payload["time_spent_seconds"] = time_spent_seconds
    response = api.client.post(
        f"/api/courses/{course_id}/quizzes/{quiz_id}/attempts",
        json=payload,
        headers=api.authorization,
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]


def _add_document(session, course, *, status: str, marker: str) -> None:
    session.add(
        UploadedDocument(
            original_file_name=f"{marker}.txt",
            file_type="txt",
            mime_type="text/plain",
            file_size=10,
            file_hash=marker * 64,
            user_id=course.owner_id,
            course=course,
            storage_provider="local:test",
            storage_key=f"{marker}.txt",
            status=status,
        )
    )


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
    assert summary["status"] == single["status"]
    assert summary["total_time_spent_seconds"] == single["total_time_spent_seconds"]


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
    assert {course["id"] for course in readable.json()["data"]} == set()

    assert _summaries(authz_api.client, authz_api.authorization_admin) == {}


def test_progress_requires_authentication(upload_api) -> None:
    assert upload_api.client.get("/api/progress").status_code == 401


def test_last_activity_is_the_latest_of_all_three_sources(upload_api) -> None:
    base = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)

    with upload_api.session_factory() as session:
        quiz = _quiz_with_questions(session, upload_api.course_id)
        question_ids = _question_ids(session, quiz.id)
        session.add(
            GeneratedOutput(
                course_id=upload_api.course_id,
                user_id=upload_api.user_id,
                output_type="summary",
                content="Study guide body",
                created_at=base + timedelta(days=2),
            )
        )
        session.add(
            Conversation(
                user_id=upload_api.user_id,
                course_id=upload_api.course_id,
                conversation_type=ConversationType.COURSE_QA.value,
                created_at=base,
                updated_at=base + timedelta(days=5),
            )
        )
        session.commit()

    _submit(upload_api, upload_api.course_id, quiz.id, question_ids, correct=1)

    with upload_api.session_factory() as session:
        attempt = session.scalars(select(QuizAttempt)).one()
        attempt.created_at = base + timedelta(days=1)
        session.commit()

    summary = _summaries(upload_api.client, upload_api.authorization)[
        upload_api.course_id
    ]

    assert summary["last_activity"] is not None
    assert datetime.fromisoformat(summary["last_activity"]) == base + timedelta(days=5)


def test_studied_but_never_quizzed_reports_activity_without_a_score(
    upload_api,
) -> None:
    stamp = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)

    with upload_api.session_factory() as session:
        session.add(
            GeneratedOutput(
                course_id=upload_api.other_course_id,
                user_id=upload_api.user_id,
                output_type="summary",
                content="Study guide body",
                created_at=stamp,
            )
        )
        session.commit()

    summary = _summaries(upload_api.client, upload_api.authorization)[
        upload_api.other_course_id
    ]

    assert summary["attempts_count"] == 0
    assert summary["average_score"] is None
    assert summary["completion"] is None
    assert datetime.fromisoformat(summary["last_activity"]) == stamp


def test_generated_output_without_an_owner_is_not_counted_as_activity(
    upload_api,
) -> None:
    with upload_api.session_factory() as session:
        session.add(
            GeneratedOutput(
                course_id=upload_api.other_course_id,
                user_id=None,
                output_type="summary",
                content="Legacy row",
                created_at=datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc),
            )
        )
        session.commit()

    summary = _summaries(upload_api.client, upload_api.authorization)[
        upload_api.other_course_id
    ]

    assert summary["last_activity"] is None


def test_another_users_conversation_is_not_this_users_activity(authz_api) -> None:
    stamp = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)

    with authz_api.session_factory() as session:
        session.add(
            Conversation(
                user_id=authz_api.user_b_id,
                course_id=authz_api.a_course_id,
                conversation_type=ConversationType.COURSE_QA.value,
                created_at=stamp,
                updated_at=stamp,
            )
        )
        session.commit()

    summary = _summaries(authz_api.client, authz_api.authorization_a)[
        authz_api.a_course_id
    ]

    assert summary["last_activity"] is None


def test_recording_an_exchange_moves_last_activity(upload_api) -> None:
    stale = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)

    with upload_api.session_factory() as session:
        conversation = Conversation(
            user_id=upload_api.user_id,
            course_id=upload_api.course_id,
            conversation_type=ConversationType.COURSE_QA.value,
            created_at=stale,
            updated_at=stale,
        )
        session.add(conversation)
        session.commit()
        conversation_id = conversation.id

    before = _summaries(upload_api.client, upload_api.authorization)[
        upload_api.course_id
    ]["last_activity"]
    assert datetime.fromisoformat(before) == stale

    with upload_api.session_factory() as session:
        stored = session.get(Conversation, conversation_id)
        assert stored is not None
        ConversationService.record_exchange(
            session,
            conversation=stored,
            user_id=upload_api.user_id,
            course_id=upload_api.course_id,
            conversation_type=ConversationType.COURSE_QA,
            question="What is a semaphore?",
            answer="A synchronization primitive.",
        )
        session.commit()

    after = _summaries(upload_api.client, upload_api.authorization)[
        upload_api.course_id
    ]["last_activity"]

    assert datetime.fromisoformat(after) > stale


def test_a_course_without_material_reports_no_documents(upload_api) -> None:
    summary = _summaries(upload_api.client, upload_api.authorization)[
        upload_api.course_id
    ]

    assert summary["status"] == "no_documents"


def test_a_document_still_being_read_reports_processing(upload_api) -> None:
    with upload_api.session_factory() as session:
        course = session.get(Course, upload_api.course_id)
        _add_document(session, course, status="processing", marker="1")
        session.commit()

    summary = _summaries(upload_api.client, upload_api.authorization)[
        upload_api.course_id
    ]

    assert summary["status"] == "processing"


def test_a_read_document_and_no_attempt_reports_ready(upload_api) -> None:
    with upload_api.session_factory() as session:
        course = session.get(Course, upload_api.course_id)
        _add_document(session, course, status="ready", marker="2")
        session.commit()

    summary = _summaries(upload_api.client, upload_api.authorization)[
        upload_api.course_id
    ]

    assert summary["status"] == "ready"


def test_an_attempt_below_the_threshold_reports_practiced(upload_api) -> None:
    with upload_api.session_factory() as session:
        course = session.get(Course, upload_api.course_id)
        _add_document(session, course, status="ready", marker="3")
        quiz = _quiz_with_questions(session, upload_api.course_id, count=4)
        question_ids = _question_ids(session, quiz.id)
        session.commit()

    _submit(upload_api, upload_api.course_id, quiz.id, question_ids, correct=3)

    summary = _summaries(upload_api.client, upload_api.authorization)[
        upload_api.course_id
    ]

    assert summary["average_score"] == pytest.approx(0.75)
    assert summary["status"] == "practiced"


def test_an_attempt_at_the_threshold_reports_mastered(upload_api) -> None:
    with upload_api.session_factory() as session:
        course = session.get(Course, upload_api.course_id)
        _add_document(session, course, status="ready", marker="4")
        quiz = _quiz_with_questions(session, upload_api.course_id, count=5)
        question_ids = _question_ids(session, quiz.id)
        session.commit()

    _submit(upload_api, upload_api.course_id, quiz.id, question_ids, correct=4)

    summary = _summaries(upload_api.client, upload_api.authorization)[
        upload_api.course_id
    ]

    assert summary["average_score"] == pytest.approx(0.8)
    assert summary["status"] == "mastered"


def test_a_fresh_upload_does_not_demote_a_mastered_course(upload_api) -> None:
    with upload_api.session_factory() as session:
        course = session.get(Course, upload_api.course_id)
        _add_document(session, course, status="ready", marker="5")
        quiz = _quiz_with_questions(session, upload_api.course_id, count=5)
        question_ids = _question_ids(session, quiz.id)
        session.commit()

    _submit(upload_api, upload_api.course_id, quiz.id, question_ids, correct=5)

    with upload_api.session_factory() as session:
        course = session.get(Course, upload_api.course_id)
        _add_document(session, course, status="uploaded", marker="6")
        session.commit()

    summary = _summaries(upload_api.client, upload_api.authorization)[
        upload_api.course_id
    ]

    assert summary["status"] == "mastered"


def test_time_spent_sums_the_attempts_that_recorded_it(upload_api) -> None:
    with upload_api.session_factory() as session:
        quiz = _quiz_with_questions(session, upload_api.course_id)
        question_ids = _question_ids(session, quiz.id)

    _submit(
        upload_api,
        upload_api.course_id,
        quiz.id,
        question_ids,
        correct=1,
        time_spent_seconds=90,
    )
    _submit(
        upload_api,
        upload_api.course_id,
        quiz.id,
        question_ids,
        correct=2,
        time_spent_seconds=45,
    )

    summary = _summaries(upload_api.client, upload_api.authorization)[
        upload_api.course_id
    ]

    assert summary["total_time_spent_seconds"] == 135


def test_time_spent_is_absent_when_no_attempt_recorded_it(upload_api) -> None:
    with upload_api.session_factory() as session:
        quiz = _quiz_with_questions(session, upload_api.course_id)
        question_ids = _question_ids(session, quiz.id)

    _submit(upload_api, upload_api.course_id, quiz.id, question_ids, correct=1)

    summary = _summaries(upload_api.client, upload_api.authorization)[
        upload_api.course_id
    ]

    assert summary["attempts_count"] == 1
    assert summary["total_time_spent_seconds"] is None
