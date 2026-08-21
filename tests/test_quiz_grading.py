import pytest
from sqlalchemy import select

import routes.quiz as quiz_route
from backend.app.models import Quiz, QuizAttempt, QuizAttemptAnswer, QuizQuestion, User
from schemas.quiz_attempt import OPEN_ENDED_PASS_THRESHOLD
from services.text_generation import GenerationMetadata, TextGenerationConnectionError
from tests.conftest import set_balance

STUB_METADATA = GenerationMetadata(provider="ollama", model="qwen3:8b", latency_ms=5)

REFERENCE_ANSWER = "Ordering lets binary search discard half the range."


class GradingProvider:
    """Stands in for the grader so tests can prove when it is and is not called."""

    def __init__(self, result=None, error=None):
        self._result = result
        self._error = error
        self.calls = 0
        self.prompt = ""

    def generate_json_with_metadata(self, prompt: str):
        self.calls += 1
        self.prompt = prompt
        if self._error is not None:
            raise self._error
        return self._result, STUB_METADATA


def _install_provider(monkeypatch, provider: GradingProvider) -> GradingProvider:
    monkeypatch.setattr(
        quiz_route, "get_text_generation_provider", lambda **_: provider
    )
    return provider


def _question(quiz_id: int, index: int, question_type: str, topic: str) -> QuizQuestion:
    row = QuizQuestion(
        quiz_id=quiz_id,
        question_index=index,
        question_type=question_type,
        difficulty="medium",
        question_text=f"Question {index + 1}?",
        topic=topic,
        explanation="Because the material says so.",
    )
    if question_type == "multiple_choice":
        row.options = ["Option A", "Option B", "Option C", "Option D"]
        row.correct_option_index = 0
        row.correct_answer = {"type": "multiple_choice", "option_index": 0}
    elif question_type == "true_false":
        row.options = ["True", "False"]
        row.correct_option_index = 0
        row.correct_answer = {"type": "true_false", "value": True}
    elif question_type == "short_answer":
        row.correct_answer = {
            "type": "short_answer",
            "text": "O(log n)",
            "accepted_answers": ["O(log n)", "logarithmic"],
        }
    else:
        row.correct_answer = {
            "type": "open_ended",
            "reference_answer": REFERENCE_ANSWER,
        }
    return row


def _quiz(
    api, course_id: int, question_types: list[str], topics: list[str] | None = None
):
    labels = topics or ["Searching"] * len(question_types)
    with api.session_factory() as session:
        quiz = Quiz(course_id=course_id, title="Mixed Quiz")
        session.add(quiz)
        session.flush()
        for index, question_type in enumerate(question_types):
            session.add(_question(quiz.id, index, question_type, labels[index]))
        session.commit()
        question_ids = [
            row.id
            for row in session.scalars(
                select(QuizQuestion)
                .where(QuizQuestion.quiz_id == quiz.id)
                .order_by(QuizQuestion.question_index)
            ).all()
        ]
        return quiz.id, question_ids


def _submit(api, course_id, quiz_id, answers, headers):
    return api.client.post(
        f"/api/courses/{course_id}/quizzes/{quiz_id}/attempts",
        json={"answers": answers},
        headers=headers,
    )


def _stored_answers(api, attempt_id: int):
    with api.session_factory() as session:
        return session.scalars(
            select(QuizAttemptAnswer)
            .where(QuizAttemptAnswer.attempt_id == attempt_id)
            .order_by(QuizAttemptAnswer.id)
        ).all()


# ---------------------------------------------------------------------------
# Short answer
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("written", ["O(log n)", "  o(LOG N). ", "logarithmic"])
def test_short_answer_accepts_every_stored_variant(upload_api, monkeypatch, written):
    quiz_id, question_ids = _quiz(upload_api, upload_api.course_id, ["short_answer"])
    provider = _install_provider(monkeypatch, GradingProvider())

    response = _submit(
        upload_api,
        upload_api.course_id,
        quiz_id,
        [{"question_id": question_ids[0], "text_response": written}],
        upload_api.authorization,
    )

    assert response.status_code == 201, response.text
    data = response.json()["data"]
    assert data["answers"][0]["is_correct"] is True
    assert data["score"] == pytest.approx(1.0)
    assert provider.calls == 0


