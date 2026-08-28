"""Similar questions: modelled on real past questions, and sat like any other quiz."""

import pytest
from sqlalchemy import select

import routes.exam_mode as exam_mode_route
from backend.app.models import (
    OUTPUT_TYPE_EXAM_SIMILAR_QUESTIONS,
    QUIZ_PURPOSE_EXAM_SIMILAR_QUESTIONS,
    ExamTopicUnlock,
    GeneratedOutput,
    PastExamQuestion,
    Quiz,
    QuizQuestion,
    User,
)
from services.credits import GENERATION_CREDIT_COSTS
from tests.test_exam_mode import (  # noqa: F401 - fixtures
    CountingProvider,
    create_plan,
    exam_course,
    extract_questions,
    extraction_payload,
    past_exam_question,
    run_analysis,
)
from utils.ai_errors import AiErrorCode

UNLOCK_PRICE = GENERATION_CREDIT_COSTS["exam_topic_unlock"]


def generated_question(**overrides) -> dict:
    question = {
        "question_number": 1,
        "question_type": "multiple_choice",
        "topic": "Graph Traversal",
        "difficulty": "medium",
        "question": "Which traversal explores a graph using an explicit stack?",
        "options": ["Depth-first search", "Breadth-first search", "Dijkstra", "Prim"],
        "correct_option_index": 0,
        "explanation": "Depth-first search uses a stack.",
        "citations": ["S1"],
    }
    question.update(overrides)
    return question


