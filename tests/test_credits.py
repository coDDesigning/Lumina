import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.config import settings
from backend.app.models import Course, DocumentChunk, Quiz, UploadedDocument, User
from schemas.credits import CreditReason
from services import credits as credits_service
from services.text_generation import (
    GenerationMetadata,
    TextGenerationConnectionError,
)
from services.credits import GENERATION_CREDIT_COSTS, CreditService
from services.retrieval_material import MaterialRetrievalError
from tests.conftest import assert_balance_is_derivable, rows, set_balance


class StubProvider:
    def __init__(self, should_fail: bool = False):
        self.should_fail = should_fail
        self.call_count = 0

    def generate_text_with_metadata(self, prompt: str):
        self.call_count += 1
        if self.should_fail:
            raise TextGenerationConnectionError("Provider down")
        return "Answer text", GenerationMetadata(
            provider="gemini",
            model="gemini-3.6-flash",
            total_tokens=10,
            latency_ms=5,
        )

    def generate_text(self, prompt: str):
        text, _ = self.generate_text_with_metadata(prompt)
        return text

    def generate_json_with_metadata(self, prompt: str):
        self.call_count += 1
        if self.should_fail:
            raise TextGenerationConnectionError("Provider down")
        return {
            "title": "Study Guide",
            "summary": "This is a summary of the course materials.",
            "key_points": ["Point 1", "Point 2"],
            "important_terms": [{"term": "Term 1", "definition": "Def 1"}],
            "common_mistakes": [{"mistake": "Mistake 1", "correction": "Fix 1"}],
            "exam_tips": {
                "lecture_based": ["Tip 1"],
                "ai_suggestions": ["Tip 2"],
            },
            "difficulty": {"level": "Medium", "reason": "Moderate depth"},
            "estimated_study_time": "1 hour",
            "prerequisites": ["None"],
            "learning_objectives": ["Obj 1"],
            "coverage": {"status": "Complete", "estimated_completeness": 95},
            "confidence_notes": "High confidence",
        }, GenerationMetadata(
            provider="gemini",
            model="gemini-3.6-flash",
            total_tokens=20,
            latency_ms=8,
        )

    def generate_json(self, prompt: str):
        data, _ = self.generate_json_with_metadata(prompt)
        return data


def _add_material(session_factory, user_id: int, course_id: int, retrieval_env=None):
    """Seed one ready chunk, and index it when the feature reads through retrieval."""
    with session_factory() as session:
        user = session.get(User, user_id)
        course = session.get(Course, course_id)
        doc = UploadedDocument(
            original_file_name="notes.txt",
            file_type="txt",
            mime_type="text/plain",
            file_size=50,
            file_hash="a" * 64,
            uploader=user,
            course=course,
            storage_provider="local:test",
            storage_key=f"{'a' * 64}.txt",
            status="ready",
        )
        session.add(doc)
        session.flush()
        chunk = DocumentChunk(
            document=doc,
            course=course,
            chunk_index=0,
            page_number=None,
            text="This is course content about sorting and trees.",
        )
        session.add(chunk)
        session.flush()
        if retrieval_env is not None:
            retrieval_env.index(session, doc, [chunk])
        session.commit()


def test_user_initial_credits_and_charge(authz_api):
    with authz_api.session_factory() as session:
        user = session.get(User, authz_api.user_a_id)
        assert user.credits == 50.0

        receipt = CreditService.charge(session, user.id, 1.0, source_type="course_qa")
        assert receipt is not None
        session.refresh(user)
        assert user.credits == 49.0

        CreditService.refund(session, receipt)
        session.refresh(user)
        assert user.credits == 50.0


def test_admin_has_unlimited_credits(authz_api):
    with authz_api.session_factory() as session:
        admin = session.get(User, authz_api.admin_id)
        assert admin.credits is None

        receipt = CreditService.charge(session, admin.id, 1.0, source_type="course_qa")
        assert receipt is not None
        assert receipt.is_exempt is True
        session.refresh(admin)
        assert admin.credits is None


