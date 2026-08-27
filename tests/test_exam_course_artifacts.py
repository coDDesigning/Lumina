"""Mock exams and review sheets: priced on their own, drawn from the whole plan."""

import pytest
from sqlalchemy import select

import routes.exam_mode as exam_mode_route
from backend.app.models import (
    OUTPUT_TYPE_EXAM_MOCK_EXAM,
    OUTPUT_TYPE_EXAM_REVIEW_SHEET,
    CreditTransaction,
    ExamTopicUnlock,
    GeneratedOutput,
    Quiz,
    QuizQuestion,
    User,
)
from conftest import assert_balance_is_derivable, set_balance
from services.credits import GENERATION_CREDIT_COSTS
from services.text_generation import TextGenerationError
from tests.test_exam_mode import (  # noqa: F401 - fixtures
    CountingProvider,
    create_plan,
    exam_course,
    run_analysis,
)
from utils.ai_errors import AiErrorCode

MOCK_PRICE = GENERATION_CREDIT_COSTS["exam_mock_exam"]
REVIEW_PRICE = GENERATION_CREDIT_COSTS["exam_review_sheet"]


def question(number: int, topic: str = "Graph Traversal", **overrides) -> dict:
    payload = {
        "question_number": number,
        "question_type": "multiple_choice",
        "topic": topic,
        "question": f"Question {number}?",
        "difficulty": "medium",
        "options": ["A queue", "A stack", "A heap", "A set"],
        "correct_option_index": 0,
        "explanation": "BFS uses a queue.",
        "citations": ["S1"],
    }
    payload.update(overrides)
    return payload


def mock_payload(**overrides) -> dict:
    payload = {
        "title": "Algorithms mock exam",
        "questions": [
            question(1),
            question(2, topic="Dynamic Programming"),
            question(3),
        ],
    }
    payload.update(overrides)
    return payload


def review_payload(**overrides) -> dict:
    payload = {
        "title": "Last-minute review",
        "topics": [
            {
                "topic_label": "Graph Traversal",
                "must_remember": [
                    {"text": "BFS visits by distance.", "citations": ["S1"]}
                ],
                "traps": [{"text": "Mark on enqueue.", "citations": ["S1"]}],
            }
        ],
        "final_checks": [{"text": "Know the queue invariant.", "citations": ["S1"]}],
        "confidence_notes": "",
    }
    payload.update(overrides)
    return payload


def balance_of(session_factory, user_id: int):
    with session_factory() as session:
        return session.get(User, user_id).credits


def unlocks(session_factory, course_id: int):
    with session_factory() as session:
        return session.scalars(
            select(ExamTopicUnlock).where(ExamTopicUnlock.course_id == course_id)
        ).all()


def quizzes_of(session_factory, course_id: int):
    with session_factory() as session:
        return session.scalars(
            select(Quiz).where(Quiz.course_id == course_id).order_by(Quiz.id)
        ).all()


def outputs_of(session_factory, course_id: int, output_type: str):
    with session_factory() as session:
        return session.scalars(
            select(GeneratedOutput).where(
                GeneratedOutput.course_id == course_id,
                GeneratedOutput.output_type == output_type,
            )
        ).all()


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


def ask(authz_api, kind: str, monkeypatch, *, payload=None, json=None):
    provider = CountingProvider(
        payload
        if payload is not None
        else (mock_payload() if kind == "mock-exam" else review_payload())
    )
    monkeypatch.setattr(
        exam_mode_route, "get_text_generation_provider", lambda *a, **k: provider
    )
    response = authz_api.client.post(
        f"/api/courses/{authz_api.a_course_id}/exam-mode/{kind}",
        json=json if json is not None else {},
        headers=authz_api.authorization_a,
    )
    return response, provider


# --------------------------------------------------------------- the mock exam


def test_the_paper_covers_every_planned_topic_weighted_by_the_plan(
    authz_api, planned_course, monkeypatch
) -> None:
    response, provider = ask(authz_api, "mock-exam", monkeypatch)

    assert response.status_code == 200, response.text
    assert "Graph Traversal (weight 2)" in provider.prompt
    assert "Dynamic Programming (weight 1)" in provider.prompt


def test_the_mock_exam_hides_its_answers(
    authz_api, planned_course, monkeypatch
) -> None:
    response, _ = ask(authz_api, "mock-exam", monkeypatch)

    data = response.json()["data"]
    assert data["answers_hidden"] is True
    for entry in data["quiz"]["questions"]:
        assert entry["correct_answer"] is None
        assert entry["explanation"] == ""


