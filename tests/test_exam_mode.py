"""Exam Mode end to end: sources, analysis, plans, ownership, and reopening."""

from datetime import date, timedelta

import pytest
from sqlalchemy import select

import routes.exam_mode as exam_mode_route
from conftest import assert_balance_is_derivable, set_balance
from backend.app.models import (
    AiUsageLog,
    OUTPUT_TYPE_EXAM_PLAN,
    OUTPUT_TYPE_EXAM_TOPIC_ANALYSIS,
    Course,
    CourseTopic,
    CreditTransaction,
    DocumentChunk,
    ExamTopicCandidate,
    GeneratedOutput,
    PastExamQuestion,
    Quiz,
    QuizAttempt,
    QuizAttemptAnswer,
    QuizQuestion,
    UploadedDocument,
    User,
)
from services.credits import GENERATION_CREDIT_COSTS
from services.text_generation import GenerationMetadata, TextGenerationError
from utils.ai_errors import AiErrorCode

STUB_METADATA = GenerationMetadata(provider="ollama", model="qwen3:8b", latency_ms=5)

FUTURE_EXAM_DATE = date.today() + timedelta(days=30)
PAST_EXAM_DATE = date.today() - timedelta(days=5)


def analysis_payload(**overrides) -> dict:
    payload = {
        "topics": [
            {
                "label": "Graph Traversal",
                "aliases": ["BFS", "DFS"],
                "in_syllabus": True,
                "in_material": True,
                "in_past_exams": True,
                "syllabus_mention_count": 3,
                "material_chunk_count": 2,
                "material_character_count": 400,
                "discovery_confidence": 0.9,
                "citations": ["S1"],
            },
            {
                "label": "Dynamic Programming",
                "in_material": True,
                "material_chunk_count": 1,
                "material_character_count": 120,
                "discovery_confidence": 0.6,
                "citations": ["S1"],
            },
        ],
        "past_exam_questions": [],
        "coverage": {"status": "Partial", "estimated_completeness": 60},
        "confidence_notes": "Based on the selected sources.",
    }
    payload.update(overrides)
    return payload


def past_exam_question(**overrides) -> dict:
    question = {
        "question_label": "Q1",
        "question_number": 1,
        "question_text": "Explain breadth-first search and give its complexity.",
        "subparts": [{"label": "a", "text": "State the complexity.", "marks": 4.0}],
        "question_type": "structured",
        "difficulty": "medium",
        "marks": 10.0,
        "answer_guidance": "Award marks for the queue invariant.",
        "marking_points": ["Queue invariant", "O(V+E)"],
        "visual_refs": [
            {"page_number": 2, "visual_index": 0, "visual_type": "diagram"}
        ],
        "topics": ["Graph Traversal"],
        "citations": ["S1"],
    }
    question.update(overrides)
    return question


class CountingProvider:
    """Records every call so a test can prove the provider was never reached."""

    def __init__(self, result: dict | None = None, error: Exception | None = None):
        self._result = result if result is not None else analysis_payload()
        self._error = error
        self.calls = 0
        self.prompt = ""

    def generate_json_with_metadata(self, prompt: str):
        self.calls += 1
        self.prompt = prompt
        if self._error is not None:
            raise self._error
        return self._result, STUB_METADATA


def install_provider(monkeypatch, provider: CountingProvider) -> CountingProvider:
    monkeypatch.setattr(
        exam_mode_route, "get_text_generation_provider", lambda *a, **k: provider
    )
    return provider


def poison_provider(monkeypatch) -> None:
    """Make any provider construction fail, so a read that touches one cannot pass."""

    def forbidden(*args, **kwargs):
        raise AssertionError("reopening an exam plan must never reach a provider")

    monkeypatch.setattr(exam_mode_route, "get_text_generation_provider", forbidden)


def add_material(
    session,
    course_id: int,
    texts,
    *,
    file_hash: str,
    retrieval_env,
    material_kind: str = "lecture_notes",
    file_name: str | None = None,
    status: str = "ready",
) -> UploadedDocument:
    course = session.get(Course, course_id)
    assert course is not None
    document = UploadedDocument(
        original_file_name=file_name or f"{file_hash[:6]}.txt",
        file_type="txt",
        mime_type="text/plain",
        file_size=10,
        file_hash=file_hash,
        user_id=course.owner_id,
        course=course,
        storage_provider="local:test",
        storage_key=f"{file_hash[:6]}.txt",
        status=status,
        material_kind=material_kind,
    )
    session.add(document)
    session.flush()
    chunks = [
        DocumentChunk(
            document=document,
            course=course,
            chunk_index=index,
            page_number=index + 1,
            end_page_number=index + 1,
            text=text,
        )
        for index, text in enumerate(texts)
    ]
    session.add_all(chunks)
    session.flush()
    retrieval_env.index(
        session,
        document,
        chunks,
        seeds=[index * 0.1 for index in range(len(chunks))],
    )
    session.commit()
    return document


def set_exam_date(session_factory, course_id: int, value: date | None) -> None:
    with session_factory() as session:
        course = session.get(Course, course_id)
        course.exam_date = value
        session.commit()


def set_topics(session_factory, course_id: int, names: list[str]) -> None:
    with session_factory() as session:
        course = session.get(Course, course_id)
        for row in list(course.topic_rows):
            session.delete(row)
        session.flush()
        session.add_all(
            CourseTopic(course_id=course_id, position=index, name=name)
            for index, name in enumerate(names)
        )
        session.commit()


def outputs_of(session_factory, course_id: int, output_type: str):
    with session_factory() as session:
        return session.scalars(
            select(GeneratedOutput).where(
                GeneratedOutput.course_id == course_id,
                GeneratedOutput.output_type == output_type,
            )
        ).all()


def credit_rows(session_factory, user_id: int):
    with session_factory() as session:
        return session.scalars(
            select(CreditTransaction).where(CreditTransaction.user_id == user_id)
        ).all()


def balance_of(session_factory, user_id: int):
    with session_factory() as session:
        return session.get(User, user_id).credits


