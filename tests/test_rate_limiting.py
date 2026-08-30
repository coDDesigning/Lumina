from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

import routes.auth as auth_route
import utils.rate_limit as rate_limit_module
from backend.app.config import settings
from backend.app.models import AiUsageLog, RateLimitBucket
from schemas.ai_usage import GenerationType
from utils.rate_limit import check_and_increment


def _set_auth_rate_limit_policy(
    monkeypatch: pytest.MonkeyPatch, **overrides: object
) -> None:
    monkeypatch.setattr(auth_route, "settings", replace(settings, **overrides))


def _set_generation_rate_limit_policy(
    monkeypatch: pytest.MonkeyPatch, **overrides: object
) -> None:
    monkeypatch.setattr(rate_limit_module, "settings", replace(settings, **overrides))


# --- utils.rate_limit.check_and_increment: unit tests -----------------------


def test_check_and_increment_allows_up_to_the_limit(db_session: Session) -> None:
    for _ in range(3):
        decision = check_and_increment(
            db_session, "test:unit:allow", window_seconds=60, limit=3
        )
        assert decision.allowed is True
        assert decision.retry_after_seconds == 0


def test_check_and_increment_rejects_over_the_limit_with_retry_after(
    db_session: Session,
) -> None:
    for _ in range(3):
        check_and_increment(db_session, "test:unit:reject", window_seconds=60, limit=3)

    decision = check_and_increment(
        db_session, "test:unit:reject", window_seconds=60, limit=3
    )

    assert decision.allowed is False
    assert 0 < decision.retry_after_seconds <= 60


def test_check_and_increment_resets_after_window_rollover(db_session: Session) -> None:
    for _ in range(3):
        check_and_increment(
            db_session, "test:unit:rollover", window_seconds=60, limit=3
        )

    bucket = db_session.get(RateLimitBucket, "test:unit:rollover")
    assert bucket is not None
    bucket.window_start = datetime.now(timezone.utc) - timedelta(seconds=61)
    db_session.commit()

    decision = check_and_increment(
        db_session, "test:unit:rollover", window_seconds=60, limit=3
    )

    assert decision.allowed is True


def test_check_and_increment_lockout_blocks_without_bumping_window(
    db_session: Session,
) -> None:
    key = "test:unit:lockout-block"
    for _ in range(2):
        check_and_increment(
            db_session,
            key,
            window_seconds=60,
            limit=2,
            lockout_base_seconds=30,
            lockout_max_seconds=1800,
        )
    # The third call trips the lockout.
    tripped = check_and_increment(
        db_session,
        key,
        window_seconds=60,
        limit=2,
        lockout_base_seconds=30,
        lockout_max_seconds=1800,
    )
    assert tripped.allowed is False
    assert tripped.retry_after_seconds == 30

    bucket = db_session.get(RateLimitBucket, key)
    assert bucket is not None
    count_while_locked = bucket.count

    still_locked = check_and_increment(
        db_session,
        key,
        window_seconds=60,
        limit=2,
        lockout_base_seconds=30,
        lockout_max_seconds=1800,
    )
    assert still_locked.allowed is False

    db_session.refresh(bucket)
    assert bucket.count == count_while_locked


def test_check_and_increment_lockout_escalates_on_repeat_violation(
    db_session: Session,
) -> None:
    key = "test:unit:lockout-escalate"

    def trip_once() -> None:
        for _ in range(3):
            check_and_increment(
                db_session,
                key,
                window_seconds=60,
                limit=2,
                lockout_base_seconds=10,
                lockout_max_seconds=1800,
            )

    trip_once()
    first_bucket = db_session.get(RateLimitBucket, key)
    assert first_bucket is not None
    assert first_bucket.locked_until is not None
    assert first_bucket.violation_streak == 1

    # Expire the lockout without rolling the window: an attacker who keeps
    # hammering the same account inside one abuse window, rather than one
    # who returns after a genuinely quiet window (which resets the streak;
    # see the window-rollover test above).
    first_bucket.locked_until = datetime.now(timezone.utc) - timedelta(seconds=1)
    db_session.commit()

    second = check_and_increment(
        db_session,
        key,
        window_seconds=60,
        limit=2,
        lockout_base_seconds=10,
        lockout_max_seconds=1800,
    )

    assert second.allowed is False
    # Base was 10s; the repeat violation should have doubled it to ~20s.
    assert second.retry_after_seconds >= 19

    second_bucket = db_session.get(RateLimitBucket, key)
    assert second_bucket is not None
    assert second_bucket.violation_streak == 2


