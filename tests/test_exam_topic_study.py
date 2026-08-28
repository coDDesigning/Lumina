"""Per-topic study guides and summaries: what they cost, and what they may read."""

import pytest
from sqlalchemy import select

import routes.exam_mode as exam_mode_route
from backend.app.models import (
    OUTPUT_TYPE_EXAM_TOPIC_GUIDE,
    OUTPUT_TYPE_EXAM_TOPIC_SUMMARY,
    AiUsageLog,
    ExamTopicUnlock,
    GeneratedOutput,
    User,
)
from conftest import assert_balance_is_derivable, set_balance
from services.credits import GENERATION_CREDIT_COSTS
from services.text_generation import GenerationMetadata, TextGenerationError
from tests.test_exam_mode import (  # noqa: F401 - fixtures
    CountingProvider,
    add_material,
    analysis_payload,
    create_plan,
    exam_course,
    run_analysis,
    set_exam_date,
    set_topics,
)
from utils.ai_errors import AiErrorCode

UNLOCK_PRICE = GENERATION_CREDIT_COSTS["exam_topic_unlock"]

STUB_METADATA = GenerationMetadata(provider="ollama", model="qwen3:8b", latency_ms=5)


def guide_payload(**overrides) -> dict:
    payload = {
        "title": "Graph Traversal",
        "overview": {
            "text": "Traversal visits every reachable vertex.",
            "citations": ["S1"],
        },
        "sections": [
            {
                "heading": "Breadth-first search",
                "body": {"text": "BFS explores by distance.", "citations": ["S1"]},
                "key_points": [{"text": "Uses a queue.", "citations": ["S1"]}],
            }
        ],
        "key_terms": [
            {
                "term": "Frontier",
                "definition": "The vertices to visit next.",
                "citations": ["S1"],
            }
        ],
        "common_pitfalls": [
            {
                "mistake": "Marking on dequeue.",
                "correction": "Mark on enqueue.",
                "citations": ["S1"],
            }
        ],
        "what_to_be_able_to_do": [
            {"text": "Trace BFS on a graph.", "citations": ["S1"]}
        ],
        "coverage": {"status": "Partial", "estimated_completeness": 70},
        "confidence_notes": "The material covers BFS more than DFS.",
    }
    payload.update(overrides)
    return payload