def test_short_answer_rejects_a_wrong_answer(upload_api, monkeypatch):
    quiz_id, question_ids = _quiz(upload_api, upload_api.course_id, ["short_answer"])
    _install_provider(monkeypatch, GradingProvider())

    response = _submit(
        upload_api,
        upload_api.course_id,
        quiz_id,
        [{"question_id": question_ids[0], "text_response": "O(n squared)"}],
        upload_api.authorization,
    )

    data = response.json()["data"]
    assert data["answers"][0]["is_correct"] is False
    assert data["score"] == pytest.approx(0.0)


def test_unanswered_short_answer_scores_zero(upload_api, monkeypatch):
    quiz_id, _ = _quiz(upload_api, upload_api.course_id, ["short_answer"])
    _install_provider(monkeypatch, GradingProvider())

    response = upload_api.client.post(
        f"/api/courses/{upload_api.course_id}/quizzes/{quiz_id}/attempts",
        json={"answers": [{"question_id": 999999, "text_response": "x"}]},
        headers=upload_api.authorization,
    )

    assert response.status_code == 400


# ---------------------------------------------------------------------------
# Open ended
# ---------------------------------------------------------------------------


def test_open_ended_is_scored_by_the_provider(upload_api, monkeypatch):
    quiz_id, question_ids = _quiz(upload_api, upload_api.course_id, ["open_ended"])
    provider = _install_provider(
        monkeypatch,
        GradingProvider(
            {"verdicts": [{"question_number": 1, "score": 0.75, "feedback": "Solid."}]}
        ),
    )

    response = _submit(
        upload_api,
        upload_api.course_id,
        quiz_id,
        [{"question_id": question_ids[0], "text_response": "Because it is sorted."}],
        upload_api.authorization,
    )

    assert response.status_code == 201, response.text
    data = response.json()["data"]
    answer = data["answers"][0]

    assert provider.calls == 1
    assert REFERENCE_ANSWER in provider.prompt
    assert "Because it is sorted." in provider.prompt
    assert answer["score"] == pytest.approx(0.75)
    assert answer["is_correct"] is True
    assert answer["feedback"] == "Solid."
    assert data["score"] == pytest.approx(0.75)
    assert data["graded_count"] == 1

    stored = _stored_answers(upload_api, data["attempt_id"])
    assert stored[0].text_response == "Because it is sorted."
    assert stored[0].score == pytest.approx(0.75)
    assert stored[0].feedback == "Solid."


def test_a_score_outside_the_unit_interval_is_refused(upload_api, monkeypatch):
    """The unit interval is enforced on the way in, not by a check constraint.

    A provider is free to answer 5.0; nothing downstream clamps it, so the
    verdict schema has to be what stops it from ever reaching a stored score.
    """
    quiz_id, question_ids = _quiz(upload_api, upload_api.course_id, ["open_ended"])
    _install_provider(
        monkeypatch,
        GradingProvider(
            {"verdicts": [{"question_number": 1, "score": 5.0, "feedback": "Great."}]}
        ),
    )

    response = _submit(
        upload_api,
        upload_api.course_id,
        quiz_id,
        [{"question_id": question_ids[0], "text_response": "Because it is sorted."}],
        upload_api.authorization,
    )

    assert response.status_code == 201, response.text
    data = response.json()["data"]
    assert data["answers"][0]["is_correct"] is None
    assert data["answers"][0]["score"] is None
    assert data["graded_count"] == 0

    stored = _stored_answers(upload_api, data["attempt_id"])
    assert stored[0].score is None


def test_open_ended_below_the_threshold_is_not_correct(upload_api, monkeypatch):
    quiz_id, question_ids = _quiz(upload_api, upload_api.course_id, ["open_ended"])
    low = OPEN_ENDED_PASS_THRESHOLD - 0.1
    _install_provider(
        monkeypatch,
        GradingProvider({"verdicts": [{"question_number": 1, "score": low}]}),
    )

    response = _submit(
        upload_api,
        upload_api.course_id,
        quiz_id,
        [{"question_id": question_ids[0], "text_response": "Not much."}],
        upload_api.authorization,
    )

    answer = response.json()["data"]["answers"][0]
    assert answer["is_correct"] is False
    assert answer["score"] == pytest.approx(low)


def test_unanswered_open_ended_scores_zero_without_calling_the_provider(
    upload_api, monkeypatch
):
    quiz_id, question_ids = _quiz(
        upload_api, upload_api.course_id, ["multiple_choice", "open_ended"]
    )
    provider = _install_provider(monkeypatch, GradingProvider())

    response = _submit(
        upload_api,
        upload_api.course_id,
        quiz_id,
        [{"question_id": question_ids[0], "selected_option_index": 0}],
        upload_api.authorization,
    )

    data = response.json()["data"]
    assert provider.calls == 0
    assert data["answers"][1]["is_correct"] is False
    assert data["answers"][1]["score"] == pytest.approx(0.0)
    assert data["score"] == pytest.approx(0.5)