def test_check_and_increment_resets_violation_streak_after_clean_window(
    db_session: Session,
) -> None:
    key = "test:unit:lockout-reset"
    for _ in range(3):
        check_and_increment(
            db_session,
            key,
            window_seconds=60,
            limit=2,
            lockout_base_seconds=10,
            lockout_max_seconds=1800,
        )
    bucket = db_session.get(RateLimitBucket, key)
    assert bucket is not None
    assert bucket.violation_streak > 0

    # Let the lockout lapse and land in a clean window.
    bucket.locked_until = datetime.now(timezone.utc) - timedelta(seconds=1)
    bucket.window_start = datetime.now(timezone.utc) - timedelta(seconds=61)
    db_session.commit()

    decision = check_and_increment(
        db_session,
        key,
        window_seconds=60,
        limit=2,
        lockout_base_seconds=10,
        lockout_max_seconds=1800,
    )
    assert decision.allowed is True

    db_session.refresh(bucket)
    assert bucket.violation_streak == 0


def test_rate_limit_key_hashes_and_bounds_length() -> None:
    long_value = "attacker@" + "a" * 500 + ".example.com"
    key = rate_limit_module.rate_limit_key("login:account", long_value)
    assert key.startswith("login:account:")
    digest = key.removeprefix("login:account:")
    assert len(digest) == 64
    assert len(key) <= 255
    # Distinct values produce distinct keys
    assert key != rate_limit_module.rate_limit_key("login:account", long_value + "x")


def test_clear_bucket_removes_entry(db_session: Session) -> None:
    key = "test:unit:clear_me"
    check_and_increment(db_session, key, window_seconds=60, limit=2)
    assert db_session.get(RateLimitBucket, key) is not None

    rate_limit_module.clear(db_session, key)
    assert db_session.get(RateLimitBucket, key) is None


def test_check_and_increment_prunes_stale_buckets(db_session: Session) -> None:
    now = datetime.now(timezone.utc)
    old_time = now - timedelta(days=30)
    stale_bucket = RateLimitBucket(
        key="test:unit:stale",
        window_start=old_time,
        count=5,
        violation_streak=0,
        locked_until=None,
    )
    active_bucket = RateLimitBucket(
        key="test:unit:active",
        window_start=now - timedelta(seconds=10),
        count=2,
        violation_streak=0,
        locked_until=None,
    )
    db_session.add(stale_bucket)
    db_session.add(active_bucket)
    db_session.commit()

    # check_and_increment on a brand new key triggers pruning
    check_and_increment(db_session, "test:unit:brand_new", window_seconds=60, limit=5)

    assert db_session.get(RateLimitBucket, "test:unit:stale") is None
    assert db_session.get(RateLimitBucket, "test:unit:active") is not None


# --- /api/auth/login and /api/auth/register: integration tests --------------


def test_login_rate_limited_per_ip_returns_429(api_context, monkeypatch) -> None:
    _set_auth_rate_limit_policy(
        monkeypatch,
        rate_limit_login_max_attempts=2,
        rate_limit_login_window_seconds=300,
    )

    for _ in range(2):
        response = api_context.client.post(
            "/api/auth/login",
            data={"username": "nobody@example.com", "password": "wrong-password"},
        )
        assert response.status_code == 401

    limited = api_context.client.post(
        "/api/auth/login",
        data={"username": "nobody@example.com", "password": "wrong-password"},
    )

    assert limited.status_code == 429
    assert limited.headers["X-Error-Code"] == "login_rate_limited"
    assert "Retry-After" in limited.headers


