import pytest

from backend.app.config import settings
from backend.app.models import Course, DocumentChunk, UploadedDocument, User
from schemas.credits import CreditReason
from services import credits as credits_service
from services.text_generation import (
    GenerationMetadata,
    TextGenerationConnectionError,
)
from services.credits import CreditService
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


def test_insufficient_credits_returns_402(authz_api, monkeypatch: pytest.MonkeyPatch):
    _add_material(authz_api.session_factory, authz_api.user_a_id, authz_api.a_course_id)

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


def test_failed_generation_refunds_credits(authz_api, monkeypatch: pytest.MonkeyPatch):
    _add_material(authz_api.session_factory, authz_api.user_a_id, authz_api.a_course_id)

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
    authz_api, monkeypatch: pytest.MonkeyPatch
):
    """Zero is not a dead end: an administrator can lift an account off it.

    This is the whole point of the administrative path. Before it existed an
    account that spent its allowance stayed at 402 until the calendar moved.
    """
    _add_material(authz_api.session_factory, authz_api.user_a_id, authz_api.a_course_id)
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
    authz_api, monkeypatch: pytest.MonkeyPatch
):
    """The other way out of zero needs no support action at all.

    The monthly grant is lazy, so it lands on the account's next attempt in a
    new period rather than waiting for a scheduler to have run.
    """
    _add_material(authz_api.session_factory, authz_api.user_a_id, authz_api.a_course_id)
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