@pytest.fixture
def exam_course(authz_api, retrieval_env):
    """Owner A's course with a syllabus, topics, lecture material, and a paper."""
    set_exam_date(authz_api.session_factory, authz_api.a_course_id, FUTURE_EXAM_DATE)
    with authz_api.session_factory() as session:
        course = session.get(Course, authz_api.a_course_id)
        course.syllabus = (
            "Week 1 Graph Traversal, assessed heavily. Week 2 Dynamic Programming."
        )
        session.commit()
    set_topics(
        authz_api.session_factory,
        authz_api.a_course_id,
        ["Graph Traversal", "Recursion"],
    )
    with authz_api.session_factory() as session:
        lecture = add_material(
            session,
            authz_api.a_course_id,
            ["Graph traversal covers BFS and DFS.", "Dynamic programming overview."],
            file_hash="a1" + "1" * 62,
            retrieval_env=retrieval_env,
            file_name="Lecture 1.txt",
        )
        paper = add_material(
            session,
            authz_api.a_course_id,
            ["Past exam 2024 question one on graph traversal."],
            file_hash="b2" + "2" * 62,
            retrieval_env=retrieval_env,
            material_kind="past_exam",
            file_name="Past Exam 2024.txt",
        )
        return {"lecture_id": lecture.id, "paper_id": paper.id}


def run_analysis(authz_api, monkeypatch, *, payload=None, json=None, rescan=False):
    provider = install_provider(monkeypatch, CountingProvider(payload))
    suffix = "/rescan" if rescan else ""
    response = authz_api.client.post(
        f"/api/courses/{authz_api.a_course_id}/exam-mode/analysis{suffix}",
        json=json if json is not None else {},
        headers=authz_api.authorization_a,
    )
    return response, provider


def create_plan(authz_api, body: dict):
    return authz_api.client.post(
        f"/api/courses/{authz_api.a_course_id}/exam-mode/plans",
        json=body,
        headers=authz_api.authorization_a,
    )


# --------------------------------------------------------------- sources


def test_the_source_inventory_reports_what_the_course_can_supply(
    authz_api, exam_course
) -> None:
    response = authz_api.client.get(
        f"/api/courses/{authz_api.a_course_id}/exam-mode/sources",
        headers=authz_api.authorization_a,
    )

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["syllabus_present"] is True
    assert data["course_topics"] == ["Graph Traversal", "Recursion"]
    assert data["past_exam_document_count"] == 1
    assert data["chunks_available"] >= 3
    labels = {document["label"] for document in data["documents"]}
    assert {"Lecture 1", "Past Exam 2024"} <= labels
    assert all(document["status"] != "deleting" for document in data["documents"])


# --------------------------------------------------------------- analysis


def test_an_owner_can_analyse_and_the_evidence_is_persisted_as_rows(
    authz_api, exam_course, monkeypatch
) -> None:
    response, provider = run_analysis(
        authz_api,
        monkeypatch,
        payload=analysis_payload(past_exam_questions=[past_exam_question()]),
    )

    assert response.status_code == 200, response.text
    assert provider.calls == 1
    data = response.json()["data"]["analysis"]
    assert data["candidate_count"] == 3
    assert data["past_exam_question_count"] == 1
    assert data["manual_review_recommended"] is True

    keys = {topic["topic_key"] for topic in data["topics"]}
    assert "graph-traversal" in keys
    assert "dynamic-programming" in keys

    with authz_api.session_factory() as session:
        rows = session.scalars(
            select(ExamTopicCandidate).where(
                ExamTopicCandidate.course_id == authz_api.a_course_id
            )
        ).all()
        assert {row.topic_key for row in rows} == keys
        assert all(
            row.analysis_output_id == data["generated_output_id"] for row in rows
        )


def test_a_declared_topic_no_source_mentions_is_still_surfaced(
    authz_api, exam_course, monkeypatch
) -> None:
    """The gap between what the student listed and what the course covers."""
    response, _ = run_analysis(authz_api, monkeypatch)

    topics = {
        topic["topic_key"]: topic
        for topic in response.json()["data"]["analysis"]["topics"]
    }
    assert "recursion" in topics
    assert topics["recursion"]["in_course_topics"] is True
    assert topics["recursion"]["in_material"] is False
    assert topics["graph-traversal"]["in_course_topics"] is True


def test_the_prompt_carries_the_material_last_and_treats_it_as_data(
    authz_api, exam_course, monkeypatch
) -> None:
    _, provider = run_analysis(authz_api, monkeypatch)

    prompt = provider.prompt
    assert prompt.index("COURSE MATERIAL") > prompt.index("SOURCE CITATIONS")
    assert prompt.index("SYLLABUS MATERIAL") > prompt.index("TOPIC RULES")
    assert "{{" not in prompt


def test_analysis_charges_once_and_a_rescan_charges_the_cheaper_price(
    authz_api, exam_course, monkeypatch
) -> None:
    before = balance_of(authz_api.session_factory, authz_api.user_a_id)

    run_analysis(authz_api, monkeypatch)
    after_first = balance_of(authz_api.session_factory, authz_api.user_a_id)
    assert before - after_first == GENERATION_CREDIT_COSTS["exam_topic_analysis"]

    run_analysis(authz_api, monkeypatch, rescan=True)
    after_rescan = balance_of(authz_api.session_factory, authz_api.user_a_id)
    assert (
        after_first - after_rescan
        == (GENERATION_CREDIT_COSTS["exam_topic_analysis_rescan"])
    )
    assert (
        GENERATION_CREDIT_COSTS["exam_topic_analysis_rescan"]
        < (GENERATION_CREDIT_COSTS["exam_topic_analysis"])
    )


def test_a_malformed_provider_response_persists_nothing_and_refunds(
    authz_api, exam_course, monkeypatch
) -> None:
    before = balance_of(authz_api.session_factory, authz_api.user_a_id)
    response, provider = run_analysis(authz_api, monkeypatch, payload={"topics": []})

    assert response.status_code == 500
    assert response.headers["X-Error-Code"] == AiErrorCode.INVALID_GENERATED_STRUCTURE
    assert provider.calls == 1
    assert (
        outputs_of(
            authz_api.session_factory,
            authz_api.a_course_id,
            OUTPUT_TYPE_EXAM_TOPIC_ANALYSIS,
        )
        == []
    )
    assert balance_of(authz_api.session_factory, authz_api.user_a_id) == before


