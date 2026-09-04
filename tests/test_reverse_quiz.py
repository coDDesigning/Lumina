import json

import pytest
from sqlalchemy import select

from backend.app.models import GeneratedOutput
from schemas.reverse_quiz import ConceptStatus
from services.text_generation import GenerationMetadata
from tests.generation_fixtures import seed_ready_material


class _EvalStub:
    """Stands in for the text-generation provider in reverse-quiz evaluation.

    Exposing only ``generate_json`` also exercises the no-metadata branch of
    ``ReverseQuizService.generate``.
    """

    name = "stub-evaluator"

    def __init__(self, payload: dict) -> None:
        self._payload = payload
        self.prompts: list[str] = []

    def generate_json(self, prompt: str) -> dict:
        self.prompts.append(prompt)
        return self._payload


def _install_provider(monkeypatch: pytest.MonkeyPatch, stub: _EvalStub) -> None:
    monkeypatch.setattr(
        "routes.reverse_quiz.get_text_generation_provider",
        lambda **kwargs: stub,
    )


def test_reverse_quiz_endpoint_returns_grounded_evaluation(
    upload_api, retrieval_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    with upload_api.session_factory() as session:
        seed_ready_material(
            session,
            upload_api.course_id,
            ["Plants build sugars from sunlight and CO2 in photosynthesis."],
            file_hash="b" * 64,
            retrieval_env=retrieval_env,
        )

    stub = _EvalStub(
        {
            "feedback": "You have the energy source backwards: plants do not eat soil.",
            "misconceptions": [
                {
                    "concept": "Plant nutrition",
                    "status": ConceptStatus.CONTRADICTED.value,
                    "detail": "The material says food is synthesised from light, not soil.",
                }
            ],
        }
    )
    _install_provider(monkeypatch, stub)

    response = upload_api.client.post(
        f"/api/courses/{upload_api.course_id}/reverse-quiz",
        json={
            "topic": "Photosynthesis",
            "explanation": "Plants take their food from the soil.",
        },
        headers=upload_api.authorization,
    )

    assert response.status_code == 201, response.text
    data = response.json()["data"]
    assert data["id"] > 0
    assert data["course_id"] == upload_api.course_id
    assert data["topic"] == "Photosynthesis"
    assert data["feedback"]
    assert len(data["misconceptions"]) == 1
    assert data["misconceptions"][0]["concept"] == "Plant nutrition"
    assert data["misconceptions"][0]["status"] == ConceptStatus.CONTRADICTED.value
    assert stub.prompts and "Plants take their food from the soil" in stub.prompts[0]

    with upload_api.session_factory() as session:
        stored = session.scalars(
            select(GeneratedOutput).where(
                GeneratedOutput.course_id == upload_api.course_id,
                GeneratedOutput.output_type == "reverse_quiz",
            )
        ).all()
    assert len(stored) == 1
    assert json.loads(stored[0].content)["topic"] == "Photosynthesis"


class _MeteredEvalStub(_EvalStub):
    """An evaluator that reports which vendor and model actually answered."""

    def __init__(self, payload: dict) -> None:
        super().__init__(payload)
        self.metadata = GenerationMetadata(
            provider="gemini", model="gemini-3.6-flash", latency_ms=5
        )

    def generate_json_with_metadata(self, prompt: str):
        self.prompts.append(prompt)
        return self._payload, self.metadata


def test_reverse_quiz_records_attribution_the_same_way_every_feature_does(
    upload_api, retrieval_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Attribution is ``provider:model`` everywhere or it cannot be compared.

    Reverse quiz used to store the bare model name while every other feature
    stored ``provider:model``, so one course's history disagreed with itself
    about which vendor wrote what.
    """
    with upload_api.session_factory() as session:
        seed_ready_material(
            session,
            upload_api.course_id,
            ["Plants build sugars from sunlight and CO2 in photosynthesis."],
            file_hash="c" * 64,
            retrieval_env=retrieval_env,
        )
    stub = _MeteredEvalStub({"feedback": "Close.", "misconceptions": []})
    _install_provider(monkeypatch, stub)

    response = upload_api.client.post(
        f"/api/courses/{upload_api.course_id}/reverse-quiz",
        json={
            "topic": "Photosynthesis",
            "question": "How do plants feed themselves?",
            "explanation": "Plants take their food from the soil.",
        },
        headers=upload_api.authorization,
    )

    assert response.status_code == 201, response.text
    with upload_api.session_factory() as session:
        stored = session.scalars(
            select(GeneratedOutput).where(
                GeneratedOutput.output_type == "reverse_quiz",
            )
        ).one()

    assert stored.model_used == "gemini:gemini-3.6-flash"


def test_reverse_quiz_endpoint_evaluates_without_indexed_material(
    upload_api, retrieval_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    stub = _EvalStub(
        {
            "feedback": "That is an accurate account of the concept.",
            "misconceptions": [],
        }
    )
    _install_provider(monkeypatch, stub)

    response = upload_api.client.post(
        f"/api/courses/{upload_api.course_id}/reverse-quiz",
        json={
            "topic": "Newton's second law",
            "explanation": "Net force equals mass times acceleration.",
        },
        headers=upload_api.authorization,
    )

    assert response.status_code == 201, response.text
    assert response.json()["data"]["misconceptions"] == []
    assert stub.prompts


def test_reverse_quiz_endpoint_rejects_blank_explanation(upload_api) -> None:
    response = upload_api.client.post(
        f"/api/courses/{upload_api.course_id}/reverse-quiz",
        json={"topic": "Photosynthesis", "explanation": ""},
        headers=upload_api.authorization,
    )
    assert response.status_code == 422, response.text


def test_reverse_quiz_endpoint_hides_unknown_course_as_404(
    upload_api, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_provider(monkeypatch, _EvalStub({"feedback": "x", "misconceptions": []}))

    response = upload_api.client.post(
        "/api/courses/999999/reverse-quiz",
        json={"topic": "Anything", "explanation": "Some explanation."},
        headers=upload_api.authorization,
    )
    assert response.status_code == 404, response.text


def test_reverse_quiz_history_lists_saved_sessions(
    upload_api, retrieval_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    stub = _EvalStub(
        {
            "feedback": "Partly right.",
            "misconceptions": [
                {
                    "concept": "Energy source",
                    "status": ConceptStatus.ABSENT.value,
                    "detail": "You did not say where the energy comes from.",
                }
            ],
        }
    )
    _install_provider(monkeypatch, stub)

    created = upload_api.client.post(
        f"/api/courses/{upload_api.course_id}/reverse-quiz",
        json={"topic": "Cell respiration", "explanation": "Cells break down glucose."},
        headers=upload_api.authorization,
    )
    assert created.status_code == 201, created.text

    listed = upload_api.client.get(
        f"/api/courses/{upload_api.course_id}/reverse-quizzes",
        headers=upload_api.authorization,
    )
    assert listed.status_code == 200, listed.text
    history = listed.json()["data"]
    assert len(history) == 1
    assert history[0]["topic"] == "Cell respiration"
    assert history[0]["misconceptions"][0]["status"] == ConceptStatus.ABSENT.value
    assert history[0]["id"] == created.json()["data"]["id"]


def test_reverse_quiz_adds_misconceptions_to_weak_topics(upload_api) -> None:
    # 1. Create a GeneratedOutput entry with misconceptions
    content = {
        "id": 1,
        "course_id": upload_api.course_id,
        "topic": "Photosynthesis",
        "explanation": "Plants get food from soil",
        "feedback": "Plants make food via photosynthesis.",
        "misconceptions": [
            {
                "concept": "Plant nutrition",
                "status": ConceptStatus.CONTRADICTED.value,
                "detail": "Plants make food via photosynthesis",
            }
        ],
    }

    with upload_api.session_factory() as session:
        output = GeneratedOutput(
            course_id=upload_api.course_id,
            user_id=upload_api.user_id,
            output_type="reverse_quiz",
            content=json.dumps(content),
        )
        session.add(output)
        session.commit()

    # 2. Query the progress endpoint
    response = upload_api.client.get(
        f"/api/courses/{upload_api.course_id}/progress",
        headers=upload_api.authorization,
    )

    assert response.status_code == 200, response.text
    payload = response.json()["data"]

    # 3. Verify that the weak topics include the reverse quiz topic
    assert "Photosynthesis (Reverse Quiz)" in payload["weak_topics"]


def test_reverse_quiz_endpoint_accepts_a_picked_question(
    upload_api, retrieval_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    with upload_api.session_factory() as session:
        seed_ready_material(
            session,
            upload_api.course_id,
            ["Photosynthesis converts light energy into glucose inside chloroplasts."],
            file_hash="c" * 64,
            retrieval_env=retrieval_env,
        )

    stub = _EvalStub({"feedback": "Close, but incomplete.", "misconceptions": []})
    _install_provider(monkeypatch, stub)

    question = "Explain how chloroplasts turn light into chemical energy."
    response = upload_api.client.post(
        f"/api/courses/{upload_api.course_id}/reverse-quiz",
        json={
            "topic": "Photosynthesis",
            "question": question,
            "explanation": "Chloroplasts use light to make sugar.",
        },
        headers=upload_api.authorization,
    )

    assert response.status_code == 201, response.text
    data = response.json()["data"]
    assert data["question"] == question
    assert question in stub.prompts[0]


def test_reverse_quiz_questions_endpoint_drafts_from_sources(
    upload_api, retrieval_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    with upload_api.session_factory() as session:
        seed_ready_material(
            session,
            upload_api.course_id,
            [
                "Insertion sort builds a sorted prefix by shifting larger elements right.",
                "Its worst-case running time is quadratic in the number of elements.",
            ],
            file_hash="d" * 64,
            retrieval_env=retrieval_env,
        )

    stub = _EvalStub(
        {
            "questions": [
                {
                    "topic": "Insertion sort mechanism",
                    "question": "Explain in your own words how insertion sort places each new element.",
                },
                {
                    "topic": "Time complexity",
                    "question": "Describe why insertion sort is quadratic in the worst case.",
                },
                # duplicate is dropped
                {
                    "topic": "Time complexity",
                    "question": "Describe why insertion sort is quadratic in the worst case.",
                },
            ]
        }
    )
    _install_provider(monkeypatch, stub)

    response = upload_api.client.post(
        f"/api/courses/{upload_api.course_id}/reverse-quiz/questions",
        headers=upload_api.authorization,
    )

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["course_id"] == upload_api.course_id
    assert len(data["questions"]) == 2
    assert data["questions"][0]["topic"] == "Insertion sort mechanism"
    assert all(q["question"] for q in data["questions"])


def test_reverse_quiz_questions_endpoint_empty_without_material(
    upload_api, retrieval_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    stub = _EvalStub({"questions": []})
    _install_provider(monkeypatch, stub)

    response = upload_api.client.post(
        f"/api/courses/{upload_api.course_id}/reverse-quiz/questions",
        headers=upload_api.authorization,
    )

    assert response.status_code == 200, response.text
    assert response.json()["data"]["questions"] == []
    # no indexed material -> the provider is never asked
    assert stub.prompts == []


def test_reverse_quiz_omits_mastered_topics_from_weak_topics(upload_api) -> None:
    content = {
        "id": 2,
        "course_id": upload_api.course_id,
        "topic": "Cell Biology",
        "explanation": "Cells are the basic unit of life.",
        "feedback": "Great job.",
        "misconceptions": [],
    }

    with upload_api.session_factory() as session:
        output = GeneratedOutput(
            course_id=upload_api.course_id,
            user_id=upload_api.user_id,
            output_type="reverse_quiz",
            content=json.dumps(content),
        )
        session.add(output)
        session.commit()

    response = upload_api.client.get(
        f"/api/courses/{upload_api.course_id}/progress",
        headers=upload_api.authorization,
    )

    assert response.status_code == 200, response.text
    payload = response.json()["data"]

    assert "Cell Biology (Reverse Quiz)" not in payload["weak_topics"]