def test_login_account_lockout_blocks_failed_attempts_and_allows_correct_password(
    api_context, monkeypatch
) -> None:
    _set_auth_rate_limit_policy(
        monkeypatch,
        rate_limit_login_max_attempts=2,
        rate_limit_login_window_seconds=300,
        rate_limit_lockout_base_seconds=30,
        rate_limit_lockout_max_seconds=1800,
    )

    api_context.client.post(
        "/api/auth/register",
        json={
            "name": "Locked User",
            "email": "locked@example.com",
            "password": "correct-password",
        },
    )

    for _ in range(2):
        response = api_context.client.post(
            "/api/auth/login",
            data={"username": "locked@example.com", "password": "wrong-password"},
        )
        assert response.status_code == 401

    # Third failed attempt is locked out with 429.
    locked = api_context.client.post(
        "/api/auth/login",
        data={"username": "locked@example.com", "password": "wrong-password"},
    )
    assert locked.status_code == 429
    assert locked.headers["X-Error-Code"] == "login_rate_limited"
    assert "Retry-After" in locked.headers


def test_login_successful_clears_account_failure_bucket(
    api_context, monkeypatch
) -> None:
    _set_auth_rate_limit_policy(
        monkeypatch,
        rate_limit_login_max_attempts=5,
        rate_limit_login_window_seconds=300,
        rate_limit_lockout_base_seconds=30,
        rate_limit_lockout_max_seconds=1800,
    )

    api_context.client.post(
        "/api/auth/register",
        json={
            "name": "Legit User",
            "email": "legit@example.com",
            "password": "correct-password",
        },
    )

    for _ in range(2):
        res = api_context.client.post(
            "/api/auth/login",
            data={"username": "legit@example.com", "password": "wrong-password"},
        )
        assert res.status_code == 401

    account_key = rate_limit_module.rate_limit_key("login:account", "legit@example.com")
    with api_context.session_factory() as session:
        bucket = session.get(RateLimitBucket, account_key)
        assert bucket is not None
        assert bucket.count == 2

    # Successful login clears the account bucket
    success = api_context.client.post(
        "/api/auth/login",
        data={"username": "legit@example.com", "password": "correct-password"},
    )
    assert success.status_code == 200

    with api_context.session_factory() as session:
        bucket = session.get(RateLimitBucket, account_key)
        assert bucket is None


def test_login_unknown_account_gets_same_lockout_behavior(
    api_context, monkeypatch
) -> None:
    _set_auth_rate_limit_policy(
        monkeypatch,
        rate_limit_login_max_attempts=2,
        rate_limit_login_window_seconds=300,
        rate_limit_lockout_base_seconds=30,
        rate_limit_lockout_max_seconds=1800,
    )

    for _ in range(2):
        res = api_context.client.post(
            "/api/auth/login",
            data={"username": "ghost@example.com", "password": "wrong-password"},
        )
        assert res.status_code == 401

    locked = api_context.client.post(
        "/api/auth/login",
        data={"username": "ghost@example.com", "password": "wrong-password"},
    )
    assert locked.status_code == 429
    assert locked.headers["X-Error-Code"] == "login_rate_limited"
    assert "Retry-After" in locked.headers


def test_register_rate_limited_per_ip_returns_429(api_context, monkeypatch) -> None:
    _set_generation_rate_limit_policy(
        monkeypatch,
        rate_limit_register_max_attempts=2,
        rate_limit_register_window_seconds=3600,
    )

    for index in range(2):
        response = api_context.client.post(
            "/api/auth/register",
            json={
                "name": f"User {index}",
                "email": f"user{index}@example.com",
                "password": "a-strong-password",
            },
        )
        assert response.status_code == 200

    limited = api_context.client.post(
        "/api/auth/register",
        json={
            "name": "One Too Many",
            "email": "toomany@example.com",
            "password": "a-strong-password",
        },
    )

    assert limited.status_code == 429
    assert limited.headers["X-Error-Code"] == "registration_rate_limited"


