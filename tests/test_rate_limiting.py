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


def test_login_rate_limited_per_account_locks_out_correct_password(
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

    # Even the correct password is rejected while the account is locked out.
    locked = api_context.client.post(
        "/api/auth/login",
        data={"username": "locked@example.com", "password": "correct-password"},
    )

    assert locked.status_code == 429
    assert locked.headers["X-Error-Code"] == "login_rate_limited"


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


# --- generation rate limiting: doesn't charge credits ------------------------


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
        # The throttled request never reached the route body, so it recorded
        # no additional usage log and (by the same reasoning) never charged.
        assert usage_count_after == usage_count_before