def test_a_course_with_no_processed_material_fails_before_the_provider(
    authz_api, monkeypatch
) -> None:
    set_exam_date(authz_api.session_factory, authz_api.a_course_id, FUTURE_EXAM_DATE)
    before = balance_of(authz_api.session_factory, authz_api.user_a_id)

    response, provider = run_analysis(authz_api, monkeypatch)

    assert response.status_code == 400
    assert response.headers["X-Error-Code"] == AiErrorCode.NO_READY_MATERIAL
    assert provider.calls == 0
    assert balance_of(authz_api.session_factory, authz_api.user_a_id) == before


# --------------------------------------------------------------- source scope


def test_a_document_from_another_course_is_answered_as_a_missing_one(
    authz_api, exam_course, monkeypatch
) -> None:
    with authz_api.session_factory() as session:
        other = session.scalar(
            select(UploadedDocument).where(
                UploadedDocument.course_id == authz_api.b_course_id
            )
        )
        foreign_id = str(other.id) if other else "00000000-0000-0000-0000-000000000001"

    before = balance_of(authz_api.session_factory, authz_api.user_a_id)
    response, provider = run_analysis(
        authz_api, monkeypatch, json={"document_ids": [foreign_id]}
    )

    assert response.status_code == 404
    assert provider.calls == 0
    assert balance_of(authz_api.session_factory, authz_api.user_a_id) == before


def test_a_selected_document_still_processing_says_so_and_spends_nothing(
    authz_api, exam_course, retrieval_env, monkeypatch
) -> None:
    with authz_api.session_factory() as session:
        pending = add_material(
            session,
            authz_api.a_course_id,
            ["Not indexed yet."],
            file_hash="c3" + "3" * 62,
            retrieval_env=retrieval_env,
            status="processing",
        )
        pending_id = str(pending.id)

    before = balance_of(authz_api.session_factory, authz_api.user_a_id)
    response, provider = run_analysis(
        authz_api, monkeypatch, json={"document_ids": [pending_id]}
    )

    assert response.status_code == 409
    assert response.headers["X-Error-Code"] == AiErrorCode.SOURCE_NOT_READY
    assert provider.calls == 0
    assert balance_of(authz_api.session_factory, authz_api.user_a_id) == before


def test_only_the_selected_documents_reach_the_prompt(
    authz_api, exam_course, monkeypatch
) -> None:
    _, provider = run_analysis(
        authz_api,
        monkeypatch,
        json={"document_ids": [str(exam_course["paper_id"])]},
    )

    assert "Past exam 2024 question one" in provider.prompt
    assert "Dynamic programming overview" not in provider.prompt


# --------------------------------------------------------------- past exams


def test_a_past_exam_question_is_extracted_with_its_stated_values(
    authz_api, exam_course, monkeypatch
) -> None:
    response, _ = run_analysis(
        authz_api,
        monkeypatch,
        payload=analysis_payload(past_exam_questions=[past_exam_question()]),
        json={"document_ids": [str(exam_course["paper_id"])]},
    )
    analysis_id = response.json()["data"]["analysis"]["generated_output_id"]

    questions = authz_api.client.get(
        f"/api/courses/{authz_api.a_course_id}"
        f"/exam-mode/analysis/{analysis_id}/questions",
        headers=authz_api.authorization_a,
    ).json()["data"]["questions"]

    assert len(questions) == 1
    question = questions[0]
    assert question["question_text"].startswith("Explain breadth-first search")
    assert question["question_type"] == "structured"
    assert question["difficulty"] == "medium"
    assert question["marks"] == 10.0
    assert question["answer_guidance"] == "Award marks for the queue invariant."
    assert question["marking_points"] == ["Queue invariant", "O(V+E)"]
    assert question["visual_refs"] == [
        {"page_number": 2, "visual_index": 0, "visual_type": "diagram"}
    ]
    assert question["topic_key"] == "graph-traversal"
    assert question["document_id"] == str(exam_course["paper_id"])
    assert question["page_start"] == 1
    assert question["citations"]


def test_an_absent_mark_or_answer_stays_null_rather_than_being_invented(
    authz_api, exam_course, monkeypatch
) -> None:
    response, _ = run_analysis(
        authz_api,
        monkeypatch,
        payload=analysis_payload(
            past_exam_questions=[
                past_exam_question(
                    marks=None,
                    difficulty=None,
                    answer_guidance=None,
                    marking_points=[],
                    visual_refs=[],
                    subparts=[],
                )
            ]
        ),
        json={"document_ids": [str(exam_course["paper_id"])]},
    )
    analysis_id = response.json()["data"]["analysis"]["generated_output_id"]

    question = authz_api.client.get(
        f"/api/courses/{authz_api.a_course_id}"
        f"/exam-mode/analysis/{analysis_id}/questions",
        headers=authz_api.authorization_a,
    ).json()["data"]["questions"][0]

    assert question["marks"] is None
    assert question["difficulty"] is None
    assert question["answer_guidance"] is None
    assert question["marking_points"] == []
    assert question["visual_refs"] == []


def test_a_document_that_is_not_a_past_exam_never_becomes_exam_evidence(
    authz_api, exam_course, monkeypatch
) -> None:
    """Only the past_exam material kind counts, however exam-like the prose is."""
    response, _ = run_analysis(
        authz_api,
        monkeypatch,
        payload=analysis_payload(past_exam_questions=[past_exam_question()]),
        json={"document_ids": [str(exam_course["lecture_id"])]},
    )
    analysis_id = response.json()["data"]["analysis"]["generated_output_id"]

    with authz_api.session_factory() as session:
        rows = session.scalars(
            select(PastExamQuestion).where(
                PastExamQuestion.analysis_output_id == analysis_id
            )
        ).all()
        assert all(row.document_id is None for row in rows)
        candidates = session.scalars(
            select(ExamTopicCandidate).where(
                ExamTopicCandidate.analysis_output_id == analysis_id
            )
        ).all()
        assert all(candidate.past_exam_question_count == 0 for candidate in candidates)
        assert all(candidate.in_past_exams is False for candidate in candidates)


