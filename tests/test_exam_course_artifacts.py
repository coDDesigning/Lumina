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


# The shape mock_payload() actually has: three multiple choice questions, two on
# the higher-ranked topic. Stated explicitly because the paper is now validated
# against the split the request asked for.
MOCK_REQUEST = {
    "question_count": 3,
    "question_mix": [{"question_type": "multiple_choice", "count": 3}],
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
    if json is None:
        json = dict(MOCK_REQUEST) if kind == "mock-exam" else {}
    response = authz_api.client.post(
        f"/api/courses/{authz_api.a_course_id}/exam-mode/{kind}",
        json=json,
        headers=authz_api.authorization_a,
    )
    return response, provider


# --------------------------------------------------------------- the mock exam


def test_the_paper_covers_every_planned_topic_weighted_by_the_plan(
    authz_api, planned_course, monkeypatch
) -> None:
    response, provider = ask(authz_api, "mock-exam", monkeypatch)

    assert response.status_code == 200, response.text
    # The prompt states an exact quota, not a weight to interpret: the split is
    # calculated here and the paper is refused if it does not match.
    assert "Graph Traversal: exactly 2 questions" in provider.prompt
    assert "Dynamic Programming: exactly 1 question" in provider.prompt
    assert "multiple_choice: exactly 3 questions" in provider.prompt


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


def test_a_failure_while_persisting_refunds_the_charge_too(
    authz_api, planned_course, monkeypatch
) -> None:
    """The window between a successful generation and a written row."""
    before = balance_of(authz_api.session_factory, authz_api.user_a_id)

    def exploding_record(*args, **kwargs):
        raise RuntimeError("the database went away")

    monkeypatch.setattr(
        "services.exam_course_artifacts.GeneratedOutputService.record",
        exploding_record,
    )
    response, _ = ask(authz_api, "mock-exam", monkeypatch)

    assert response.status_code >= 500
    assert balance_of(authz_api.session_factory, authz_api.user_a_id) == before
    assert quizzes_of(authz_api.session_factory, authz_api.a_course_id) == []
    with authz_api.session_factory() as session:
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


# --------------------------------------------------------- configuring the paper


def true_false_question(number: int, *, topic: str = "Graph Traversal", answer=True):
    """A true/false question, which has no options key to leave out.

    The generated-question union forbids unknown fields, so a null options key
    is rejected the same way a populated one would be.
    """
    return {
        "question_number": number,
        "question_type": "true_false",
        "topic": topic,
        "question": f"Statement {number}: BFS uses a queue?",
        "difficulty": "medium",
        "correct_answer": answer,
        "explanation": "BFS uses a queue.",
        "citations": ["S1"],
    }


def mock_request(**overrides) -> dict:
    payload = dict(MOCK_REQUEST)
    payload.update(overrides)
    return payload


def test_a_requested_duration_becomes_the_paper_s_own_time_limit(
    authz_api, planned_course, monkeypatch
) -> None:
    """Minutes on the wire, seconds in the row: converted once, at the boundary."""
    response, _ = ask(
        authz_api, "mock-exam", monkeypatch, json=mock_request(duration_minutes=90)
    )

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["duration_minutes"] == 90
    assert data["time_limit_seconds"] == 5400

    with authz_api.session_factory() as session:
        quiz = session.get(Quiz, data["quiz"]["quiz_id"])
        assert quiz.time_limit_seconds == 5400


def test_a_paper_covering_a_chosen_subset_leaves_the_other_topics_out(
    authz_api, planned_course, monkeypatch
) -> None:
    response, provider = ask(
        authz_api,
        "mock-exam",
        monkeypatch,
        payload=mock_payload(questions=[question(1), question(2), question(3)]),
        json=mock_request(topic_keys=["graph-traversal"]),
    )

    assert response.status_code == 200, response.text
    assert "Graph Traversal: exactly 3 questions" in provider.prompt


def test_an_exact_type_mix_reaches_the_prompt_and_is_enforced(
    authz_api, planned_course, monkeypatch
) -> None:
    response, provider = ask(
        authz_api,
        "mock-exam",
        monkeypatch,
        payload=mock_payload(
            questions=[
                question(1),
                question(2, topic="Dynamic Programming"),
                true_false_question(3),
                true_false_question(4, topic="Dynamic Programming", answer=False),
            ]
        ),
        json=mock_request(
            question_count=4,
            question_mix=[
                {"question_type": "multiple_choice", "count": 2},
                {"question_type": "true_false", "count": 2},
            ],
        ),
    )

    assert response.status_code == 200, response.text
    assert "multiple_choice: exactly 2 questions" in provider.prompt
    assert "true_false: exactly 2 questions" in provider.prompt


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        pytest.param(
            {"question_count": 3, "topic_keys": ["not-a-planned-topic"]},
            "a topic the plan never listed",
            id="topic_outside_the_plan",
        ),
        pytest.param(
            {
                "question_count": 1,
                "question_mix": [{"question_type": "multiple_choice", "count": 1}],
                "topic_keys": ["graph-traversal", "dynamic-programming"],
            },
            "fewer questions than topics to cover",
            id="too_few_questions_for_full_coverage",
        ),
    ],
)
def test_a_paper_that_cannot_be_built_is_refused_before_anything_is_spent(
    authz_api, planned_course, monkeypatch, body, reason
) -> None:
    before = balance_of(authz_api.session_factory, authz_api.user_a_id)

    response, provider = ask(authz_api, "mock-exam", monkeypatch, json=body)

    assert response.status_code == 422, f"{reason}: {response.text}"
    assert response.headers["X-Error-Code"] == "mock_exam_configuration_invalid"
    assert provider.calls == 0
    assert balance_of(authz_api.session_factory, authz_api.user_a_id) == before
    assert quizzes_of(authz_api.session_factory, authz_api.a_course_id) == []


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        pytest.param(
            {
                "question_count": 5,
                "question_mix": [{"question_type": "multiple_choice", "count": 3}],
            },
            "a mix that does not sum to the paper length",
            id="mix_total_mismatch",
        ),
        pytest.param(
            {
                "question_count": 3,
                "question_mix": [{"question_type": "multiple_choice", "count": 0}],
            },
            "a type asked for zero times",
            id="zero_type_count",
        ),
        pytest.param(
            {
                "question_count": 3,
                "question_mix": [{"question_type": "essay", "count": 3}],
            },
            "a type no quiz row can hold",
            id="unsupported_question_type",
        ),
        pytest.param(
            {"question_count": 3, "duration_minutes": 1},
            "a sitting shorter than the minimum",
            id="duration_below_minimum",
        ),
        pytest.param(
            {"question_count": 3, "duration_minutes": 1000},
            "a sitting longer than the maximum",
            id="duration_above_maximum",
        ),
        pytest.param(
            {"question_count": 3, "topic_keys": ["graph-traversal", "graph-traversal"]},
            "the same topic twice",
            id="repeated_topic_key",
        ),
    ],
)
def test_a_malformed_configuration_never_reaches_a_provider(
    authz_api, planned_course, monkeypatch, body, reason
) -> None:
    before = balance_of(authz_api.session_factory, authz_api.user_a_id)

    response, provider = ask(authz_api, "mock-exam", monkeypatch, json=body)

    assert response.status_code == 422, f"{reason}: {response.text}"
    assert provider.calls == 0
    assert balance_of(authz_api.session_factory, authz_api.user_a_id) == before
    assert quizzes_of(authz_api.session_factory, authz_api.a_course_id) == []