def test_insufficient_credits_returns_402(
    authz_api, retrieval_env, monkeypatch: pytest.MonkeyPatch
):
    _add_material(
        authz_api.session_factory,
        authz_api.user_a_id,
        authz_api.a_course_id,
        retrieval_env,
    )

    with authz_api.session_factory() as session:
        user = session.get(User, authz_api.user_a_id)
        user.credits = 0.0
        session.commit()

    client = authz_api.client
    headers = authz_api.authorization_a

    monkeypatch.setattr(
        "routes.course_qa.get_text_generation_provider",
        lambda **kwargs: StubProvider(),
    )

    # Attempt to ask a question with 0 credits
    res = client.post(
        f"/api/courses/{authz_api.a_course_id}/qa",
        json={"question": "What are algorithms?"},
        headers=headers,
    )
    assert res.status_code == 402
    assert "credits" in res.json()["detail"].lower()


def test_failed_generation_refunds_credits(
    authz_api, retrieval_env, monkeypatch: pytest.MonkeyPatch
):
    _add_material(
        authz_api.session_factory,
        authz_api.user_a_id,
        authz_api.a_course_id,
        retrieval_env,
    )

    with authz_api.session_factory() as session:
        user = session.get(User, authz_api.user_a_id)
        user.credits = 10.0
        session.commit()

    client = authz_api.client
    headers = authz_api.authorization_a

    # Provider fails
    monkeypatch.setattr(
        "routes.course_qa.get_text_generation_provider",
        lambda **kwargs: StubProvider(should_fail=True),
    )

    res = client.post(
        f"/api/courses/{authz_api.a_course_id}/qa",
        json={"question": "What is sorting?"},
        headers=headers,
    )
    assert res.status_code == 503

    # Credits must be refunded back to 10.0
    with authz_api.session_factory() as session:
        user = session.get(User, authz_api.user_a_id)
        assert user.credits == 10.0


@pytest.mark.parametrize(
    ("endpoint", "provider_dependency", "material_dependency", "payload"),
    [
        (
            "qa",
            "routes.course_qa.get_text_generation_provider",
            "services.course_qa.CourseQAService.get_course_material",
            {"question": "What is sorting?"},
        ),
        (
            "ai-tutor",
            "routes.ai_tutor.get_text_generation_provider",
            "services.ai_tutor.AiTutorService.get_course_material",
            {"question": "What is sorting?"},
        ),
        (
            "flashcards",
            "routes.flashcard.get_text_generation_provider",
            "services.flashcard.FlashcardService.get_course_material",
            {"topic_focus": "Sorting"},
        ),
        (
            "quiz",
            "routes.quiz.get_text_generation_provider",
            "services.quiz.QuizService.get_course_material",
            {
                "question_count": 1,
                "question_types": ["multiple_choice"],
                "difficulty": "medium",
                "topic_focus": "Sorting",
            },
        ),
        (
            "study-guide",
            "routes.study_guide.get_text_generation_provider",
            "services.study_guide.StudyGuideService.get_course_material",
            {"summary_format": "overview", "topic_focus": "Sorting"},
        ),
    ],
)
def test_retrieval_failure_refunds_generation_charge(
    authz_api,
    retrieval_env,
    monkeypatch: pytest.MonkeyPatch,
    endpoint: str,
    provider_dependency: str,
    material_dependency: str,
    payload: dict,
):
    _add_material(
        authz_api.session_factory,
        authz_api.user_a_id,
        authz_api.a_course_id,
        retrieval_env,
    )
    set_balance(authz_api.session_factory, authz_api.user_a_id, 10.0)
    provider = StubProvider()
    monkeypatch.setattr(provider_dependency, lambda **kwargs: provider)

    def fail_retrieval(*args, **kwargs):
        raise MaterialRetrievalError("Vector store unavailable")

    monkeypatch.setattr(material_dependency, fail_retrieval)

    response = authz_api.client.post(
        f"/api/courses/{authz_api.a_course_id}/{endpoint}",
        json=payload,
        headers=authz_api.authorization_a,
    )

    assert response.status_code == 503
    assert provider.call_count == 0
    with authz_api.session_factory() as session:
        assert session.get(User, authz_api.user_a_id).credits == 10.0
        transactions = rows(session, authz_api.user_a_id)
        assert [row.reason for row in transactions[-2:]] == [
            CreditReason.GENERATION_CHARGE.value,
            CreditReason.GENERATION_REFUND.value,
        ]