def test_an_unknown_citation_key_is_dropped_before_persistence(
    authz_api, exam_course, monkeypatch
) -> None:
    response, _ = run_analysis(
        authz_api,
        monkeypatch,
        payload=analysis_payload(
            past_exam_questions=[past_exam_question(citations=["S999"])]
        ),
        json={"document_ids": [str(exam_course["paper_id"])]},
    )
    analysis_id = response.json()["data"]["analysis"]["generated_output_id"]

    with authz_api.session_factory() as session:
        row = session.scalar(
            select(PastExamQuestion).where(
                PastExamQuestion.analysis_output_id == analysis_id
            )
        )
        assert row.citations is None
        assert row.document_id is None
        assert row.page_start is None


# --------------------------------------------------------------- plans


def _analyse_then_plan(authz_api, monkeypatch, *, body=None, payload=None):
    response, _ = run_analysis(
        authz_api,
        monkeypatch,
        payload=payload or analysis_payload(past_exam_questions=[past_exam_question()]),
    )
    assert response.status_code == 200, response.text
    analysis_id = response.json()["data"]["analysis"]["generated_output_id"]
    return analysis_id, create_plan(
        authz_api,
        body
        if body is not None
        else {
            "analysis_output_id": analysis_id,
            "selected_topic_keys": ["graph-traversal", "dynamic-programming"],
            "high_priority_topic_keys": ["dynamic-programming"],
        },
    )


def test_an_owned_course_with_a_future_exam_date_produces_a_ranked_plan(
    authz_api, exam_course, monkeypatch
) -> None:
    analysis_id, response = _analyse_then_plan(authz_api, monkeypatch)

    assert response.status_code == 200, response.text
    plan = response.json()["data"]
    assert plan["analysis_output_id"] == analysis_id
    assert plan["plan_version"] == 1
    assert plan["supersedes_output_id"] is None
    assert plan["ranking_engine"] == "deterministic"
    assert plan["exam_date"] == FUTURE_EXAM_DATE.isoformat()
    assert plan["days_until_exam"] == 30
    assert sum(plan["effective_weights"].values()) == 100
    assert [topic["rank"] for topic in plan["topics"]] == [1, 2]
    assert plan["topics"][0]["topic_key"] == "dynamic-programming"
    assert plan["topics"][0]["is_high_priority"] is True


def test_only_the_selected_topics_are_ranked(
    authz_api, exam_course, monkeypatch
) -> None:
    analysis_id, _ = _analyse_then_plan(
        authz_api,
        monkeypatch,
        body={"selected_topic_keys": ["graph-traversal"]},
    )
    response = create_plan(
        authz_api,
        {"analysis_output_id": analysis_id, "selected_topic_keys": ["graph-traversal"]},
    )

    keys = [topic["topic_key"] for topic in response.json()["data"]["topics"]]
    assert keys == ["graph-traversal"]


def test_the_plan_explains_why_each_topic_received_its_rank(
    authz_api, exam_course, monkeypatch
) -> None:
    _, response = _analyse_then_plan(authz_api, monkeypatch)

    for topic in response.json()["data"]["topics"]:
        assert topic["explanation"]
        assert topic["reason_codes"]
        assert set(topic["signals"]) == {
            "syllabus",
            "past_exam",
            "mastery",
            "material",
        }
        for breakdown in topic["signals"].values():
            assert set(breakdown) >= {
                "available",
                "raw_value",
                "normalized_value",
                "effective_weight",
            }
            if not breakdown["available"]:
                assert breakdown["raw_value"] is None
                assert breakdown["normalized_value"] is None
                assert breakdown["effective_weight"] == 0


def test_creating_a_plan_reaches_no_provider_and_charges_nothing(
    authz_api, exam_course, monkeypatch
) -> None:
    response, _ = run_analysis(authz_api, monkeypatch)
    analysis_id = response.json()["data"]["analysis"]["generated_output_id"]

    before = balance_of(authz_api.session_factory, authz_api.user_a_id)
    poison_provider(monkeypatch)

    created = create_plan(
        authz_api,
        {"analysis_output_id": analysis_id, "selected_topic_keys": ["graph-traversal"]},
    )

    assert created.status_code == 200, created.text
    assert balance_of(authz_api.session_factory, authz_api.user_a_id) == before
    assert "exam_plan" not in GENERATION_CREDIT_COSTS


def test_a_plan_records_no_model_because_no_model_produced_it(
    authz_api, exam_course, monkeypatch
) -> None:
    _, response = _analyse_then_plan(authz_api, monkeypatch)
    plan_id = response.json()["data"]["generated_output_id"]

    with authz_api.session_factory() as session:
        plan = session.get(GeneratedOutput, plan_id)
        assert plan.output_type == OUTPUT_TYPE_EXAM_PLAN
        assert plan.model_used is None
        assert plan.user_id == authz_api.user_a_id
        analysis = session.get(
            GeneratedOutput, response.json()["data"]["analysis_output_id"]
        )
        assert analysis.model_used == "ollama:qwen3:8b"


def test_a_plan_needs_a_topic_selection(authz_api, exam_course, monkeypatch) -> None:
    response, _ = run_analysis(authz_api, monkeypatch)
    analysis_id = response.json()["data"]["analysis"]["generated_output_id"]

    created = create_plan(
        authz_api, {"analysis_output_id": analysis_id, "selected_topic_keys": []}
    )

    assert created.status_code == 409
    assert created.headers["X-Error-Code"] == (
        AiErrorCode.EXAM_TOPIC_SELECTION_REQUIRED
    )


def test_a_topic_outside_the_analysis_is_refused(
    authz_api, exam_course, monkeypatch
) -> None:
    response, _ = run_analysis(authz_api, monkeypatch)
    analysis_id = response.json()["data"]["analysis"]["generated_output_id"]

    created = create_plan(
        authz_api,
        {"analysis_output_id": analysis_id, "selected_topic_keys": ["not-a-topic"]},
    )

    assert created.status_code == 409
    assert created.headers["X-Error-Code"] == AiErrorCode.EXAM_TOPIC_NOT_DISCOVERED