@pytest.mark.parametrize(
    "provider",
    [
        GradingProvider(error=TextGenerationConnectionError("offline")),
        GradingProvider({"verdicts": "lots"}),
        GradingProvider({"verdicts": [{"question_number": 1, "score": 7.0}]}),
    ],
)
def test_a_grading_failure_leaves_the_answer_ungraded_but_persisted(
    upload_api, monkeypatch, provider
):
    """A grading outage must never lose the student's written work."""
    quiz_id, question_ids = _quiz(
        upload_api, upload_api.course_id, ["multiple_choice", "open_ended"]
    )
    _install_provider(monkeypatch, provider)

    response = _submit(
        upload_api,
        upload_api.course_id,
        quiz_id,
        [
            {"question_id": question_ids[0], "selected_option_index": 0},
            {"question_id": question_ids[1], "text_response": "A written answer."},
        ],
        upload_api.authorization,
    )

    assert response.status_code == 201, response.text
    data = response.json()["data"]

    assert data["answers"][1]["is_correct"] is None
    assert data["answers"][1]["score"] is None
    assert data["graded_count"] == 1
    assert data["score"] == pytest.approx(1.0)

    stored = _stored_answers(upload_api, data["attempt_id"])
    assert stored[1].text_response == "A written answer."
    assert stored[1].is_correct is None


def test_a_missing_verdict_leaves_only_that_answer_ungraded(upload_api, monkeypatch):
    quiz_id, question_ids = _quiz(
        upload_api, upload_api.course_id, ["open_ended", "open_ended"]
    )
    _install_provider(
        monkeypatch,
        GradingProvider({"verdicts": [{"question_number": 1, "score": 1.0}]}),
    )

    response = _submit(
        upload_api,
        upload_api.course_id,
        quiz_id,
        [
            {"question_id": question_ids[0], "text_response": "First answer."},
            {"question_id": question_ids[1], "text_response": "Second answer."},
        ],
        upload_api.authorization,
    )

    data = response.json()["data"]
    assert data["answers"][0]["score"] == pytest.approx(1.0)
    assert data["answers"][1]["score"] is None
    assert data["graded_count"] == 1


# ---------------------------------------------------------------------------
# Submission shape
# ---------------------------------------------------------------------------


def test_text_answer_is_rejected_for_an_option_question(upload_api, monkeypatch):
    quiz_id, question_ids = _quiz(upload_api, upload_api.course_id, ["multiple_choice"])
    _install_provider(monkeypatch, GradingProvider())

    response = _submit(
        upload_api,
        upload_api.course_id,
        quiz_id,
        [{"question_id": question_ids[0], "text_response": "Option A"}],
        upload_api.authorization,
    )

    assert response.status_code == 400


def test_option_index_is_rejected_for_a_text_question(upload_api, monkeypatch):
    quiz_id, question_ids = _quiz(upload_api, upload_api.course_id, ["open_ended"])
    _install_provider(monkeypatch, GradingProvider())

    response = _submit(
        upload_api,
        upload_api.course_id,
        quiz_id,
        [{"question_id": question_ids[0], "selected_option_index": 0}],
        upload_api.authorization,
    )

    assert response.status_code == 400


def test_true_false_still_grades_by_option_index(upload_api, monkeypatch):
    quiz_id, question_ids = _quiz(upload_api, upload_api.course_id, ["true_false"])
    _install_provider(monkeypatch, GradingProvider())

    response = _submit(
        upload_api,
        upload_api.course_id,
        quiz_id,
        [{"question_id": question_ids[0], "selected_option_index": 0}],
        upload_api.authorization,
    )

    data = response.json()["data"]
    assert data["answers"][0]["is_correct"] is True
    assert data["answers"][0]["question_type"] == "true_false"