@pytest.mark.parametrize(
    ("endpoint", "provider_dependency", "material_dependency", "payload"),
    [
        (
            "qa",
            "routes.course_qa.get_text_generation_provider",
            "services.course_qa.CourseQAService.get_course_material",
            {"question": "What is sorting?"},
        ),
        (
            "ai-tutor",
            "routes.ai_tutor.get_text_generation_provider",
            "services.ai_tutor.AiTutorService.get_course_material",
            {"question": "What is sorting?"},
        ),
        (
            "study-guide",
            "routes.study_guide.get_text_generation_provider",
            "services.study_guide.StudyGuideService.get_course_material",
            {"summary_format": "overview", "topic_focus": "Sorting"},
        ),
    ],
)
def test_unexpected_retrieval_failure_refunds_generation_charge(
    authz_api,
    retrieval_env,
    monkeypatch: pytest.MonkeyPatch,
    endpoint: str,
    provider_dependency: str,
    material_dependency: str,
    payload: dict,
):
    _add_material(
        authz_api.session_factory,
        authz_api.user_a_id,
        authz_api.a_course_id,
        retrieval_env,
    )
    set_balance(authz_api.session_factory, authz_api.user_a_id, 10.0)
    monkeypatch.setattr(provider_dependency, lambda **kwargs: StubProvider())

    def fail_retrieval(*args, **kwargs):
        raise RuntimeError("Unexpected retrieval defect")

    monkeypatch.setattr(material_dependency, fail_retrieval)

    with pytest.raises(RuntimeError, match="Unexpected retrieval defect"):
        authz_api.client.post(
            f"/api/courses/{authz_api.a_course_id}/{endpoint}",
            json=payload,
            headers=authz_api.authorization_a,
        )

    with authz_api.session_factory() as session:
        assert session.get(User, authz_api.user_a_id).credits == 10.0
        transactions = rows(session, authz_api.user_a_id)
        assert [row.reason for row in transactions[-2:]] == [
            CreditReason.GENERATION_CHARGE.value,
            CreditReason.GENERATION_REFUND.value,
        ]


@pytest.mark.parametrize(
    ("endpoint", "provider_dependency"),
    [
        ("qa", "routes.course_qa.get_text_generation_provider"),
        ("ai-tutor", "routes.ai_tutor.get_text_generation_provider"),
    ],
)
def test_conversation_persistence_failure_refunds_credits(
    authz_api,
    retrieval_env,
    monkeypatch: pytest.MonkeyPatch,
    endpoint: str,
    provider_dependency: str,
):
    _add_material(
        authz_api.session_factory,
        authz_api.user_a_id,
        authz_api.a_course_id,
        retrieval_env,
    )
    with authz_api.session_factory() as session:
        user = session.get(User, authz_api.user_a_id)
        user.credits = 10.0
        session.commit()

    monkeypatch.setattr(provider_dependency, lambda **kwargs: StubProvider())
    original_commit = Session.commit
    commit_count = 0

    def fail_conversation_commit(session: Session) -> None:
        nonlocal commit_count
        commit_count += 1
        if commit_count == 2:
            raise RuntimeError("Conversation persistence failed")
        original_commit(session)

    monkeypatch.setattr(Session, "commit", fail_conversation_commit)
    response = authz_api.client.post(
        f"/api/courses/{authz_api.a_course_id}/{endpoint}",
        json={"question": "What is sorting?"},
        headers=authz_api.authorization_a,
    )

    assert response.status_code == 500
    with authz_api.session_factory() as session:
        user = session.get(User, authz_api.user_a_id)
        assert user.credits == 10.0