def test_a_plan_without_an_analysis_names_the_next_action(authz_api, exam_course):
    created = create_plan(authz_api, {"selected_topic_keys": ["graph-traversal"]})

    assert created.status_code == 409
    assert created.headers["X-Error-Code"] == AiErrorCode.EXAM_ANALYSIS_REQUIRED


def test_a_first_plan_requires_an_exam_date(
    authz_api, exam_course, monkeypatch
) -> None:
    set_exam_date(authz_api.session_factory, authz_api.a_course_id, None)
    response, _ = run_analysis(authz_api, monkeypatch)
    analysis_id = response.json()["data"]["analysis"]["generated_output_id"]

    created = create_plan(
        authz_api,
        {"analysis_output_id": analysis_id, "selected_topic_keys": ["graph-traversal"]},
    )

    assert created.status_code == 400
    assert created.headers["X-Error-Code"] == AiErrorCode.EXAM_DATE_MISSING


def test_a_first_plan_requires_the_exam_date_to_be_in_the_future(
    authz_api, exam_course, monkeypatch
) -> None:
    set_exam_date(authz_api.session_factory, authz_api.a_course_id, PAST_EXAM_DATE)
    response, _ = run_analysis(authz_api, monkeypatch)
    analysis_id = response.json()["data"]["analysis"]["generated_output_id"]

    created = create_plan(
        authz_api,
        {"analysis_output_id": analysis_id, "selected_topic_keys": ["graph-traversal"]},
    )

    assert created.status_code == 400
    assert created.headers["X-Error-Code"] == AiErrorCode.EXAM_DATE_NOT_FUTURE


def test_automatic_selection_takes_every_discovered_topic_and_still_asks_for_review(
    authz_api, exam_course, monkeypatch
) -> None:
    response, provider = run_analysis(authz_api, monkeypatch)
    analysis_id = response.json()["data"]["analysis"]["generated_output_id"]
    discovered = {
        topic["topic_key"] for topic in response.json()["data"]["analysis"]["topics"]
    }

    created = create_plan(
        authz_api,
        {"analysis_output_id": analysis_id, "selection_mode": "all_discovered"},
    )

    assert created.status_code == 200, created.text
    plan = created.json()["data"]
    assert {topic["topic_key"] for topic in plan["topics"]} == discovered
    assert plan["selection_mode"] == "all_discovered"
    assert plan["manual_review_recommended"] is True
    # The automatic path reuses the analysis rather than asking the model again.
    assert provider.calls == 1


# --------------------------------------------------------------- reopening


def test_reopening_a_plan_returns_the_stored_ranking_without_a_provider(
    authz_api, exam_course, retrieval_env, monkeypatch
) -> None:
    _, created = _analyse_then_plan(authz_api, monkeypatch)
    plan_id = created.json()["data"]["generated_output_id"]
    stored = created.json()["data"]

    before_balance = balance_of(authz_api.session_factory, authz_api.user_a_id)
    before_rows = len(credit_rows(authz_api.session_factory, authz_api.user_a_id))
    before_outputs = len(
        outputs_of(
            authz_api.session_factory, authz_api.a_course_id, OUTPUT_TYPE_EXAM_PLAN
        )
    )

    poison_provider(monkeypatch)
    embed_calls = len(retrieval_env.provider.embed_query_calls)

    response = authz_api.client.get(
        f"/api/courses/{authz_api.a_course_id}/exam-mode/plans/{plan_id}",
        headers=authz_api.authorization_a,
    )

    assert response.status_code == 200, response.text
    reopened = response.json()["data"]
    assert reopened["topics"] == stored["topics"]
    # No retrieval either: a reopen is a database read, so nothing is embedded.
    assert len(retrieval_env.provider.embed_query_calls) == embed_calls
    assert reopened["effective_weights"] == stored["effective_weights"]
    assert balance_of(authz_api.session_factory, authz_api.user_a_id) == before_balance
    assert (
        len(credit_rows(authz_api.session_factory, authz_api.user_a_id)) == before_rows
    )
    assert (
        len(
            outputs_of(
                authz_api.session_factory,
                authz_api.a_course_id,
                OUTPUT_TYPE_EXAM_PLAN,
            )
        )
        == before_outputs
    )


def test_a_plan_still_opens_after_the_exam_date_has_passed(
    authz_api, exam_course, retrieval_env, monkeypatch
) -> None:
    """A plan is a study resource; it does not expire with the exam."""
    _, created = _analyse_then_plan(authz_api, monkeypatch)
    plan_id = created.json()["data"]["generated_output_id"]

    set_exam_date(authz_api.session_factory, authz_api.a_course_id, PAST_EXAM_DATE)
    poison_provider(monkeypatch)
    embed_calls = len(retrieval_env.provider.embed_query_calls)

    response = authz_api.client.get(
        f"/api/courses/{authz_api.a_course_id}/exam-mode/plans/{plan_id}",
        headers=authz_api.authorization_a,
    )

    assert response.status_code == 200, response.text
    assert response.json()["data"]["topics"]
    assert len(retrieval_env.provider.embed_query_calls) == embed_calls
    # The date passing is not a mutation: the stored plan still says what it said.
    assert response.json()["data"]["exam_date"] == FUTURE_EXAM_DATE.isoformat()


# --------------------------------------------------------------- versioning


def test_a_new_plan_supersedes_the_old_one_and_leaves_it_untouched(
    authz_api, exam_course, monkeypatch
) -> None:
    analysis_id, first = _analyse_then_plan(authz_api, monkeypatch)
    first_id = first.json()["data"]["generated_output_id"]
    first_topics = first.json()["data"]["topics"]

    second = create_plan(
        authz_api,
        {"analysis_output_id": analysis_id, "selected_topic_keys": ["graph-traversal"]},
    )

    assert second.status_code == 200, second.text
    assert second.json()["data"]["plan_version"] == 2
    assert second.json()["data"]["supersedes_output_id"] == first_id

    reopened = authz_api.client.get(
        f"/api/courses/{authz_api.a_course_id}/exam-mode/plans/{first_id}",
        headers=authz_api.authorization_a,
    ).json()["data"]
    assert reopened["plan_version"] == 1
    assert reopened["topics"] == first_topics