def test_client_ip_resolution() -> None:
    from starlette.requests import Request

    # When client is None
    scope = {"type": "http", "client": None}
    req = Request(scope)
    assert rate_limit_module.client_ip(req) == "unknown"

    # When client is provided
    scope = {"type": "http", "client": ("192.168.1.50", 12345)}
    req = Request(scope)
    assert rate_limit_module.client_ip(req) == "192.168.1.50"


# --- generation rate limiting: shared per-user policy -----------------------


def test_generation_rate_limit_rejects_without_charging_credits(
    upload_api, monkeypatch
) -> None:
    import routes.prompt_generator as prompt_generator_route

    class FakeProvider:
        def generate_json(self, prompt: str) -> dict[str, object]:
            return {"generated_prompt": "A generated prompt."}

    monkeypatch.setattr(
        prompt_generator_route,
        "get_text_generation_provider",
        lambda *args, **kwargs: FakeProvider(),
    )
    _set_generation_rate_limit_policy(
        monkeypatch,
        rate_limit_generation_max_attempts=1,
        rate_limit_generation_window_seconds=3600,
    )

    allowed = upload_api.client.post(
        "/api/prompt-generator",
        json={"description": "Generate a concise study guide."},
        headers=upload_api.authorization,
    )
    assert allowed.status_code == 200

    with upload_api.session_factory() as session:
        usage_count_before = session.scalar(
            select(func.count())
            .select_from(AiUsageLog)
            .where(
                AiUsageLog.user_id == upload_api.user_id,
                AiUsageLog.generation_type == GenerationType.PROMPT_GENERATOR.value,
            )
        )

    limited = upload_api.client.post(
        "/api/prompt-generator",
        json={"description": "Generate another prompt."},
        headers=upload_api.authorization,
    )

    assert limited.status_code == 429
    assert limited.headers["X-Error-Code"] == "generation_rate_limited"

    with upload_api.session_factory() as session:
        usage_count_after = session.scalar(
            select(func.count())
            .select_from(AiUsageLog)
            .where(
                AiUsageLog.user_id == upload_api.user_id,
                AiUsageLog.generation_type == GenerationType.PROMPT_GENERATOR.value,
            )
        )
        assert usage_count_after == usage_count_before


def test_cross_feature_generation_exhaustion(upload_api, monkeypatch) -> None:
    import routes.prompt_generator as prompt_generator_route
    import routes.study_guide as study_guide_route

    class FakePromptProvider:
        def generate_json(self, prompt: str) -> dict[str, object]:
            return {"generated_prompt": "A prompt."}

    class FakeStudyGuideProvider:
        def generate_text(self, prompt: str) -> str:
            return "## Study Guide\nContent"

    monkeypatch.setattr(
        prompt_generator_route,
        "get_text_generation_provider",
        lambda *args, **kwargs: FakePromptProvider(),
    )
    monkeypatch.setattr(
        study_guide_route,
        "get_text_generation_provider",
        lambda *args, **kwargs: FakeStudyGuideProvider(),
    )
    _set_generation_rate_limit_policy(
        monkeypatch,
        rate_limit_generation_max_attempts=1,
        rate_limit_generation_window_seconds=3600,
    )

    # 1st request to prompt-generator succeeds and uses the 1 attempt for the user
    allowed = upload_api.client.post(
        "/api/prompt-generator",
        json={"description": "Generate a prompt."},
        headers=upload_api.authorization,
    )
    assert allowed.status_code == 200

    # 2nd request to another route (study guide) is throttled under the same per-user limit
    limited = upload_api.client.post(
        f"/api/courses/{upload_api.course_id}/study-guide",
        json={"topic_focus": "Core Concepts"},
        headers=upload_api.authorization,
    )
    assert limited.status_code == 429
    assert limited.headers["X-Error-Code"] == "generation_rate_limited"
    assert "Retry-After" in limited.headers