def similar_payload(**overrides) -> dict:
    payload = {
        "title": "Graph Traversal, in the style of the 2024 paper",
        "questions": [{"source_number": 1, "question": generated_question()}],
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


def outputs_of(session_factory, course_id: int):
    with session_factory() as session:
        return session.scalars(
            select(GeneratedOutput).where(
                GeneratedOutput.course_id == course_id,
                GeneratedOutput.output_type == OUTPUT_TYPE_EXAM_SIMILAR_QUESTIONS,
            )
        ).all()


def similar_quizzes(session_factory, course_id: int):
    with session_factory() as session:
        return session.scalars(
            select(Quiz).where(
                Quiz.course_id == course_id,
                Quiz.purpose == QUIZ_PURPOSE_EXAM_SIMILAR_QUESTIONS,
            )
        ).all()


def stored_questions(session_factory, quiz_id: int):
    with session_factory() as session:
        return session.scalars(
            select(QuizQuestion)
            .where(QuizQuestion.quiz_id == quiz_id)
            .order_by(QuizQuestion.question_index)
        ).all()


def source_ids(session_factory, document_id):
    with session_factory() as session:
        return [
            row.id
            for row in session.scalars(
                select(PastExamQuestion)
                .where(PastExamQuestion.document_id == document_id)
                .order_by(PastExamQuestion.position)
            ).all()
        ]


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


def ask(authz_api, monkeypatch, *, topic="graph-traversal", payload=None, body=None):
    provider = CountingProvider(payload if payload is not None else similar_payload())
    monkeypatch.setattr(
        exam_mode_route, "get_text_generation_provider", lambda *a, **k: provider
    )
    response = authz_api.client.post(
        f"/api/courses/{authz_api.a_course_id}"
        f"/exam-mode/topics/{topic}/similar-questions",
        json={"question_count": 1} if body is None else body,
        headers=authz_api.authorization_a,
    )
    return response, provider


# --------------------------------------------------------------- persistence


def test_a_similar_question_set_is_a_real_quiz_carrying_its_provenance(
    authz_api, planned_course, monkeypatch
) -> None:
    """The whole point: these are rows in quizzes, not a document beside them.

    Anything else would need its own attempts, grading, mastery, and progress.
    """
    expected = source_ids(authz_api.session_factory, planned_course["paper_id"])

    response, _ = ask(authz_api, monkeypatch)

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["source_question_ids"] == expected

    quizzes = similar_quizzes(authz_api.session_factory, authz_api.a_course_id)
    assert len(quizzes) == 1
    quiz = quizzes[0]
    assert quiz.purpose == QUIZ_PURPOSE_EXAM_SIMILAR_QUESTIONS
    assert quiz.exam_plan_output_id == planned_course["plan_id"]
    assert quiz.exam_topic_key == "graph-traversal"
    assert data["quiz"]["quiz_id"] == quiz.id

    rows = stored_questions(authz_api.session_factory, quiz.id)
    assert len(rows) == 1
    assert rows[0].source_past_exam_question_id == expected[0]
    assert rows[0].topic == "Graph Traversal"
    assert rows[0].citations


def test_the_answers_are_hidden_until_the_set_is_attempted(
    authz_api, planned_course, monkeypatch
) -> None:
    """A question set a student can read the answers to is not an assessment."""
    response, _ = ask(authz_api, monkeypatch)

    question = response.json()["data"]["quiz"]["questions"][0]
    assert response.json()["data"]["answers_hidden"] is True
    assert question["correct_option_index"] is None
    assert question["correct_answer"] is None
    assert question["explanation"] == ""

    quiz_id = response.json()["data"]["quiz"]["quiz_id"]
    rows = stored_questions(authz_api.session_factory, quiz_id)
    assert rows[0].correct_option_index == 0, "the row still knows the answer"


def test_it_is_attempted_and_graded_by_the_ordinary_quiz_lifecycle(
    authz_api, planned_course, monkeypatch
) -> None:
    """No second grader: the set goes through the attempt endpoint like any quiz."""
    response, _ = ask(authz_api, monkeypatch)
    quiz_id = response.json()["data"]["quiz"]["quiz_id"]
    question_id = response.json()["data"]["quiz"]["questions"][0]["question_id"]

    attempt = authz_api.client.post(
        f"/api/courses/{authz_api.a_course_id}/quizzes/{quiz_id}/attempts",
        json={"answers": [{"question_id": question_id, "selected_option_index": 0}]},
        headers=authz_api.authorization_a,
    )

    assert attempt.status_code == 201, attempt.text
    payload = attempt.json()["data"]
    assert payload["correct_count"] == 1
    assert payload["score"] == pytest.approx(1.0)
    assert payload["answers"][0]["topic"] == "Graph Traversal"


def test_the_attempt_moves_the_planned_topic_s_mastery(
    authz_api, planned_course, monkeypatch
) -> None:
    """Mastery filed under the plan's own label is what the next plan can read."""
    response, _ = ask(authz_api, monkeypatch)
    quiz_id = response.json()["data"]["quiz"]["quiz_id"]
    question_id = response.json()["data"]["quiz"]["questions"][0]["question_id"]

    authz_api.client.post(
        f"/api/courses/{authz_api.a_course_id}/quizzes/{quiz_id}/attempts",
        json={"answers": [{"question_id": question_id, "selected_option_index": 0}]},
        headers=authz_api.authorization_a,
    )
    progress = authz_api.client.get(
        f"/api/courses/{authz_api.a_course_id}/progress",
        headers=authz_api.authorization_a,
    )

    assert progress.status_code == 200, progress.text
    topics = {row["topic"] for row in progress.json()["data"]["topic_mastery"]}
    assert "Graph Traversal" in topics


def test_the_history_row_is_written_without_the_answers(
    authz_api, planned_course, monkeypatch
) -> None:
    ask(authz_api, monkeypatch)

    outputs = outputs_of(authz_api.session_factory, authz_api.a_course_id)
    assert len(outputs) == 1
    assert '"correct_option_index":null' in outputs[0].content.replace(" ", "")


# --------------------------------------------------------------- the prompt


def test_the_prompt_keeps_past_style_apart_from_the_grounding_material(
    authz_api, planned_course, monkeypatch
) -> None:
    """A past paper says how this course asks; the material says what is true.

    Concatenating them would let an out-of-date paper settle an answer.
    """
    _, provider = ask(authz_api, monkeypatch)
    prompt = provider.prompt

    style_at = prompt.index("OBSERVED PAST-EXAM STYLE")
    material_at = prompt.index("COURSE MATERIAL - FACTUAL SOURCE OF TRUTH")
    original_at = prompt.index("Explain breadth-first search")
    lecture_at = prompt.index("Graph traversal covers BFS and DFS.")

    assert style_at < original_at < material_at, "the original is not style evidence"
    assert lecture_at > material_at, "lecture text is not presented as a past question"
    assert "NEVER AN ANSWER SOURCE" in prompt
    assert "[10 marks]" in prompt


def test_the_prompt_refuses_to_predict_the_real_examination(
    authz_api, planned_course, monkeypatch
) -> None:
    _, provider = ask(authz_api, monkeypatch)

    assert "NOT a prediction of what any future examination will contain" in (
        provider.prompt
    )
    assert "Never claim, imply, or predict" in provider.prompt


def test_the_model_is_never_shown_a_row_identifier(
    authz_api, planned_course, monkeypatch
) -> None:
    """It answers with the position it was shown, never with a database id."""
    expected = source_ids(authz_api.session_factory, planned_course["paper_id"])
    _, provider = ask(authz_api, monkeypatch)

    assert f"source_question_id: {expected[0]}" not in provider.prompt
    assert "source_number" in provider.prompt


def test_the_paper_is_read_from_its_stored_rows_rather_than_re_extracted(
    authz_api, planned_course, monkeypatch
) -> None:
    extract_questions(
        authz_api.session_factory,
        planned_course["paper_id"],
        extraction_payload(
            past_exam_question(question_text="Prove BFS finds shortest paths.")
        ),
    )

    def forbidden(*args, **kwargs):
        raise AssertionError("writing similar questions must not re-extract a paper")

    monkeypatch.setattr(
        "services.exam_question_extraction.get_text_generation_provider", forbidden
    )
    _, provider = ask(authz_api, monkeypatch)

    assert "Prove BFS finds shortest paths." in provider.prompt


# --------------------------------------------------------------- the request


def test_an_explicitly_named_source_question_is_honoured(
    authz_api, planned_course, monkeypatch
) -> None:
    expected = source_ids(authz_api.session_factory, planned_course["paper_id"])

    response, _ = ask(
        authz_api,
        monkeypatch,
        body={"question_count": 1, "source_question_ids": expected[:1]},
    )

    assert response.status_code == 200, response.text
    assert response.json()["data"]["source_question_ids"] == expected[:1]


@pytest.mark.parametrize(
    ("body", "status"),
    [
        pytest.param({"question_count": 0}, 422, id="question_count_below_minimum"),
        pytest.param({"question_count": 21}, 422, id="question_count_above_maximum"),
        pytest.param(
            {"question_count": 1, "source_question_ids": [1, 1]},
            422,
            id="duplicate_source_ids",
        ),
        pytest.param(
            {"question_count": 1, "source_question_ids": []},
            422,
            id="empty_source_ids",
        ),
        pytest.param(
            {"question_count": 1, "difficulty_policy": "impossible"},
            422,
            id="unknown_difficulty_policy",
        ),
        pytest.param(
            {"question_count": 1, "requested_question_types": ["essay"]},
            422,
            id="unsupported_question_type",
        ),
        pytest.param(
            {
                "question_count": 1,
                "requested_question_types": ["multiple_choice", "multiple_choice"],
            },
            422,
            id="duplicate_question_types",
        ),
    ],
)
def test_a_malformed_request_is_refused_before_anything_is_spent(
    authz_api, planned_course, monkeypatch, body, status
) -> None:
    before = balance_of(authz_api.session_factory, authz_api.user_a_id)

    response, provider = ask(authz_api, monkeypatch, body=body)

    assert response.status_code == status, response.text
    assert provider.calls == 0
    assert balance_of(authz_api.session_factory, authz_api.user_a_id) == before
    assert similar_quizzes(authz_api.session_factory, authz_api.a_course_id) == []


def test_a_source_question_from_another_course_is_answered_as_a_missing_one(
    authz_api, planned_course, monkeypatch
) -> None:
    """Distinguishing "not yours" from "does not exist" would leak what exists."""
    before = balance_of(authz_api.session_factory, authz_api.user_a_id)

    response, provider = ask(
        authz_api,
        monkeypatch,
        body={"question_count": 1, "source_question_ids": [999_999]},
    )

    assert response.status_code == 404
    assert provider.calls == 0
    assert balance_of(authz_api.session_factory, authz_api.user_a_id) == before
    assert unlocks(authz_api.session_factory, authz_api.a_course_id) == []


def test_a_topic_this_course_never_examined_costs_nothing_to_ask_about(
    authz_api, planned_course, monkeypatch
) -> None:
    response, provider = ask(authz_api, monkeypatch, topic="dynamic-programming")

    assert response.status_code == 409
    assert response.headers["X-Error-Code"] == AiErrorCode.EXAM_ANALYSIS_REQUIRED
    assert provider.calls == 0
    assert unlocks(authz_api.session_factory, authz_api.a_course_id) == []


# --------------------------------------------------------------- validation


@pytest.mark.parametrize(
    ("payload", "reason"),
    [
        pytest.param(
            similar_payload(questions=[]), "no questions at all", id="empty_set"
        ),
        pytest.param(
            similar_payload(
                questions=[
                    {"source_number": 1, "question": generated_question()},
                    {
                        "source_number": 1,
                        "question": generated_question(
                            question_number=2, question="A second, different question?"
                        ),
                    },
                ]
            ),
            "more questions than were asked for",
            id="too_many_questions",
        ),
        pytest.param(
            similar_payload(
                questions=[{"source_number": 99, "question": generated_question()}]
            ),
            "a source number nobody supplied",
            id="unknown_source_number",
        ),
        pytest.param(
            similar_payload(
                questions=[
                    {
                        "source_number": 1,
                        "question": generated_question(
                            question=(
                                "Explain breadth-first search and give its complexity."
                            )
                        ),
                    }
                ]
            ),
            "a verbatim copy of the original",
            id="verbatim_copy_of_source",
        ),
        pytest.param(
            similar_payload(
                questions=[
                    {
                        "source_number": 1,
                        "question": generated_question(citations=["S999"]),
                    }
                ]
            ),
            "citations that resolve to nothing",
            id="all_citations_unresolved",
        ),
        pytest.param(
            similar_payload(
                questions=[
                    {
                        "source_number": 1,
                        "question": generated_question(citations=[]),
                    }
                ]
            ),
            "no citation at all",
            id="no_citations",
        ),
        pytest.param(
            similar_payload(
                questions=[
                    {
                        "source_number": 1,
                        "question": generated_question(topic="Dynamic Programming"),
                    }
                ]
            ),
            "a question about a different planned topic",
            id="topic_drifted_onto_another_plan_topic",
        ),
        pytest.param(
            similar_payload(
                questions=[
                    {
                        "source_number": 1,
                        "question": generated_question(difficulty="easy"),
                    }
                ]
            ),
            "a difficulty the source did not have",
            id="difficulty_does_not_match_source",
        ),
    ],
)
def test_an_invalid_set_is_refused_whole_and_refunded(
    authz_api, planned_course, monkeypatch, payload, reason
) -> None:
    """A set that is short or wrong is refused entirely, never persisted partially.

    A student who paid for questions and silently received fewer has been
    charged for work that was not done.
    """
    before = balance_of(authz_api.session_factory, authz_api.user_a_id)

    response, _ = ask(authz_api, monkeypatch, payload=payload)

    assert response.status_code == 500, f"{reason}: {response.text}"
    assert response.headers["X-Error-Code"] == AiErrorCode.INVALID_GENERATED_STRUCTURE
    assert similar_quizzes(authz_api.session_factory, authz_api.a_course_id) == []
    assert outputs_of(authz_api.session_factory, authz_api.a_course_id) == []
    assert balance_of(authz_api.session_factory, authz_api.user_a_id) == before
    assert unlocks(authz_api.session_factory, authz_api.a_course_id) == []


def test_two_generated_questions_that_ask_the_same_thing_are_refused(
    authz_api, planned_course, monkeypatch
) -> None:
    before = balance_of(authz_api.session_factory, authz_api.user_a_id)

    response, _ = ask(
        authz_api,
        monkeypatch,
        body={"question_count": 2},
        payload=similar_payload(
            questions=[
                {"source_number": 1, "question": generated_question()},
                {"source_number": 1, "question": generated_question(question_number=2)},
            ]
        ),
    )

    assert response.status_code == 500, response.text
    assert response.headers["X-Error-Code"] == AiErrorCode.INVALID_GENERATED_STRUCTURE
    assert similar_quizzes(authz_api.session_factory, authz_api.a_course_id) == []
    assert balance_of(authz_api.session_factory, authz_api.user_a_id) == before


def test_a_question_type_that_was_not_requested_is_refused(
    authz_api, planned_course, monkeypatch
) -> None:
    response, _ = ask(
        authz_api,
        monkeypatch,
        body={"question_count": 1, "requested_question_types": ["short_answer"]},
        payload=similar_payload(),
    )

    assert response.status_code == 500, response.text
    assert response.headers["X-Error-Code"] == AiErrorCode.INVALID_GENERATED_STRUCTURE
    assert similar_quizzes(authz_api.session_factory, authz_api.a_course_id) == []


def test_an_explicit_difficulty_policy_is_enforced_against_the_output(
    authz_api, planned_course, monkeypatch
) -> None:
    response, _ = ask(
        authz_api,
        monkeypatch,
        body={"question_count": 1, "difficulty_policy": "hard"},
        payload=similar_payload(),
    )

    assert response.status_code == 500, response.text
    assert response.headers["X-Error-Code"] == AiErrorCode.INVALID_GENERATED_STRUCTURE


def test_an_unresolvable_citation_key_is_dropped_but_the_question_survives(
    authz_api, planned_course, monkeypatch
) -> None:
    """One invented key beside a real one is dropped; the grounding still holds."""
    response, _ = ask(
        authz_api,
        monkeypatch,
        payload=similar_payload(
            questions=[
                {
                    "source_number": 1,
                    "question": generated_question(citations=["S1", "S999"]),
                }
            ]
        ),
    )

    assert response.status_code == 200, response.text
    quiz_id = response.json()["data"]["quiz"]["quiz_id"]
    rows = stored_questions(authz_api.session_factory, quiz_id)
    assert len(rows[0].citations) == 1


# --------------------------------------------------------------- credits and reads


def test_it_costs_nothing_beyond_the_topic_s_unlock(
    authz_api, planned_course, monkeypatch
) -> None:
    before = balance_of(authz_api.session_factory, authz_api.user_a_id)

    first, _ = ask(authz_api, monkeypatch)
    second, _ = ask(authz_api, monkeypatch)

    assert first.json()["data"]["credits_charged"] == UNLOCK_PRICE
    assert second.json()["data"]["credits_charged"] == 0.0
    assert (
        balance_of(authz_api.session_factory, authz_api.user_a_id)
        == before - UNLOCK_PRICE
    )


def test_it_is_reopened_without_reaching_a_provider(
    authz_api, planned_course, monkeypatch
) -> None:
    ask(authz_api, monkeypatch)

    def forbidden(*args, **kwargs):
        raise AssertionError("reopening similar questions must not reach a provider")

    monkeypatch.setattr(exam_mode_route, "get_text_generation_provider", forbidden)
    response = authz_api.client.get(
        f"/api/courses/{authz_api.a_course_id}"
        "/exam-mode/topics/graph-traversal/similar-questions",
        headers=authz_api.authorization_a,
    )

    assert response.status_code == 200, response.text
    question = response.json()["data"]["questions"][0]
    assert question["correct_option_index"] is None, "a reopen is still an assessment"
    assert question["explanation"] == ""


def test_a_stranger_and_an_administrator_are_both_refused_a_write(
    authz_api, planned_course, monkeypatch
) -> None:
    provider = CountingProvider(similar_payload())
    monkeypatch.setattr(
        exam_mode_route, "get_text_generation_provider", lambda *a, **k: provider
    )

    for headers in (authz_api.authorization_b, authz_api.authorization_admin):
        response = authz_api.client.post(
            f"/api/courses/{authz_api.a_course_id}"
            "/exam-mode/topics/graph-traversal/similar-questions",
            json={"question_count": 1},
            headers=headers,
        )
        assert response.status_code == 404

    assert provider.calls == 0
    assert similar_quizzes(authz_api.session_factory, authz_api.a_course_id) == []