def test_successful_generation_deducts_credits(
    authz_api, retrieval_env, monkeypatch: pytest.MonkeyPatch
):
    _add_material(
        authz_api.session_factory,
        authz_api.user_a_id,
        authz_api.a_course_id,
        retrieval_env,
    )

    with authz_api.session_factory() as session:
        user = session.get(User, authz_api.user_a_id)
        user.credits = 25.0
        session.commit()

    client = authz_api.client
    headers = authz_api.authorization_a

    monkeypatch.setattr(
        "routes.study_guide.get_text_generation_provider",
        lambda **kwargs: StubProvider(should_fail=False),
    )

    res = client.post(
        f"/api/courses/{authz_api.a_course_id}/study-guide",
        json={
            "summary_format": "overview",
            "topic_focus": "Data Structures",
        },
        headers=headers,
    )
    assert res.status_code == 200

    with authz_api.session_factory() as session:
        user = session.get(User, authz_api.user_a_id)
        assert user.credits == 24.0


def test_an_exhausted_account_recovers_after_an_administrator_change(
    authz_api, retrieval_env, monkeypatch: pytest.MonkeyPatch
):
    """Zero is not a dead end: an administrator can lift an account off it.

    This is the whole point of the administrative path. Before it existed an
    account that spent its allowance stayed at 402 until the calendar moved.
    """
    _add_material(
        authz_api.session_factory,
        authz_api.user_a_id,
        authz_api.a_course_id,
        retrieval_env,
    )
    set_balance(authz_api.session_factory, authz_api.user_a_id, 0.0)

    monkeypatch.setattr(
        "routes.course_qa.get_text_generation_provider",
        lambda **kwargs: StubProvider(),
    )
    question = {"question": "What are algorithms?"}
    url = f"/api/courses/{authz_api.a_course_id}/qa"

    blocked = authz_api.client.post(
        url, json=question, headers=authz_api.authorization_a
    )
    assert blocked.status_code == 402

    recovered = authz_api.client.post(
        "/api/admin/users/owner-a@example.com/credits",
        json={
            "delta": 10,
            "reason": "support_compensation",
            "note": "Support recovery",
        },
        headers=authz_api.authorization_admin,
    )
    assert recovered.status_code == 200
    assert recovered.json()["data"]["user"]["credits"] == 10.0

    allowed = authz_api.client.post(
        url, json=question, headers=authz_api.authorization_a
    )
    assert allowed.status_code == 200

    with authz_api.session_factory() as session:
        assert session.get(User, authz_api.user_a_id).credits == 9.0
        history = rows(session, authz_api.user_a_id)
        assert [row.reason for row in history[-2:]] == [
            CreditReason.SUPPORT_COMPENSATION.value,
            CreditReason.GENERATION_CHARGE.value,
        ]
        assert history[-2].delta == 10.0
        assert history[-2].actor_user_id == authz_api.admin_id
        assert history[-1].delta == -1.0
        assert_balance_is_derivable(session, authz_api.user_a_id)


def test_an_exhausted_account_recovers_when_the_next_month_grants(
    authz_api, retrieval_env, monkeypatch: pytest.MonkeyPatch
):
    """The other way out of zero needs no support action at all.

    The monthly grant is lazy, so it lands on the account's next attempt in a
    new period rather than waiting for a scheduler to have run.
    """
    _add_material(
        authz_api.session_factory,
        authz_api.user_a_id,
        authz_api.a_course_id,
        retrieval_env,
    )
    set_balance(authz_api.session_factory, authz_api.user_a_id, 0.0)

    monkeypatch.setattr(
        "routes.course_qa.get_text_generation_provider",
        lambda **kwargs: StubProvider(),
    )
    question = {"question": "What are algorithms?"}
    url = f"/api/courses/{authz_api.a_course_id}/qa"

    blocked = authz_api.client.post(
        url, json=question, headers=authz_api.authorization_a
    )
    assert blocked.status_code == 402

    monkeypatch.setattr(credits_service, "current_grant_period", lambda: "2099-01")

    allowed = authz_api.client.post(
        url, json=question, headers=authz_api.authorization_a
    )
    assert allowed.status_code == 200

    with authz_api.session_factory() as session:
        expected = settings.credit_periodic_grant - 1.0
        assert session.get(User, authz_api.user_a_id).credits == expected
        history = rows(session, authz_api.user_a_id)
        assert [row.reason for row in history[-2:]] == [
            CreditReason.PERIODIC_GRANT.value,
            CreditReason.GENERATION_CHARGE.value,
        ]
        assert history[-2].delta == settings.credit_periodic_grant
        assert history[-2].grant_period == "2099-01"
        assert_balance_is_derivable(session, authz_api.user_a_id)