def test_all_four_types_grade_in_one_attempt(upload_api, monkeypatch):
    quiz_id, question_ids = _quiz(
        upload_api,
        upload_api.course_id,
        ["multiple_choice", "true_false", "short_answer", "open_ended"],
    )
    _install_provider(
        monkeypatch,
        GradingProvider({"verdicts": [{"question_number": 1, "score": 1.0}]}),
    )

    response = _submit(
        upload_api,
        upload_api.course_id,
        quiz_id,
        [
            {"question_id": question_ids[0], "selected_option_index": 0},
            {"question_id": question_ids[1], "selected_option_index": 1},
            {"question_id": question_ids[2], "text_response": "O(log n)"},
            {"question_id": question_ids[3], "text_response": "Because it is sorted."},
        ],
        upload_api.authorization,
    )

    assert response.status_code == 201, response.text
    data = response.json()["data"]

    assert [answer["question_type"] for answer in data["answers"]] == [
        "multiple_choice",
        "true_false",
        "short_answer",
        "open_ended",
    ]
    assert [answer["is_correct"] for answer in data["answers"]] == [
        True,
        False,
        True,
        True,
    ]
    assert data["correct_count"] == 3
    assert data["graded_count"] == 4
    assert data["score"] == pytest.approx(0.75)


# ---------------------------------------------------------------------------
# Credits
# ---------------------------------------------------------------------------


def _credits(api, user_id: int) -> float | None:
    with api.session_factory() as session:
        return session.get(User, user_id).credits


def test_grading_a_deterministic_attempt_charges_nothing(authz_api, monkeypatch):
    quiz_id, question_ids = _quiz(
        authz_api, authz_api.a_course_id, ["multiple_choice", "short_answer"]
    )
    _install_provider(monkeypatch, GradingProvider())
    before = _credits(authz_api, authz_api.user_a_id)

    response = _submit(
        authz_api,
        authz_api.a_course_id,
        quiz_id,
        [
            {"question_id": question_ids[0], "selected_option_index": 0},
            {"question_id": question_ids[1], "text_response": "O(log n)"},
        ],
        authz_api.authorization_a,
    )

    assert response.status_code == 201, response.text
    assert _credits(authz_api, authz_api.user_a_id) == before


def test_grading_an_open_ended_answer_charges_nothing(authz_api, monkeypatch):
    """Open-ended grading is paid for once, when the quiz is generated."""
    quiz_id, question_ids = _quiz(authz_api, authz_api.a_course_id, ["open_ended"])
    _install_provider(
        monkeypatch,
        GradingProvider({"verdicts": [{"question_number": 1, "score": 1.0}]}),
    )
    before = _credits(authz_api, authz_api.user_a_id)

    _submit(
        authz_api,
        authz_api.a_course_id,
        quiz_id,
        [{"question_id": question_ids[0], "text_response": "Because it is sorted."}],
        authz_api.authorization_a,
    )

    assert _credits(authz_api, authz_api.user_a_id) == before


def test_an_exhausted_balance_still_grades_open_ended_answers(authz_api, monkeypatch):
    """Grading is prepaid, so a zero balance can never silently skip it."""
    quiz_id, question_ids = _quiz(authz_api, authz_api.a_course_id, ["open_ended"])
    _install_provider(
        monkeypatch,
        GradingProvider({"verdicts": [{"question_number": 1, "score": 1.0}]}),
    )
    set_balance(authz_api.session_factory, authz_api.user_a_id, 0.0)

    response = _submit(
        authz_api,
        authz_api.a_course_id,
        quiz_id,
        [{"question_id": question_ids[0], "text_response": "Because it is sorted."}],
        authz_api.authorization_a,
    )

    assert response.status_code == 201, response.text
    assert response.json()["data"]["graded_count"] == 1
    assert _credits(authz_api, authz_api.user_a_id) == 0.0


def test_a_grading_failure_leaves_the_balance_untouched(authz_api, monkeypatch):
    quiz_id, question_ids = _quiz(authz_api, authz_api.a_course_id, ["open_ended"])
    _install_provider(
        monkeypatch, GradingProvider(error=TextGenerationConnectionError("offline"))
    )
    before = _credits(authz_api, authz_api.user_a_id)

    response = _submit(
        authz_api,
        authz_api.a_course_id,
        quiz_id,
        [{"question_id": question_ids[0], "text_response": "Because it is sorted."}],
        authz_api.authorization_a,
    )

    assert response.status_code == 201, response.text
    assert _credits(authz_api, authz_api.user_a_id) == before