def test_the_plan_list_names_the_current_version_and_keeps_the_history(
    authz_api, exam_course, monkeypatch
) -> None:
    analysis_id, first = _analyse_then_plan(authz_api, monkeypatch)
    second = create_plan(
        authz_api,
        {"analysis_output_id": analysis_id, "selected_topic_keys": ["graph-traversal"]},
    )

    listing = authz_api.client.get(
        f"/api/courses/{authz_api.a_course_id}/exam-mode/plans",
        headers=authz_api.authorization_a,
    ).json()["data"]

    assert (
        listing["current_plan_output_id"]
        == (second.json()["data"]["generated_output_id"])
    )
    assert [plan["plan_version"] for plan in listing["plans"]] == [2, 1]
    assert [plan["is_current"] for plan in listing["plans"]] == [True, False]


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        ("exam_date", "exam_date_changed"),
        ("syllabus", "syllabus_changed"),
        ("topics", "course_topics_changed"),
        ("mastery", "new_quiz_results"),
    ],
)
def test_a_changed_input_makes_the_plan_stale_for_its_own_reason(
    authz_api, exam_course, monkeypatch, mutate, reason
) -> None:
    _, created = _analyse_then_plan(authz_api, monkeypatch)
    plan_id = created.json()["data"]["generated_output_id"]
    assert created.json()["data"]["staleness"]["is_stale"] is False

    if mutate == "exam_date":
        set_exam_date(
            authz_api.session_factory,
            authz_api.a_course_id,
            FUTURE_EXAM_DATE + timedelta(days=7),
        )
    elif mutate == "syllabus":
        with authz_api.session_factory() as session:
            session.get(Course, authz_api.a_course_id).syllabus = "Entirely new scope."
            session.commit()
    elif mutate == "topics":
        set_topics(authz_api.session_factory, authz_api.a_course_id, ["Sorting"])
    elif mutate == "mastery":
        _record_attempt(authz_api, "Graph Traversal", correct=False)

    poison_provider(monkeypatch)
    response = authz_api.client.get(
        f"/api/courses/{authz_api.a_course_id}/exam-mode/plans/{plan_id}",
        headers=authz_api.authorization_a,
    )

    staleness = response.json()["data"]["staleness"]
    assert staleness["is_stale"] is True
    assert reason in staleness["stale_reasons"]


def test_a_moved_exam_date_does_not_demand_another_scan(
    authz_api, exam_course, monkeypatch
) -> None:
    """The countdown moved, not the priorities."""
    _, created = _analyse_then_plan(authz_api, monkeypatch)
    plan_id = created.json()["data"]["generated_output_id"]
    set_exam_date(
        authz_api.session_factory,
        authz_api.a_course_id,
        FUTURE_EXAM_DATE + timedelta(days=7),
    )

    staleness = authz_api.client.get(
        f"/api/courses/{authz_api.a_course_id}/exam-mode/plans/{plan_id}",
        headers=authz_api.authorization_a,
    ).json()["data"]["staleness"]

    assert staleness["stale_reasons"] == ["exam_date_changed"]
    assert staleness["is_stale"] is True
    assert staleness["requires_rescan"] is False


def _record_attempt(authz_api, topic: str, *, correct: bool) -> None:
    with authz_api.session_factory() as session:
        quiz = Quiz(
            course_id=authz_api.a_course_id,
            user_id=authz_api.user_a_id,
            title="Practice",
        )
        session.add(quiz)
        session.flush()
        question = QuizQuestion(
            quiz_id=quiz.id,
            question_index=0,
            question_type="true_false",
            question_text="Is BFS level order?",
            correct_answer=True,
            topic=topic,
        )
        session.add(question)
        session.flush()
        attempt = QuizAttempt(
            user_id=authz_api.user_a_id, quiz_id=quiz.id, score=1.0 if correct else 0.0
        )
        session.add(attempt)
        session.flush()
        session.add(
            QuizAttemptAnswer(
                attempt_id=attempt.id,
                quiz_question_id=question.id,
                is_correct=correct,
                topic=topic,
            )
        )
        session.commit()


def test_mastery_reaches_the_plan_through_the_canonical_topic_key(
    authz_api, exam_course, monkeypatch
) -> None:
    _record_attempt(authz_api, "graph traversals", correct=False)
    _, created = _analyse_then_plan(authz_api, monkeypatch)

    topics = {topic["topic_key"]: topic for topic in created.json()["data"]["topics"]}
    assert topics["graph-traversal"]["mastery_percentage"] == 0
    assert topics["graph-traversal"]["is_unattempted"] is False
    assert topics["dynamic-programming"]["mastery_percentage"] is None
    assert topics["dynamic-programming"]["is_unattempted"] is True


def test_a_mastery_label_matching_nothing_is_counted_rather_than_attributed(
    authz_api, exam_course, monkeypatch
) -> None:
    _record_attempt(authz_api, "Quantum Entanglement", correct=False)
    _, created = _analyse_then_plan(authz_api, monkeypatch)

    plan = created.json()["data"]
    assert plan["unmapped_mastery_labels"] == 1
    assert "unmapped_mastery_labels" in plan["warnings"]


# --------------------------------------------------------------- rescan