class QuizProvider:
    """Returns a quiz whose shape matches whatever question types were asked for."""

    def __init__(self, *question_types: str):
        self.question_types = question_types

    def generate_json_with_metadata(self, prompt: str):
        questions = []
        for index, question_type in enumerate(self.question_types, start=1):
            question: dict = {
                "question_number": index,
                "question_type": question_type,
                "topic": f"Topic {index}",
                "question": f"Question {index}?",
                "difficulty": "medium",
                "explanation": "Because the material says so.",
            }
            if question_type == "multiple_choice":
                question |= {
                    "options": ["A", "B", "C", "D"],
                    "correct_option_index": 0,
                }
            else:
                question |= {
                    "reference_answer": "Ordering lets half the range be discarded."
                }
            questions.append(question)
        return {"title": "Example Quiz", "questions": questions}, GenerationMetadata(
            provider="ollama",
            model="qwen3:8b",
            latency_ms=5,
        )


def _generate_quiz(authz_api, monkeypatch, *question_types: str):
    monkeypatch.setattr(
        "routes.quiz.get_text_generation_provider",
        lambda **kwargs: QuizProvider(*question_types),
    )
    return authz_api.client.post(
        f"/api/courses/{authz_api.a_course_id}/quiz",
        json={
            "question_count": len(question_types),
            "question_types": list(question_types),
            "difficulty": "medium",
            "topic_focus": "All Topics",
        },
        headers=authz_api.authorization_a,
    )


def test_a_quiz_without_open_ended_questions_costs_one_credit(
    authz_api, retrieval_env, monkeypatch: pytest.MonkeyPatch
):
    _add_material(
        authz_api.session_factory,
        authz_api.user_a_id,
        authz_api.a_course_id,
        retrieval_env,
    )

    response = _generate_quiz(authz_api, monkeypatch, "multiple_choice")

    assert response.status_code == 200, response.text
    with authz_api.session_factory() as session:
        assert session.get(User, authz_api.user_a_id).credits == 49.0
        charge = rows(session, authz_api.user_a_id, CreditReason.GENERATION_CHARGE)[-1]
        assert charge.delta == -1.0
        assert charge.source_type == "quiz"
        assert_balance_is_derivable(session, authz_api.user_a_id)


def test_quiz_history_persistence_failure_rolls_back_quiz_and_refunds_credit(
    authz_api, retrieval_env, monkeypatch: pytest.MonkeyPatch
):
    _add_material(
        authz_api.session_factory,
        authz_api.user_a_id,
        authz_api.a_course_id,
        retrieval_env,
    )
    set_balance(authz_api.session_factory, authz_api.user_a_id, 10.0)
    monkeypatch.setattr(
        "routes.quiz.get_text_generation_provider",
        lambda **kwargs: QuizProvider("multiple_choice"),
    )

    def fail_history_write(*args, **kwargs):
        raise RuntimeError("Generated output persistence failed")

    monkeypatch.setattr("routes.quiz.GeneratedOutputService.record", fail_history_write)

    response = authz_api.client.post(
        f"/api/courses/{authz_api.a_course_id}/quiz",
        json={
            "question_count": 1,
            "question_types": ["multiple_choice"],
            "difficulty": "medium",
            "topic_focus": "All Topics",
        },
        headers=authz_api.authorization_a,
    )

    assert response.status_code == 500
    with authz_api.session_factory() as session:
        assert session.get(User, authz_api.user_a_id).credits == 10.0
        assert (
            session.scalars(
                select(Quiz).where(Quiz.course_id == authz_api.a_course_id)
            ).all()
            == []
        )