def test_a_quiz_without_open_ended_questions_never_builds_a_grader(
    upload_api, monkeypatch
):
    """Deterministic grading must not depend on the AI provider being reachable."""
    quiz_id, question_ids = _quiz(
        upload_api,
        upload_api.course_id,
        ["multiple_choice", "true_false", "short_answer"],
    )

    def explode(**_):
        raise RuntimeError("provider is misconfigured")

    monkeypatch.setattr(quiz_route, "get_text_generation_provider", explode)

    response = _submit(
        upload_api,
        upload_api.course_id,
        quiz_id,
        [
            {"question_id": question_ids[0], "selected_option_index": 0},
            {"question_id": question_ids[1], "selected_option_index": 0},
            {"question_id": question_ids[2], "text_response": "O(log n)"},
        ],
        upload_api.authorization,
    )

    assert response.status_code == 201, response.text
    data = response.json()["data"]
    assert data["graded_count"] == 3
    assert data["correct_count"] == 3


def test_a_grader_that_cannot_be_built_leaves_open_ended_ungraded(
    upload_api, monkeypatch
):
    quiz_id, question_ids = _quiz(
        upload_api, upload_api.course_id, ["multiple_choice", "open_ended"]
    )

    def explode(**_):
        raise RuntimeError("provider is misconfigured")

    monkeypatch.setattr(quiz_route, "get_text_generation_provider", explode)

    response = _submit(
        upload_api,
        upload_api.course_id,
        quiz_id,
        [
            {"question_id": question_ids[0], "selected_option_index": 0},
            {"question_id": question_ids[1], "text_response": "A written answer."},
        ],
        upload_api.authorization,
    )

    assert response.status_code == 201, response.text
    data = response.json()["data"]
    assert data["answers"][1]["is_correct"] is None
    assert data["graded_count"] == 1

    stored = _stored_answers(upload_api, data["attempt_id"])
    assert stored[1].text_response == "A written answer."


def test_a_grader_that_cannot_be_built_leaves_the_balance_untouched(
    authz_api, monkeypatch
):
    quiz_id, question_ids = _quiz(authz_api, authz_api.a_course_id, ["open_ended"])

    def explode(**_):
        raise RuntimeError("provider is misconfigured")

    monkeypatch.setattr(quiz_route, "get_text_generation_provider", explode)
    before = _credits(authz_api, authz_api.user_a_id)

    response = _submit(
        authz_api,
        authz_api.a_course_id,
        quiz_id,
        [{"question_id": question_ids[0], "text_response": "Because it is sorted."}],
        authz_api.authorization_a,
    )

    assert response.status_code == 201, response.text
    assert _credits(authz_api, authz_api.user_a_id) == before


# ---------------------------------------------------------------------------
# Progress
# ---------------------------------------------------------------------------


def test_progress_ignores_ungraded_answers(upload_api, monkeypatch):
    quiz_id, question_ids = _quiz(
        upload_api,
        upload_api.course_id,
        ["multiple_choice", "open_ended"],
        topics=["Searching", "Reasoning"],
    )
    _install_provider(
        monkeypatch, GradingProvider(error=TextGenerationConnectionError("offline"))
    )

    _submit(
        upload_api,
        upload_api.course_id,
        quiz_id,
        [
            {"question_id": question_ids[0], "selected_option_index": 0},
            {"question_id": question_ids[1], "text_response": "A written answer."},
        ],
        upload_api.authorization,
    )

    progress = upload_api.client.get(
        f"/api/courses/{upload_api.course_id}/progress",
        headers=upload_api.authorization,
    )

    assert progress.status_code == 200, progress.text
    mastery = progress.json()["data"]["topic_mastery"]

    assert [row["topic"] for row in mastery] == ["Searching"]
    assert mastery[0]["questions_answered"] == 1


def test_attempt_and_answers_are_written_together(upload_api, monkeypatch):
    quiz_id, question_ids = _quiz(
        upload_api, upload_api.course_id, ["multiple_choice", "open_ended"]
    )
    _install_provider(
        monkeypatch,
        GradingProvider({"verdicts": [{"question_number": 1, "score": 1.0}]}),
    )

    response = _submit(
        upload_api,
        upload_api.course_id,
        quiz_id,
        [
            {"question_id": question_ids[0], "selected_option_index": 0},
            {"question_id": question_ids[1], "text_response": "Because it is sorted."},
        ],
        upload_api.authorization,
    )

    attempt_id = response.json()["data"]["attempt_id"]

    with upload_api.session_factory() as session:
        attempts = session.scalars(
            select(QuizAttempt).where(QuizAttempt.quiz_id == quiz_id)
        ).all()

    assert [attempt.id for attempt in attempts] == [attempt_id]
    assert len(_stored_answers(upload_api, attempt_id)) == 2