def test_the_mock_exam_is_a_real_quiz_the_student_can_sit(
    authz_api, planned_course, monkeypatch
) -> None:
    response, _ = ask(authz_api, "mock-exam", monkeypatch)
    quiz_id = response.json()["data"]["quiz"]["quiz_id"]

    with authz_api.session_factory() as session:
        quiz = session.get(Quiz, quiz_id)
        rows = session.scalars(
            select(QuizQuestion)
            .where(QuizQuestion.quiz_id == quiz_id)
            .order_by(QuizQuestion.question_index)
        ).all()

        assert quiz.purpose == "exam_mock_exam"
        assert quiz.exam_plan_output_id == planned_course["plan_id"]
        assert quiz.exam_topic_key is None
        assert all(row.correct_answer is not None for row in rows)


def test_a_mock_exam_keeps_each_question_s_own_topic(
    authz_api, planned_course, monkeypatch
) -> None:
    """A paper spanning topics has no single label to override with."""
    response, _ = ask(authz_api, "mock-exam", monkeypatch)
    quiz_id = response.json()["data"]["quiz"]["quiz_id"]

    with authz_api.session_factory() as session:
        topics = {
            row.topic
            for row in session.scalars(
                select(QuizQuestion).where(QuizQuestion.quiz_id == quiz_id)
            ).all()
        }

    assert topics == {"Graph Traversal", "Dynamic Programming"}


def test_the_mock_exam_is_shaped_by_this_course_s_own_past_questions(
    authz_api, planned_course, monkeypatch
) -> None:
    _, provider = ask(authz_api, "mock-exam", monkeypatch)

    assert "Explain breadth-first search" in provider.prompt


def test_the_requested_question_count_reaches_the_prompt(
    authz_api, planned_course, monkeypatch
) -> None:
    _, provider = ask(authz_api, "mock-exam", monkeypatch, json={"question_count": 12})

    assert "Write exactly 12 questions in total" in provider.prompt


# --------------------------------------------------------------- review sheet


def test_the_review_sheet_reads_as_a_reminder_not_a_guide(
    authz_api, planned_course, monkeypatch
) -> None:
    response, _ = ask(authz_api, "review-sheet", monkeypatch)

    assert response.status_code == 200, response.text
    sheet = response.json()["data"]["review_sheet"]
    assert sheet["plan_output_id"] == planned_course["plan_id"]
    entry = sheet["topics"][0]
    assert entry["topic_key"] == "graph-traversal"
    assert entry["must_remember"][0]["citations"]
    assert sheet["final_checks"][0]["text"]


def test_an_unknown_citation_key_is_dropped_before_persistence(
    authz_api, planned_course, monkeypatch
) -> None:
    response, _ = ask(
        authz_api,
        "review-sheet",
        monkeypatch,
        payload=review_payload(
            topics=[
                {
                    "topic_label": "Graph Traversal",
                    "must_remember": [{"text": "BFS.", "citations": ["S999"]}],
                    "traps": [],
                }
            ]
        ),
    )

    sheet = response.json()["data"]["review_sheet"]
    assert sheet["topics"][0]["must_remember"][0]["citations"] == []


def test_a_review_sheet_is_reopened_without_reaching_a_provider(
    authz_api, planned_course, monkeypatch
) -> None:
    ask(authz_api, "review-sheet", monkeypatch)

    def forbidden(*args, **kwargs):
        raise AssertionError("reopening a review sheet must never reach a provider")

    monkeypatch.setattr(exam_mode_route, "get_text_generation_provider", forbidden)
    response = authz_api.client.get(
        f"/api/courses/{authz_api.a_course_id}/exam-mode/review-sheet",
        headers=authz_api.authorization_a,
    )

    assert response.status_code == 200, response.text
    assert response.json()["data"]["title"] == "Last-minute review"


# --------------------------------------------------------------- pricing


def test_each_is_priced_on_its_own_rather_than_under_a_topic_unlock(
    authz_api, planned_course, monkeypatch
) -> None:
    before = balance_of(authz_api.session_factory, authz_api.user_a_id)

    mock, _ = ask(authz_api, "mock-exam", monkeypatch)
    review, _ = ask(authz_api, "review-sheet", monkeypatch)

    assert mock.json()["data"]["credits_charged"] == MOCK_PRICE
    assert review.json()["data"]["credits_charged"] == REVIEW_PRICE
    assert (
        balance_of(authz_api.session_factory, authz_api.user_a_id)
        == before - MOCK_PRICE - REVIEW_PRICE
    )
    assert unlocks(authz_api.session_factory, authz_api.a_course_id) == []


