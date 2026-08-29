"""Practice questions and topic exams: real quizzes, tagged so mastery counts."""

import pytest
from sqlalchemy import select

import routes.exam_mode as exam_mode_route
from backend.app.models import (
    OUTPUT_TYPE_EXAM_TOPIC_EXAM,
    OUTPUT_TYPE_EXAM_TOPIC_PRACTICE,
    ExamTopicUnlock,
    GeneratedOutput,
    Quiz,
    QuizQuestion,
    User,
)
from services.credits import GENERATION_CREDIT_COSTS
from services.text_generation import TextGenerationError
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


def question(number: int, **overrides) -> dict:
    payload = {
        "question_number": number,
        "question_type": "multiple_choice",
        "topic": "Whatever the model felt like calling it",
        "question": f"Question {number} on traversal?",
        "difficulty": "medium",
        "options": ["A queue", "A stack", "A heap", "A set"],
        "correct_option_index": 0,
        "explanation": "BFS uses a queue.",
        "citations": ["S1"],
    }
    payload.update(overrides)
    return payload


def quiz_payload(count: int = 3, **overrides) -> dict:
    payload = {
        "title": "Graph Traversal practice",
        "questions": [question(index + 1) for index in range(count)],
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


def questions_of(session_factory, quiz_id: int):
    with session_factory() as session:
        return session.scalars(
            select(QuizQuestion)
            .where(QuizQuestion.quiz_id == quiz_id)
            .order_by(QuizQuestion.question_index)
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


def ask(
    authz_api,
    kind: str,
    monkeypatch,
    *,
    topic="graph-traversal",
    payload=None,
    json=None,
):
    provider = CountingProvider(payload if payload is not None else quiz_payload())
    monkeypatch.setattr(
        exam_mode_route, "get_text_generation_provider", lambda *a, **k: provider
    )
    response = authz_api.client.post(
        f"/api/courses/{authz_api.a_course_id}/exam-mode/topics/{topic}/{kind}",
        json=json if json is not None else {},
        headers=authz_api.authorization_a,
    )
    return response, provider


# --------------------------------------------------------------- the topic tag


def test_every_question_carries_the_plan_s_label_not_the_model_s(
    authz_api, planned_course, monkeypatch
) -> None:
    """Without this an attempt's mastery lands nowhere the next plan can see."""
    response, _ = ask(authz_api, "practice", monkeypatch)

    assert response.status_code == 200, response.text
    quiz_id = response.json()["data"]["quiz"]["quiz_id"]
    rows = questions_of(authz_api.session_factory, quiz_id)

    assert rows
    assert {row.topic for row in rows} == {"Graph Traversal"}


def test_an_attempt_on_a_topic_exam_moves_that_topic_s_mastery(
    authz_api, planned_course, monkeypatch
) -> None:
    """The loop that makes exam mode worth reusing the quiz tables for."""
    created, _ = ask(authz_api, "practice", monkeypatch)
    quiz_id = created.json()["data"]["quiz"]["quiz_id"]
    questions = questions_of(authz_api.session_factory, quiz_id)

    submitted = authz_api.client.post(
        f"/api/courses/{authz_api.a_course_id}/quizzes/{quiz_id}/attempts",
        json={
            "answers": [
                {"question_id": row.id, "selected_option_index": 0} for row in questions
            ]
        },
        headers=authz_api.authorization_a,
    )
    assert submitted.status_code == 201, submitted.text
    assert submitted.json()["data"]["correct_count"] == len(questions)

    plan = create_plan(
        authz_api,
        {
            "analysis_output_id": planned_course["analysis_id"],
            "selected_topic_keys": ["graph-traversal"],
        },
    )
    assert plan.status_code == 200, plan.text
    topics = {entry["topic_key"]: entry for entry in plan.json()["data"]["topics"]}

    assert topics["graph-traversal"]["mastery_percentage"] is not None
    assert plan.json()["data"]["unmapped_mastery_labels"] == 0


def test_the_quiz_records_which_plan_and_topic_produced_it(
    authz_api, planned_course, monkeypatch
) -> None:
    ask(authz_api, "practice", monkeypatch)

    quiz = quizzes_of(authz_api.session_factory, authz_api.a_course_id)[-1]

    assert quiz.purpose == "exam_topic_practice"
    assert quiz.exam_plan_output_id == planned_course["plan_id"]
    assert quiz.exam_topic_key == "graph-traversal"


def test_an_ordinary_quiz_keeps_the_model_s_own_topic_labels(
    authz_api,
    exam_course,  # noqa: F811
    monkeypatch,
) -> None:
    """The override belongs to Exam Mode; nothing else may be relabelled."""
    provider = CountingProvider(quiz_payload())
    import routes.quiz as quiz_route

    monkeypatch.setattr(
        quiz_route, "get_text_generation_provider", lambda *a, **k: provider
    )
    response = authz_api.client.post(
        f"/api/courses/{authz_api.a_course_id}/quiz",
        json={
            "question_count": 3,
            "question_types": ["multiple_choice"],
            "difficulty": "medium",
        },
        headers=authz_api.authorization_a,
    )

    assert response.status_code == 200, response.text
    quiz_id = response.json()["data"]["quiz"]["quiz_id"]
    rows = questions_of(authz_api.session_factory, quiz_id)
    assert {row.topic for row in rows} == {"Whatever the model felt like calling it"}


# --------------------------------------------------------------- exam conditions


def test_a_topic_exam_hides_its_answers(authz_api, planned_course, monkeypatch) -> None:
    response, _ = ask(authz_api, "exam", monkeypatch)

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["answers_hidden"] is True
    for entry in data["quiz"]["questions"]:
        assert entry["correct_answer"] is None
        assert entry["correct_option_index"] is None
        assert entry["explanation"] == ""


def test_practice_questions_show_their_answers(
    authz_api, planned_course, monkeypatch
) -> None:
    """Immediate feedback is the whole point of practice."""
    response, _ = ask(authz_api, "practice", monkeypatch)

    data = response.json()["data"]
    assert data["answers_hidden"] is False
    entry = data["quiz"]["questions"][0]
    assert entry["correct_option_index"] == 0
    assert entry["explanation"]


def test_hiding_the_answers_does_not_remove_them_from_the_rows(
    authz_api, planned_course, monkeypatch
) -> None:
    """Grading reads the rows, so an exam must still be gradable."""
    response, _ = ask(authz_api, "exam", monkeypatch)
    quiz_id = response.json()["data"]["quiz"]["quiz_id"]

    rows = questions_of(authz_api.session_factory, quiz_id)

    assert all(row.correct_answer is not None for row in rows)
    assert all(row.explanation for row in rows)


def test_a_topic_exam_is_shaped_by_this_course_s_own_past_questions(
    authz_api, planned_course, monkeypatch
) -> None:
    _, provider = ask(authz_api, "exam", monkeypatch)

    assert "Explain breadth-first search" in provider.prompt
    assert "Do not reuse one" in provider.prompt


def test_a_course_with_no_past_paper_is_told_so_rather_than_given_a_fiction(
    authz_api, planned_course, monkeypatch
) -> None:
    _, provider = ask(authz_api, "exam", monkeypatch, topic="dynamic-programming")

    assert "no house style to follow" in provider.prompt


# --------------------------------------------------------------- generation


def test_the_requested_question_count_reaches_the_prompt(
    authz_api, planned_course, monkeypatch
) -> None:
    _, provider = ask(
        authz_api,
        "practice",
        monkeypatch,
        payload=quiz_payload(5),
        json={"question_count": 5},
    )

    assert "Write exactly 5 questions." in provider.prompt


def test_mixed_difficulty_survives_rather_than_being_forced_to_one_level(
    authz_api, planned_course, monkeypatch
) -> None:
    """An exam pitched at one difficulty is not an exam."""
    response, _ = ask(
        authz_api,
        "exam",
        monkeypatch,
        payload=quiz_payload(
            3,
            questions=[
                question(1, difficulty="easy"),
                question(2, difficulty="medium"),
                question(3, difficulty="hard"),
            ],
        ),
    )

    assert response.status_code == 200, response.text
    quiz_id = response.json()["data"]["quiz"]["quiz_id"]
    rows = questions_of(authz_api.session_factory, quiz_id)
    assert {row.difficulty for row in rows} == {"easy", "medium", "hard"}


def test_a_question_type_the_store_cannot_hold_is_refused_before_any_row(
    authz_api, planned_course, monkeypatch
) -> None:
    before = len(quizzes_of(authz_api.session_factory, authz_api.a_course_id))

    response, _ = ask(
        authz_api,
        "exam",
        monkeypatch,
        payload=quiz_payload(
            1, questions=[question(1, question_type="proof", options=None)]
        ),
    )

    assert response.status_code == 500
    assert response.headers["X-Error-Code"] == AiErrorCode.INVALID_GENERATED_STRUCTURE
    assert len(quizzes_of(authz_api.session_factory, authz_api.a_course_id)) == before


def test_a_generated_output_row_explains_where_the_quiz_came_from(
    authz_api, planned_course, monkeypatch
) -> None:
    ask(authz_api, "practice", monkeypatch)

    with authz_api.session_factory() as session:
        output = session.scalars(
            select(GeneratedOutput).where(
                GeneratedOutput.course_id == authz_api.a_course_id,
                GeneratedOutput.output_type == OUTPUT_TYPE_EXAM_TOPIC_PRACTICE,
            )
        ).one()

    assert output.model_used
    assert "graph-traversal" in output.generation_settings


# --------------------------------------------------------------- pricing


def test_a_quiz_costs_nothing_beyond_the_topic_s_unlock(
    authz_api, planned_course, monkeypatch
) -> None:
    before = balance_of(authz_api.session_factory, authz_api.user_a_id)

    first, _ = ask(authz_api, "practice", monkeypatch)
    second, _ = ask(authz_api, "exam", monkeypatch)

    assert first.json()["data"]["credits_charged"] == UNLOCK_PRICE
    assert second.json()["data"]["credits_charged"] == 0.0
    assert (
        balance_of(authz_api.session_factory, authz_api.user_a_id)
        == before - UNLOCK_PRICE
    )


def test_a_provider_failure_writes_no_quiz_and_releases_the_unlock(
    authz_api, planned_course, monkeypatch
) -> None:
    before = balance_of(authz_api.session_factory, authz_api.user_a_id)
    provider = CountingProvider(error=TextGenerationError("the provider is down"))
    monkeypatch.setattr(
        exam_mode_route, "get_text_generation_provider", lambda *a, **k: provider
    )

    response = authz_api.client.post(
        f"/api/courses/{authz_api.a_course_id}"
        "/exam-mode/topics/graph-traversal/practice",
        json={},
        headers=authz_api.authorization_a,
    )

    assert response.status_code >= 500
    assert quizzes_of(authz_api.session_factory, authz_api.a_course_id) == []
    assert unlocks(authz_api.session_factory, authz_api.a_course_id) == []
    assert balance_of(authz_api.session_factory, authz_api.user_a_id) == before


# --------------------------------------------------------------- refusals


def test_a_topic_the_plan_never_ranked_reaches_no_provider(
    authz_api, planned_course, monkeypatch
) -> None:
    response, provider = ask(
        authz_api, "practice", monkeypatch, topic="quantum-mechanics"
    )

    assert response.status_code == 409
    assert response.headers["X-Error-Code"] == AiErrorCode.EXAM_TOPIC_NOT_DISCOVERED
    assert provider.calls == 0
    assert quizzes_of(authz_api.session_factory, authz_api.a_course_id) == []


def test_a_stranger_and_an_administrator_are_both_refused(
    authz_api, planned_course, monkeypatch
) -> None:
    provider = CountingProvider(quiz_payload())
    monkeypatch.setattr(
        exam_mode_route, "get_text_generation_provider", lambda *a, **k: provider
    )

    for headers in (authz_api.authorization_b, authz_api.authorization_admin):
        response = authz_api.client.post(
            f"/api/courses/{authz_api.a_course_id}"
            "/exam-mode/topics/graph-traversal/exam",
            json={},
            headers=headers,
        )
        assert response.status_code == 404

    assert provider.calls == 0
    assert quizzes_of(authz_api.session_factory, authz_api.a_course_id) == []


# --------------------------------------------------------------- visibility


def test_an_exam_mode_quiz_appears_in_the_course_s_quiz_list(
    authz_api, planned_course, monkeypatch
) -> None:
    """Not hidden: it is a quiz the student sat, and it counts like one."""
    ask(authz_api, "exam", monkeypatch)

    response = authz_api.client.get(
        f"/api/courses/{authz_api.a_course_id}/quizzes",
        headers=authz_api.authorization_a,
    )

    assert response.status_code == 200, response.text
    assert len(response.json()["data"]) == 1


def test_the_two_kinds_do_not_collide(authz_api, planned_course, monkeypatch) -> None:
    ask(authz_api, "practice", monkeypatch)
    ask(authz_api, "exam", monkeypatch)

    with authz_api.session_factory() as session:
        kinds = session.scalars(
            select(GeneratedOutput.output_type).where(
                GeneratedOutput.course_id == authz_api.a_course_id,
                GeneratedOutput.output_type.in_(
                    (OUTPUT_TYPE_EXAM_TOPIC_PRACTICE, OUTPUT_TYPE_EXAM_TOPIC_EXAM)
                ),
            )
        ).all()

    assert sorted(kinds) == [
        OUTPUT_TYPE_EXAM_TOPIC_EXAM,
        OUTPUT_TYPE_EXAM_TOPIC_PRACTICE,
    ]


def test_the_extracted_questions_are_read_rather_than_re_extracted(
    authz_api, planned_course, monkeypatch
) -> None:
    """Writing an exam must not reach the extraction provider a second time."""
    extract_questions(
        authz_api.session_factory,
        planned_course["paper_id"],
        extraction_payload(
            past_exam_question(question_text="Prove BFS finds shortest paths.")
        ),
    )

    def forbidden(*args, **kwargs):
        raise AssertionError("writing an exam must not re-extract a paper")

    monkeypatch.setattr(
        "services.exam_question_extraction.get_text_generation_provider", forbidden
    )
    _, provider = ask(authz_api, "exam", monkeypatch)

    assert "Prove BFS finds shortest paths." in provider.prompt