def test_a_rescan_reports_carry_over_without_changing_the_existing_plan(
    authz_api, exam_course, monkeypatch
) -> None:
    _, created = _analyse_then_plan(authz_api, monkeypatch)
    plan_id = created.json()["data"]["generated_output_id"]
    original_topics = created.json()["data"]["topics"]

    rescan_payload = analysis_payload(
        topics=[
            *analysis_payload()["topics"],
            {
                "label": "Sorting",
                "in_material": True,
                "material_chunk_count": 1,
                "discovery_confidence": 0.5,
            },
        ]
    )
    response, _ = run_analysis(
        authz_api, monkeypatch, payload=rescan_payload, rescan=True
    )

    assert response.status_code == 200, response.text
    carry = response.json()["data"]["analysis"]["selection_carry_over"]
    assert carry["previous_plan_output_id"] == plan_id
    assert set(carry["preselected_topic_keys"]) == {
        "graph-traversal",
        "dynamic-programming",
    }
    assert carry["high_priority_topic_keys"] == ["dynamic-programming"]
    assert "sorting" in carry["new_topic_keys"]

    plans = outputs_of(
        authz_api.session_factory, authz_api.a_course_id, OUTPUT_TYPE_EXAM_PLAN
    )
    assert len(plans) == 1
    reopened = authz_api.client.get(
        f"/api/courses/{authz_api.a_course_id}/exam-mode/plans/{plan_id}",
        headers=authz_api.authorization_a,
    ).json()["data"]
    assert reopened["topics"] == original_topics


def test_a_rescan_reports_a_previously_selected_topic_that_vanished(
    authz_api, exam_course, monkeypatch
) -> None:
    _, created = _analyse_then_plan(authz_api, monkeypatch)

    shrunk = analysis_payload(topics=[analysis_payload()["topics"][0]])
    response, _ = run_analysis(authz_api, monkeypatch, payload=shrunk, rescan=True)

    carry = response.json()["data"]["analysis"]["selection_carry_over"]
    assert "dynamic-programming" in carry["unsupported_topic_keys"]
    assert "dynamic-programming" not in carry["preselected_topic_keys"]


# --------------------------------------------------------------- ownership


@pytest.mark.parametrize(
    ("method", "suffix", "body"),
    [
        ("GET", "/sources", None),
        ("POST", "/analysis", {}),
        ("POST", "/analysis/rescan", {}),
        ("GET", "/analysis", None),
        ("POST", "/plans", {"selected_topic_keys": ["graph-traversal"]}),
        ("GET", "/plans", None),
    ],
)
def test_a_stranger_cannot_reach_another_owners_exam_mode(
    authz_api, exam_course, monkeypatch, method, suffix, body
) -> None:
    provider = install_provider(monkeypatch, CountingProvider())
    before = len(credit_rows(authz_api.session_factory, authz_api.user_a_id))

    response = authz_api.client.request(
        method,
        f"/api/courses/{authz_api.a_course_id}/exam-mode{suffix}",
        json=body,
        headers=authz_api.authorization_b,
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "Course not found"}
    assert provider.calls == 0
    assert len(credit_rows(authz_api.session_factory, authz_api.user_a_id)) == before
    assert (
        outputs_of(
            authz_api.session_factory,
            authz_api.a_course_id,
            OUTPUT_TYPE_EXAM_TOPIC_ANALYSIS,
        )
        == []
    )


@pytest.mark.parametrize(
    ("suffix", "body"),
    [
        ("/analysis", {}),
        ("/analysis/rescan", {}),
        ("/plans", {"selected_topic_keys": ["graph-traversal"]}),
    ],
)
def test_an_administrator_may_read_but_never_generate_in_another_course(
    authz_api, exam_course, monkeypatch, suffix, body
) -> None:
    provider = install_provider(monkeypatch, CountingProvider())

    denied = authz_api.client.post(
        f"/api/courses/{authz_api.a_course_id}/exam-mode{suffix}",
        json=body,
        headers=authz_api.authorization_admin,
    )

    assert denied.status_code == 404
    assert denied.json() == {"detail": "Course not found"}
    assert provider.calls == 0
    assert (
        outputs_of(
            authz_api.session_factory, authz_api.a_course_id, OUTPUT_TYPE_EXAM_PLAN
        )
        == []
    )

    allowed = authz_api.client.get(
        f"/api/courses/{authz_api.a_course_id}/exam-mode/sources",
        headers=authz_api.authorization_admin,
    )
    assert allowed.status_code == 200


def test_an_administrator_reading_a_plan_does_not_see_it_as_stale(
    authz_api, exam_course, monkeypatch
) -> None:
    """Staleness compares the owner's mastery, not the reader's."""
    _record_attempt(authz_api, "Graph Traversal", correct=True)
    _, created = _analyse_then_plan(authz_api, monkeypatch)
    plan_id = created.json()["data"]["generated_output_id"]

    poison_provider(monkeypatch)
    response = authz_api.client.get(
        f"/api/courses/{authz_api.a_course_id}/exam-mode/plans/{plan_id}",
        headers=authz_api.authorization_admin,
    )

    assert response.status_code == 200, response.text
    assert response.json()["data"]["staleness"]["stale_reasons"] == []


def test_a_plan_from_another_course_is_indistinguishable_from_a_missing_one(
    authz_api, exam_course, monkeypatch
) -> None:
    _, created = _analyse_then_plan(authz_api, monkeypatch)
    plan_id = created.json()["data"]["generated_output_id"]

    response = authz_api.client.get(
        f"/api/courses/{authz_api.b_course_id}/exam-mode/plans/{plan_id}",
        headers=authz_api.authorization_b,
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "Exam plan not found"}


# --------------------------------------------------------------- persistence


def test_the_analysis_row_and_its_evidence_are_written_in_one_transaction(
    authz_api, exam_course, monkeypatch
) -> None:
    """An analysis that claims a run must never exist without its evidence."""
    original = ExamTopicCandidate.__init__

    def explode(self, *args, **kwargs):
        raise RuntimeError("candidate write failed")

    monkeypatch.setattr(ExamTopicCandidate, "__init__", explode)
    try:
        response, provider = run_analysis(authz_api, monkeypatch)
    finally:
        monkeypatch.setattr(ExamTopicCandidate, "__init__", original)

    assert response.status_code >= 500
    assert provider.calls == 1
    assert (
        outputs_of(
            authz_api.session_factory,
            authz_api.a_course_id,
            OUTPUT_TYPE_EXAM_TOPIC_ANALYSIS,
        )
        == []
    )
    with authz_api.session_factory() as session:
        assert session.scalars(select(ExamTopicCandidate)).all() == []


