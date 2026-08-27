"""Similar questions: modelled on real past questions, traceable to them."""

import pytest
from sqlalchemy import select

import routes.exam_mode as exam_mode_route
from backend.app.models import (
    OUTPUT_TYPE_EXAM_SIMILAR_QUESTIONS,
    ExamTopicUnlock,
    GeneratedOutput,
    PastExamQuestion,
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


def similar_payload(**overrides) -> dict:
    payload = {
        "questions": [
            {
                "source_number": 1,
                "question_text": "Explain depth-first search and give its complexity.",
                "reference_answer": "A correct answer states the stack invariant.",
                "what_changed": "The traversal order was changed from BFS to DFS.",
                "difficulty": "medium",
                "citations": ["S1"],
            }
        ],
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


def outputs_of(session_factory, course_id: int):
    with session_factory() as session:
        return session.scalars(
            select(GeneratedOutput).where(
                GeneratedOutput.course_id == course_id,
                GeneratedOutput.output_type == OUTPUT_TYPE_EXAM_SIMILAR_QUESTIONS,
            )
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


def ask(authz_api, monkeypatch, *, topic="graph-traversal", payload=None):
    provider = CountingProvider(payload if payload is not None else similar_payload())
    monkeypatch.setattr(
        exam_mode_route, "get_text_generation_provider", lambda *a, **k: provider
    )
    response = authz_api.client.post(
        f"/api/courses/{authz_api.a_course_id}"
        f"/exam-mode/topics/{topic}/similar-questions",
        json={},
        headers=authz_api.authorization_a,
    )
    return response, provider


def test_each_generated_question_names_the_original_it_was_modelled_on(
    authz_api, planned_course, monkeypatch
) -> None:
    expected = source_ids(authz_api.session_factory, planned_course["paper_id"])

    response, _ = ask(authz_api, monkeypatch)

    assert response.status_code == 200, response.text
    data = response.json()["data"]["similar_questions"]
    assert data["source_question_ids"] == expected
    pair = data["questions"][0]
    assert pair["source_question_id"] == expected[0]
    assert pair["source_question_text"].startswith("Explain breadth-first search")
    assert pair["question_text"].startswith("Explain depth-first search")
    assert pair["what_changed"]


def test_the_originals_reach_the_prompt_numbered_and_verbatim(
    authz_api, planned_course, monkeypatch
) -> None:
    _, provider = ask(authz_api, monkeypatch)

    assert "1. Explain breadth-first search" in provider.prompt
    assert "[10 marks]" in provider.prompt
    assert "Do NOT restate an original question" in provider.prompt


def test_the_model_is_never_shown_a_row_identifier(
    authz_api, planned_course, monkeypatch
) -> None:
    """It answers with the position it was shown, never with a database id."""
    expected = source_ids(authz_api.session_factory, planned_course["paper_id"])
    _, provider = ask(authz_api, monkeypatch)

    assert f"source_question_id: {expected[0]}" not in provider.prompt
    assert "source_number" in provider.prompt


def test_a_source_number_nobody_supplied_is_dropped_rather_than_guessed_at(
    authz_api, planned_course, monkeypatch
) -> None:
    response, _ = ask(
        authz_api,
        monkeypatch,
        payload=similar_payload(
            questions=[
                {
                    "source_number": 99,
                    "question_text": "A question about nothing in particular.",
                    "reference_answer": "Nothing.",
                    "citations": [],
                }
            ]
        ),
    )

    assert response.status_code == 200, response.text
    assert response.json()["data"]["similar_questions"]["questions"] == []


def test_an_unknown_citation_key_is_dropped_before_persistence(
    authz_api, planned_course, monkeypatch
) -> None:
    response, _ = ask(
        authz_api,
        monkeypatch,
        payload=similar_payload(
            questions=[
                {
                    "source_number": 1,
                    "question_text": "Explain depth-first search.",
                    "reference_answer": "The stack invariant.",
                    "citations": ["S999"],
                }
            ]
        ),
    )

    assert (
        response.json()["data"]["similar_questions"]["questions"][0]["citations"] == []
    )


def test_a_topic_this_course_never_examined_costs_nothing_to_ask_about(
    authz_api, planned_course, monkeypatch
) -> None:
    response, provider = ask(authz_api, monkeypatch, topic="dynamic-programming")

    assert response.status_code == 409
    assert response.headers["X-Error-Code"] == AiErrorCode.EXAM_ANALYSIS_REQUIRED
    assert provider.calls == 0
    assert unlocks(authz_api.session_factory, authz_api.a_course_id) == []


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
    assert response.json()["data"]["questions"][0]["source_question_id"]


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
            json={},
            headers=headers,
        )
        assert response.status_code == 404

    assert provider.calls == 0
    assert outputs_of(authz_api.session_factory, authz_api.a_course_id) == []