# --------------------------------------------------- validating what came back


@pytest.mark.parametrize(
    ("payload", "reason"),
    [
        pytest.param(
            mock_payload(questions=[question(1), question(2)]),
            "one question short of the paper that was asked for",
            id="wrong_total_count",
        ),
        pytest.param(
            mock_payload(
                questions=[question(1), question(2), question(3, topic="Recursion")]
            ),
            "a topic outside the requested coverage",
            id="topic_outside_coverage",
        ),
        pytest.param(
            mock_payload(questions=[question(1), question(2), question(3)]),
            "no questions at all on a topic that was allocated some",
            id="missing_requested_topic",
        ),
        pytest.param(
            mock_payload(
                questions=[
                    question(1),
                    question(1, topic="Dynamic Programming"),
                    question(3),
                ]
            ),
            "two questions sharing a number",
            id="duplicate_question_numbers",
        ),
        pytest.param(
            mock_payload(
                questions=[
                    question(1),
                    question(2, topic="Dynamic Programming"),
                    question(3, question="Question 1?"),
                ]
            ),
            "two questions asking the same thing",
            id="duplicate_question_text",
        ),
        pytest.param(
            mock_payload(
                questions=[
                    question(1),
                    question(2, topic="Dynamic Programming"),
                    question(3, citations=["S999"]),
                ]
            ),
            "a question grounded in nothing that resolves",
            id="unresolved_citation",
        ),
    ],
)
def test_a_paper_that_is_not_the_one_requested_is_refused_whole(
    authz_api, planned_course, monkeypatch, payload, reason
) -> None:
    """Fourteen valid questions out of fifteen is not a partial success.

    The student asked for a paper of a stated shape; quietly handing back a
    different one spends their credit on work that was not done.
    """
    before = balance_of(authz_api.session_factory, authz_api.user_a_id)

    response, _ = ask(authz_api, "mock-exam", monkeypatch, payload=payload)

    assert response.status_code == 500, f"{reason}: {response.text}"
    assert response.headers["X-Error-Code"] == AiErrorCode.INVALID_GENERATED_STRUCTURE
    assert quizzes_of(authz_api.session_factory, authz_api.a_course_id) == []
    assert (
        outputs_of(
            authz_api.session_factory,
            authz_api.a_course_id,
            OUTPUT_TYPE_EXAM_MOCK_EXAM,
        )
        == []
    )
    assert balance_of(authz_api.session_factory, authz_api.user_a_id) == before


def test_a_type_count_that_misses_the_quota_is_refused(
    authz_api, planned_course, monkeypatch
) -> None:
    response, _ = ask(
        authz_api,
        "mock-exam",
        monkeypatch,
        json=mock_request(
            question_mix=[
                {"question_type": "multiple_choice", "count": 2},
                {"question_type": "true_false", "count": 1},
            ]
        ),
    )

    assert response.status_code == 500, response.text
    assert response.headers["X-Error-Code"] == AiErrorCode.INVALID_GENERATED_STRUCTURE
    assert quizzes_of(authz_api.session_factory, authz_api.a_course_id) == []


def test_the_mock_exam_is_reopened_without_reaching_a_provider(
    authz_api, planned_course, monkeypatch
) -> None:
    ask(authz_api, "mock-exam", monkeypatch)

    def forbidden(*args, **kwargs):
        raise AssertionError("reopening a mock exam must not reach a provider")

    monkeypatch.setattr(exam_mode_route, "get_text_generation_provider", forbidden)
    response = authz_api.client.get(
        f"/api/courses/{authz_api.a_course_id}/exam-mode/mock-exam",
        headers=authz_api.authorization_a,
    )

    assert response.status_code == 200, response.text
    question_view = response.json()["data"]["questions"][0]
    assert question_view["correct_option_index"] is None
    assert question_view["explanation"] == ""