def test_the_persisted_plan_is_a_versioned_document_describing_its_evidence(
    authz_api, exam_course, monkeypatch
) -> None:
    _, created = _analyse_then_plan(authz_api, monkeypatch)
    plan_id = created.json()["data"]["generated_output_id"]

    with authz_api.session_factory() as session:
        row = session.get(GeneratedOutput, plan_id)

    detail = authz_api.client.get(
        f"/api/courses/{authz_api.a_course_id}/generated-outputs/{plan_id}",
        headers=authz_api.authorization_a,
    ).json()["data"]

    assert row.output_type == OUTPUT_TYPE_EXAM_PLAN
    assert detail["content"]["version"] == 1
    assert detail["content"]["output_type"] == "exam_plan"
    assert detail["content"]["fingerprint"]["mastery_user_id"] == authz_api.user_a_id
    assert detail["generation_settings"]["selection_mode"] == "manual"
    assert detail["generation_settings"]["high_priority_topic_keys"] == [
        "dynamic-programming"
    ]
    assert detail["generation_context"]["ranking_engine"] == "deterministic"
    assert detail["generation_context"]["analysis_model_used"] == "ollama:qwen3:8b"
    assert row.created_at is not None


def test_the_generic_generated_output_history_still_reads_exam_mode_rows(
    authz_api, exam_course, monkeypatch
) -> None:
    _analyse_then_plan(authz_api, monkeypatch)

    history = authz_api.client.get(
        f"/api/courses/{authz_api.a_course_id}/generated-outputs",
        headers=authz_api.authorization_a,
    )

    assert history.status_code == 200, history.text
    types = {row["output_type"] for row in history.json()["data"]}
    assert {OUTPUT_TYPE_EXAM_TOPIC_ANALYSIS, OUTPUT_TYPE_EXAM_PLAN} <= types


# --------------------------------------------------------------- credits


def test_an_empty_balance_is_refused_before_the_provider_is_reached(
    authz_api, exam_course, monkeypatch
) -> None:
    set_balance(authz_api.session_factory, authz_api.user_a_id, 0.0)
    before = len(credit_rows(authz_api.session_factory, authz_api.user_a_id))

    response, provider = run_analysis(authz_api, monkeypatch)

    assert response.status_code == 402
    assert response.headers["X-Error-Code"] == AiErrorCode.INSUFFICIENT_CREDITS
    assert provider.calls == 0
    assert balance_of(authz_api.session_factory, authz_api.user_a_id) == 0.0
    assert len(credit_rows(authz_api.session_factory, authz_api.user_a_id)) == before


def test_a_provider_failure_refunds_the_charge_exactly_once(
    authz_api, exam_course, monkeypatch
) -> None:
    before = balance_of(authz_api.session_factory, authz_api.user_a_id)
    provider = install_provider(
        monkeypatch,
        CountingProvider(error=TextGenerationError("the provider is down")),
    )

    response = authz_api.client.post(
        f"/api/courses/{authz_api.a_course_id}/exam-mode/analysis",
        json={},
        headers=authz_api.authorization_a,
    )

    assert response.status_code >= 500
    assert provider.calls == 1
    assert balance_of(authz_api.session_factory, authz_api.user_a_id) == before

    with authz_api.session_factory() as session:
        refunds = session.scalars(
            select(CreditTransaction).where(
                CreditTransaction.user_id == authz_api.user_a_id,
                CreditTransaction.refunds_transaction_id.is_not(None),
            )
        ).all()
        assert len(refunds) == 1
        assert_balance_is_derivable(session, authz_api.user_a_id)


def test_an_unmetered_account_is_never_given_a_fake_finite_balance(
    authz_api, exam_course, monkeypatch
) -> None:
    with authz_api.session_factory() as session:
        # A null balance is what unmetered means; it is never faked with a
        # large number, so the ledger is deliberately left untouched here.
        session.get(User, authz_api.user_a_id).credits = None
        session.commit()

    response, provider = run_analysis(authz_api, monkeypatch)

    assert response.status_code == 200, response.text
    assert provider.calls == 1
    assert balance_of(authz_api.session_factory, authz_api.user_a_id) is None


def test_the_exam_prices_are_served_rather_than_left_for_a_client_to_guess(
    authz_api,
) -> None:
    policy = authz_api.client.get(
        "/api/users/me/credits", headers=authz_api.authorization_a
    ).json()["data"]

    assert policy["generation_costs"]["exam_topic_analysis"] == 1.0
    assert policy["generation_costs"]["exam_topic_analysis_rescan"] == 0.5


# --------------------------------------------------------------- logging


def test_the_usage_log_records_the_feature_without_any_of_its_content(
    authz_api, exam_course, monkeypatch
) -> None:
    run_analysis(
        authz_api,
        monkeypatch,
        payload=analysis_payload(past_exam_questions=[past_exam_question()]),
    )

    with authz_api.session_factory() as session:
        entries = session.scalars(
            select(AiUsageLog).where(
                AiUsageLog.generation_type == "exam_topic_analysis"
            )
        ).all()

    assert len(entries) == 1
    entry = entries[0]
    assert entry.success is True
    assert entry.course_id == authz_api.a_course_id
    assert entry.user_id == authz_api.user_a_id
    assert entry.provider == "ollama"

    forbidden = (
        "Explain breadth-first search",
        "Award marks for the queue invariant",
        "Graph traversal covers BFS",
        "Week 1 Graph Traversal",
    )
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
    for secret in forbidden:
        assert secret not in recorded


def test_a_failed_analysis_logs_a_stable_category_and_no_exception_text(
    authz_api, exam_course, monkeypatch
) -> None:
    install_provider(
        monkeypatch,
        CountingProvider(error=TextGenerationError("connection refused to 10.0.0.7")),
    )

    response = authz_api.client.post(
        f"/api/courses/{authz_api.a_course_id}/exam-mode/analysis",
        json={},
        headers=authz_api.authorization_a,
    )

    assert "10.0.0.7" not in response.text
    with authz_api.session_factory() as session:
        entry = session.scalars(
            select(AiUsageLog).where(
                AiUsageLog.generation_type == "exam_topic_analysis"
            )
        ).all()[-1]

    assert entry.success is False
    assert entry.error_category == "provider_error"