def summary_payload(**overrides) -> dict:
    payload = {
        "title": "Graph Traversal",
        "summary": {
            "text": "Traversal visits every reachable vertex.",
            "citations": ["S1"],
        },
        "key_points": [{"text": "BFS uses a queue.", "citations": ["S1"]}],
        "coverage": {"status": "Partial", "estimated_completeness": 60},
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
    """Owner A's course with a current plan covering two topics."""
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


def ask(
    authz_api,
    kind: str,
    monkeypatch,
    *,
    topic="graph-traversal",
    payload=None,
    json=None,
):
    provider = CountingProvider(
        payload
        if payload is not None
        else (guide_payload() if kind == "guide" else summary_payload())
    )
    monkeypatch.setattr(
        exam_mode_route, "get_text_generation_provider", lambda *a, **k: provider
    )
    response = authz_api.client.post(
        f"/api/courses/{authz_api.a_course_id}/exam-mode/topics/{topic}/{kind}",
        json=json if json is not None else {},
        headers=authz_api.authorization_a,
    )
    return response, provider


# --------------------------------------------------------------- generation


def test_a_planned_topic_yields_a_cited_guide(
    authz_api, planned_course, monkeypatch
) -> None:
    response, provider = ask(authz_api, "guide", monkeypatch)

    assert response.status_code == 200, response.text
    assert provider.calls == 1
    guide = response.json()["data"]["guide"]
    assert guide["topic_key"] == "graph-traversal"
    assert guide["display_label"] == "Graph Traversal"
    assert guide["plan_output_id"] == planned_course["plan_id"]
    assert guide["sections"][0]["heading"] == "Breadth-first search"
    assert guide["overview"]["citations"]
    assert guide["key_terms"][0]["citations"]


def test_the_summary_is_its_own_artifact_of_the_same_topic(
    authz_api, planned_course, monkeypatch
) -> None:
    response, _ = ask(authz_api, "summary", monkeypatch)

    assert response.status_code == 200, response.text
    summary = response.json()["data"]["summary"]
    assert summary["topic_key"] == "graph-traversal"
    assert summary["key_points"][0]["citations"]
    assert "sections" not in summary


def test_the_prompt_carries_the_topic_and_the_material_last(
    authz_api, planned_course, monkeypatch
) -> None:
    _, provider = ask(authz_api, "guide", monkeypatch)

    prompt = provider.prompt
    assert "Graph Traversal" in prompt
    assert prompt.index("COURSE MATERIAL") > prompt.index("SOURCE CITATIONS")
    assert prompt.index("COURSE MATERIAL") > prompt.index("THE TOPIC")
    assert "{{" not in prompt


def test_an_unknown_citation_key_is_dropped_before_persistence(
    authz_api, planned_course, monkeypatch
) -> None:
    response, _ = ask(
        authz_api,
        "guide",
        monkeypatch,
        payload=guide_payload(
            overview={"text": "Traversal visits every vertex.", "citations": ["S999"]}
        ),
    )

    assert response.json()["data"]["guide"]["overview"]["citations"] == []


# --------------------------------------------------------------- pricing


def test_the_first_artifact_charges_the_topic_and_the_second_does_not(
    authz_api, planned_course, monkeypatch
) -> None:
    before = balance_of(authz_api.session_factory, authz_api.user_a_id)

    first, _ = ask(authz_api, "guide", monkeypatch)
    second, _ = ask(authz_api, "summary", monkeypatch)

    assert first.json()["data"]["credits_charged"] == UNLOCK_PRICE
    assert second.json()["data"]["credits_charged"] == 0.0
    assert (
        balance_of(authz_api.session_factory, authz_api.user_a_id)
        == before - UNLOCK_PRICE
    )
    assert len(unlocks(authz_api.session_factory, authz_api.a_course_id)) == 1


def test_a_second_topic_is_charged_again(
    authz_api, planned_course, monkeypatch
) -> None:
    before = balance_of(authz_api.session_factory, authz_api.user_a_id)

    ask(authz_api, "guide", monkeypatch)
    ask(authz_api, "guide", monkeypatch, topic="dynamic-programming")

    assert (
        balance_of(authz_api.session_factory, authz_api.user_a_id)
        == before - 2 * UNLOCK_PRICE
    )
    assert {
        row.topic_key
        for row in unlocks(authz_api.session_factory, authz_api.a_course_id)
    } == {"graph-traversal", "dynamic-programming"}


def test_regenerating_the_plan_does_not_charge_an_unlocked_topic_again(
    authz_api, planned_course, monkeypatch
) -> None:
    """An unlock outlives the plan that first named the topic."""
    ask(authz_api, "guide", monkeypatch)
    before = balance_of(authz_api.session_factory, authz_api.user_a_id)

    created = create_plan(
        authz_api,
        {
            "analysis_output_id": planned_course["analysis_id"],
            "selected_topic_keys": ["graph-traversal"],
        },
    )
    assert created.status_code == 200, created.text
    response, _ = ask(authz_api, "guide", monkeypatch)

    assert response.json()["data"]["credits_charged"] == 0.0
    assert balance_of(authz_api.session_factory, authz_api.user_a_id) == before


def test_an_empty_balance_is_refused_before_the_provider_is_reached(
    authz_api, planned_course, monkeypatch
) -> None:
    set_balance(authz_api.session_factory, authz_api.user_a_id, 0.0)

    response, provider = ask(authz_api, "guide", monkeypatch)

    assert response.status_code == 402
    assert response.headers["X-Error-Code"] == AiErrorCode.INSUFFICIENT_CREDITS
    assert provider.calls == 0
    assert unlocks(authz_api.session_factory, authz_api.a_course_id) == []


def test_a_provider_failure_releases_the_unlock_it_paid_for(
    authz_api, planned_course, monkeypatch
) -> None:
    """A failed first artifact must leave the student neither charged nor unlocked."""
    before = balance_of(authz_api.session_factory, authz_api.user_a_id)
    provider = CountingProvider(error=TextGenerationError("the provider is down"))
    monkeypatch.setattr(
        exam_mode_route, "get_text_generation_provider", lambda *a, **k: provider
    )

    response = authz_api.client.post(
        f"/api/courses/{authz_api.a_course_id}/exam-mode/topics/graph-traversal/guide",
        json={},
        headers=authz_api.authorization_a,
    )

    assert response.status_code >= 500
    assert provider.calls == 1
    assert balance_of(authz_api.session_factory, authz_api.user_a_id) == before
    assert unlocks(authz_api.session_factory, authz_api.a_course_id) == []
    with authz_api.session_factory() as session:
        assert_balance_is_derivable(session, authz_api.user_a_id)


def test_an_invalid_structure_releases_the_unlock_too(
    authz_api, planned_course, monkeypatch
) -> None:
    before = balance_of(authz_api.session_factory, authz_api.user_a_id)

    response, _ = ask(authz_api, "guide", monkeypatch, payload={"title": "x"})

    assert response.status_code == 500
    assert response.headers["X-Error-Code"] == AiErrorCode.INVALID_GENERATED_STRUCTURE
    assert balance_of(authz_api.session_factory, authz_api.user_a_id) == before
    assert unlocks(authz_api.session_factory, authz_api.a_course_id) == []


def test_a_failure_while_persisting_releases_the_unlock_too(
    authz_api, planned_course, monkeypatch
) -> None:
    """The window between a successful generation and a written row."""
    before = balance_of(authz_api.session_factory, authz_api.user_a_id)

    def exploding_record(*args, **kwargs):
        raise RuntimeError("the database went away")

    monkeypatch.setattr(
        "services.exam_artifacts.GeneratedOutputService.record", exploding_record
    )
    response, _ = ask(authz_api, "guide", monkeypatch)

    assert response.status_code >= 500
    assert balance_of(authz_api.session_factory, authz_api.user_a_id) == before
    assert unlocks(authz_api.session_factory, authz_api.a_course_id) == []
    with authz_api.session_factory() as session:
        assert_balance_is_derivable(session, authz_api.user_a_id)


def test_a_released_unlock_leaves_the_retry_priced_as_a_first_purchase(
    authz_api, planned_course, monkeypatch
) -> None:
    before = balance_of(authz_api.session_factory, authz_api.user_a_id)
    failing = CountingProvider(error=TextGenerationError("down"))
    monkeypatch.setattr(
        exam_mode_route, "get_text_generation_provider", lambda *a, **k: failing
    )
    authz_api.client.post(
        f"/api/courses/{authz_api.a_course_id}/exam-mode/topics/graph-traversal/guide",
        json={},
        headers=authz_api.authorization_a,
    )

    retry, _ = ask(authz_api, "guide", monkeypatch)

    assert retry.status_code == 200, retry.text
    assert retry.json()["data"]["credits_charged"] == UNLOCK_PRICE
    assert (
        balance_of(authz_api.session_factory, authz_api.user_a_id)
        == before - UNLOCK_PRICE
    )


# --------------------------------------------------------------- refusals


def test_a_course_with_no_plan_is_told_to_make_one(
    authz_api,
    exam_course,  # noqa: F811
    monkeypatch,
) -> None:
    response, provider = ask(authz_api, "guide", monkeypatch)

    assert response.status_code == 409
    assert response.headers["X-Error-Code"] == AiErrorCode.EXAM_PLAN_REQUIRED
    assert provider.calls == 0
    assert unlocks(authz_api.session_factory, authz_api.a_course_id) == []


def test_a_topic_the_plan_never_ranked_costs_nothing(
    authz_api, planned_course, monkeypatch
) -> None:
    response, provider = ask(authz_api, "guide", monkeypatch, topic="quantum-mechanics")

    assert response.status_code == 409
    assert response.headers["X-Error-Code"] == AiErrorCode.EXAM_TOPIC_NOT_DISCOVERED
    assert provider.calls == 0
    assert unlocks(authz_api.session_factory, authz_api.a_course_id) == []


# --------------------------------------------------------------- reopening


def test_a_guide_is_reopened_without_reaching_a_provider(
    authz_api, planned_course, monkeypatch
) -> None:
    created, _ = ask(authz_api, "guide", monkeypatch)
    assert created.status_code == 200

    def forbidden(*args, **kwargs):
        raise AssertionError("reopening a topic guide must never reach a provider")

    monkeypatch.setattr(exam_mode_route, "get_text_generation_provider", forbidden)
    response = authz_api.client.get(
        f"/api/courses/{authz_api.a_course_id}/exam-mode/topics/graph-traversal/guide",
        headers=authz_api.authorization_a,
    )

    assert response.status_code == 200, response.text
    assert response.json()["data"]["title"] == "Graph Traversal"


def test_reopening_serves_the_newest_artifact_for_that_topic(
    authz_api, planned_course, monkeypatch
) -> None:
    ask(authz_api, "guide", monkeypatch)
    ask(
        authz_api, "guide", monkeypatch, payload=guide_payload(title="Traversal, again")
    )

    response = authz_api.client.get(
        f"/api/courses/{authz_api.a_course_id}/exam-mode/topics/graph-traversal/guide",
        headers=authz_api.authorization_a,
    )

    assert response.json()["data"]["title"] == "Traversal, again"
    assert (
        len(
            outputs_of(
                authz_api.session_factory,
                authz_api.a_course_id,
                OUTPUT_TYPE_EXAM_TOPIC_GUIDE,
            )
        )
        == 2
    )


def test_one_topic_never_serves_another_topic_s_guide(
    authz_api, planned_course, monkeypatch
) -> None:
    ask(authz_api, "guide", monkeypatch)

    response = authz_api.client.get(
        f"/api/courses/{authz_api.a_course_id}"
        "/exam-mode/topics/dynamic-programming/guide",
        headers=authz_api.authorization_a,
    )

    assert response.status_code == 404


def test_a_guide_and_a_summary_do_not_collide(
    authz_api, planned_course, monkeypatch
) -> None:
    ask(authz_api, "guide", monkeypatch)
    ask(authz_api, "summary", monkeypatch)

    assert (
        len(
            outputs_of(
                authz_api.session_factory,
                authz_api.a_course_id,
                OUTPUT_TYPE_EXAM_TOPIC_GUIDE,
            )
        )
        == 1
    )
    assert (
        len(
            outputs_of(
                authz_api.session_factory,
                authz_api.a_course_id,
                OUTPUT_TYPE_EXAM_TOPIC_SUMMARY,
            )
        )
        == 1
    )


# --------------------------------------------------------------- ownership


def test_a_stranger_and_an_administrator_are_both_refused_a_write(
    authz_api, planned_course, monkeypatch
) -> None:
    before = balance_of(authz_api.session_factory, authz_api.user_a_id)
    provider = CountingProvider(guide_payload())
    monkeypatch.setattr(
        exam_mode_route, "get_text_generation_provider", lambda *a, **k: provider
    )

    for headers in (authz_api.authorization_b, authz_api.authorization_admin):
        response = authz_api.client.post(
            f"/api/courses/{authz_api.a_course_id}"
            "/exam-mode/topics/graph-traversal/guide",
            json={},
            headers=headers,
        )
        assert response.status_code == 404

    assert provider.calls == 0
    assert unlocks(authz_api.session_factory, authz_api.a_course_id) == []
    assert balance_of(authz_api.session_factory, authz_api.user_a_id) == before
    assert (
        outputs_of(
            authz_api.session_factory,
            authz_api.a_course_id,
            OUTPUT_TYPE_EXAM_TOPIC_GUIDE,
        )
        == []
    )


def test_an_administrator_may_still_read_a_generated_guide(
    authz_api, planned_course, monkeypatch
) -> None:
    ask(authz_api, "guide", monkeypatch)

    response = authz_api.client.get(
        f"/api/courses/{authz_api.a_course_id}/exam-mode/topics/graph-traversal/guide",
        headers=authz_api.authorization_admin,
    )

    assert response.status_code == 200, response.text


# --------------------------------------------------------------- logging


def test_the_usage_log_records_the_feature_without_any_of_its_content(
    authz_api, planned_course, monkeypatch
) -> None:
    ask(authz_api, "guide", monkeypatch)

    with authz_api.session_factory() as session:
        entry = session.scalars(
            select(AiUsageLog).where(AiUsageLog.generation_type == "exam_topic_guide")
        ).one()

    assert entry.success is True
    assert entry.course_id == authz_api.a_course_id
    assert entry.user_id == authz_api.user_a_id

    recorded = " ".join(
        str(value)
        for value in (
            entry.generation_type,
            entry.provider,
            entry.model,
            entry.error_category,
        )
        if value is not None
    )
    for secret in ("Breadth-first search", "Graph traversal covers BFS", "Frontier"):
        assert secret not in recorded


def test_the_unlock_price_is_served_rather_than_left_for_a_client_to_guess(
    authz_api,
) -> None:
    policy = authz_api.client.get(
        "/api/users/me/credits", headers=authz_api.authorization_a
    ).json()["data"]

    assert policy["generation_costs"]["exam_topic_unlock"] == UNLOCK_PRICE