def test_different_user_isolation_generation(api_context, monkeypatch) -> None:
    import routes.prompt_generator as prompt_generator_route

    class FakeProvider:
        def generate_json(self, prompt: str) -> dict[str, object]:
            return {"generated_prompt": "A prompt."}

    monkeypatch.setattr(
        prompt_generator_route,
        "get_text_generation_provider",
        lambda *args, **kwargs: FakeProvider(),
    )
    _set_generation_rate_limit_policy(
        monkeypatch,
        rate_limit_generation_max_attempts=1,
        rate_limit_generation_window_seconds=3600,
    )

    password = "A-strong-password-123!"

    # User 1
    api_context.client.post(
        "/api/auth/register",
        json={"name": "User 1", "email": "u1@example.com", "password": password},
    )
    tok1 = api_context.client.post(
        "/api/auth/login",
        data={"username": "u1@example.com", "password": password},
    ).json()["access_token"]
    auth1 = {"Authorization": f"Bearer {tok1}"}

    # User 2
    api_context.client.post(
        "/api/auth/register",
        json={"name": "User 2", "email": "u2@example.com", "password": password},
    )
    tok2 = api_context.client.post(
        "/api/auth/login",
        data={"username": "u2@example.com", "password": password},
    ).json()["access_token"]
    auth2 = {"Authorization": f"Bearer {tok2}"}

    # User 1 spends attempt
    r1 = api_context.client.post(
        "/api/prompt-generator",
        json={"description": "Test"},
        headers=auth1,
    )
    assert r1.status_code == 200

    # User 1 is throttled
    r1_throttled = api_context.client.post(
        "/api/prompt-generator",
        json={"description": "Test"},
        headers=auth1,
    )
    assert r1_throttled.status_code == 429

    # User 2 is not affected
    r2 = api_context.client.post(
        "/api/prompt-generator",
        json={"description": "Test"},
        headers=auth2,
    )
    assert r2.status_code == 200


def test_route_inventory_generation_rate_limiting() -> None:
    from fastapi.routing import APIRoute
    from main import app

    expected_generation_paths = {
        "/api/courses/{course_id}/study-guide",
        "/api/courses/{course_id}/quiz",
        "/api/courses/{course_id}/flashcards",
        "/api/courses/{course_id}/ai-tutor",
        "/api/courses/{course_id}/qa",
        "/api/prompt-generator",
        "/api/courses/{course_id}/reverse-quiz",
        "/api/courses/{course_id}/reverse-quiz/questions",
        "/api/courses/{course_id}/exam-roadmap",
        "/api/courses/{course_id}/exam-mode/analysis",
        "/api/courses/{course_id}/exam-mode/analysis/rescan",
        "/api/courses/{course_id}/exam-mode/topics/{topic_key}/guide",
        "/api/courses/{course_id}/exam-mode/topics/{topic_key}/summary",
        "/api/courses/{course_id}/exam-mode/topics/{topic_key}/practice",
        "/api/courses/{course_id}/exam-mode/topics/{topic_key}/exam",
        "/api/courses/{course_id}/exam-mode/topics/{topic_key}/similar-questions",
        "/api/courses/{course_id}/exam-mode/mock-exam",
        "/api/courses/{course_id}/exam-mode/review-sheet",
    }

    all_routes = []
    for r in app.routes:
        if type(r).__name__ == "_IncludedRouter":
            all_routes.extend(getattr(r, "original_router").routes)
        else:
            all_routes.append(r)

    found_generation_paths = {
        route.path
        for route in all_routes
        if isinstance(route, APIRoute)
        and any(
            getattr(getattr(dep, "dependency", None), "rate_limit_kind", None)
            == "generation"
            for dep in getattr(route, "dependencies", [])
        )
    }

    assert found_generation_paths == expected_generation_paths
    assert len(found_generation_paths) == 17

    # Ensure deterministic plan creation is not in generation rate limit
    plan_route = next(
        r
        for r in all_routes
        if isinstance(r, APIRoute)
        and r.path == "/api/courses/{course_id}/exam-mode/plans"
    )
    assert not any(
        getattr(getattr(dep, "dependency", None), "rate_limit_kind", None)
        == "generation"
        for dep in getattr(plan_route, "dependencies", [])
    )