def test_an_open_ended_quiz_costs_two_credits_because_grading_is_prepaid(
    authz_api, retrieval_env, monkeypatch: pytest.MonkeyPatch
):
    _add_material(
        authz_api.session_factory,
        authz_api.user_a_id,
        authz_api.a_course_id,
        retrieval_env,
    )

    response = _generate_quiz(authz_api, monkeypatch, "open_ended")

    assert response.status_code == 200, response.text
    with authz_api.session_factory() as session:
        assert session.get(User, authz_api.user_a_id).credits == 48.0
        charge = rows(session, authz_api.user_a_id, CreditReason.GENERATION_CHARGE)[-1]
        assert charge.delta == -2.0
        assert charge.source_type == "quiz"
        assert_balance_is_derivable(session, authz_api.user_a_id)


def test_an_open_ended_quiz_is_refused_when_only_one_credit_remains(
    authz_api, retrieval_env, monkeypatch: pytest.MonkeyPatch
):
    """One credit buys a plain quiz but not an open-ended one."""
    _add_material(
        authz_api.session_factory,
        authz_api.user_a_id,
        authz_api.a_course_id,
        retrieval_env,
    )
    set_balance(authz_api.session_factory, authz_api.user_a_id, 1.0)

    response = _generate_quiz(authz_api, monkeypatch, "open_ended")

    assert response.status_code == 402
    with authz_api.session_factory() as session:
        assert session.get(User, authz_api.user_a_id).credits == 1.0
        assert rows(session, authz_api.user_a_id, CreditReason.GENERATION_CHARGE) == []
        assert_balance_is_derivable(session, authz_api.user_a_id)


def test_a_refused_generation_names_its_reason_in_a_header(
    authz_api, retrieval_env, monkeypatch: pytest.MonkeyPatch
):
    """The status alone cannot stay specific if 402 ever covers another state."""
    _add_material(
        authz_api.session_factory,
        authz_api.user_a_id,
        authz_api.a_course_id,
        retrieval_env,
    )
    set_balance(authz_api.session_factory, authz_api.user_a_id, 0.0)
    monkeypatch.setattr(
        "routes.course_qa.get_text_generation_provider",
        lambda **kwargs: StubProvider(),
    )

    response = authz_api.client.post(
        f"/api/courses/{authz_api.a_course_id}/qa",
        json={"question": "What are algorithms?"},
        headers=authz_api.authorization_a,
    )

    assert response.status_code == 402
    assert response.headers["X-Error-Code"] == "insufficient_credits"
    assert isinstance(response.json()["detail"], str)


def test_the_balance_travels_with_the_policy_that_explains_it(authz_api):
    response = authz_api.client.get(
        "/api/users/me/credits", headers=authz_api.authorization_a
    )

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["credits"] == 50.0
    assert data["metering_enabled"] is True
    assert data["monthly_grant"] == settings.credit_periodic_grant
    assert data["balance_cap"] == settings.credit_max_balance
    assert data["generation_costs"] == GENERATION_CREDIT_COSTS
    assert data["generation_costs"]["quiz_open_ended"] == 2.0
    assert data["next_grant_at"] is not None


def test_an_unmetered_account_reports_no_balance_and_no_policy(authz_api):
    response = authz_api.client.get(
        "/api/users/me/credits", headers=authz_api.authorization_admin
    )

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["credits"] is None
    assert data["monthly_grant"] is None
    assert data["balance_cap"] is None
    assert data["next_grant_at"] is None
    assert data["generation_costs"] == {}