def test_a_second_mock_exam_is_charged_again(
    authz_api, planned_course, monkeypatch
) -> None:
    """Unlike a topic, a paper is bought each time it is written."""
    before = balance_of(authz_api.session_factory, authz_api.user_a_id)

    ask(authz_api, "mock-exam", monkeypatch)
    ask(authz_api, "mock-exam", monkeypatch)

    assert (
        balance_of(authz_api.session_factory, authz_api.user_a_id)
        == before - 2 * MOCK_PRICE
    )


def test_an_empty_balance_is_refused_before_the_provider_is_reached(
    authz_api, planned_course, monkeypatch
) -> None:
    set_balance(authz_api.session_factory, authz_api.user_a_id, 0.0)

    response, provider = ask(authz_api, "mock-exam", monkeypatch)

    assert response.status_code == 402
    assert response.headers["X-Error-Code"] == AiErrorCode.INSUFFICIENT_CREDITS
    assert provider.calls == 0
    assert quizzes_of(authz_api.session_factory, authz_api.a_course_id) == []


def test_a_provider_failure_refunds_the_charge_exactly_once(
    authz_api, planned_course, monkeypatch
) -> None:
    before = balance_of(authz_api.session_factory, authz_api.user_a_id)
    provider = CountingProvider(error=TextGenerationError("the provider is down"))
    monkeypatch.setattr(
        exam_mode_route, "get_text_generation_provider", lambda *a, **k: provider
    )

    response = authz_api.client.post(
        f"/api/courses/{authz_api.a_course_id}/exam-mode/mock-exam",
        json={},
        headers=authz_api.authorization_a,
    )

    assert response.status_code >= 500
    assert provider.calls == 1
    assert balance_of(authz_api.session_factory, authz_api.user_a_id) == before
    assert quizzes_of(authz_api.session_factory, authz_api.a_course_id) == []

    with authz_api.session_factory() as session:
        refunds = session.scalars(
            select(CreditTransaction).where(
                CreditTransaction.user_id == authz_api.user_a_id,
                CreditTransaction.refunds_transaction_id.is_not(None),
            )
        ).all()
        assert len(refunds) == 1
        assert_balance_is_derivable(session, authz_api.user_a_id)


def test_the_prices_are_served_rather_than_left_for_a_client_to_guess(
    authz_api,
) -> None:
    policy = authz_api.client.get(
        "/api/users/me/credits", headers=authz_api.authorization_a
    ).json()["data"]

    assert policy["generation_costs"]["exam_mock_exam"] == MOCK_PRICE
    assert policy["generation_costs"]["exam_review_sheet"] == REVIEW_PRICE


# --------------------------------------------------------------- refusals


def test_a_course_with_no_plan_is_told_to_make_one(
    authz_api,
    exam_course,  # noqa: F811
    monkeypatch,
) -> None:
    response, provider = ask(authz_api, "mock-exam", monkeypatch)

    assert response.status_code == 409
    assert response.headers["X-Error-Code"] == AiErrorCode.EXAM_PLAN_REQUIRED
    assert provider.calls == 0
    assert balance_of(authz_api.session_factory, authz_api.user_a_id) is not None


def test_a_stranger_and_an_administrator_are_both_refused_a_write(
    authz_api, planned_course, monkeypatch
) -> None:
    before = balance_of(authz_api.session_factory, authz_api.user_a_id)
    provider = CountingProvider(mock_payload())
    monkeypatch.setattr(
        exam_mode_route, "get_text_generation_provider", lambda *a, **k: provider
    )

    for headers in (authz_api.authorization_b, authz_api.authorization_admin):
        response = authz_api.client.post(
            f"/api/courses/{authz_api.a_course_id}/exam-mode/mock-exam",
            json={},
            headers=headers,
        )
        assert response.status_code == 404

    assert provider.calls == 0
    assert balance_of(authz_api.session_factory, authz_api.user_a_id) == before
    assert (
        outputs_of(
            authz_api.session_factory,
            authz_api.a_course_id,
            OUTPUT_TYPE_EXAM_MOCK_EXAM,
        )
        == []
    )


def test_the_two_kinds_do_not_collide(authz_api, planned_course, monkeypatch) -> None:
    ask(authz_api, "mock-exam", monkeypatch)
    ask(authz_api, "review-sheet", monkeypatch)

    assert (
        len(
            outputs_of(
                authz_api.session_factory,
                authz_api.a_course_id,
                OUTPUT_TYPE_EXAM_MOCK_EXAM,
            )
        )
        == 1
    )
    assert (
        len(
            outputs_of(
                authz_api.session_factory,
                authz_api.a_course_id,
                OUTPUT_TYPE_EXAM_REVIEW_SHEET,
            )
        )
        == 1
    )
