from datetime import datetime, timedelta, timezone

import pytest

from backend.app.models import AiUsageLog
from services.ai_cost_reporting import build_ai_cost_report


def test_admin_cost_report_aggregates_priced_and_unpriced_successes(authz_api) -> None:
    with authz_api.session_factory() as session:
        session.add_all(
            [
                AiUsageLog(
                    user_id=authz_api.user_a_id,
                    generation_type="study_guide",
                    provider="gemini",
                    model="gemini-2.5-flash",
                    prompt_tokens=100_000,
                    completion_tokens=200_000,
                    total_tokens=300_000,
                    success=True,
                    estimated_cost_usd=0.5,
                    pricing_version="2026-08-24",
                ),
                AiUsageLog(
                    user_id=authz_api.user_b_id,
                    generation_type="quiz",
                    provider="ollama",
                    model="qwen3:8b",
                    prompt_tokens=50,
                    completion_tokens=100,
                    total_tokens=150,
                    success=True,
                ),
                AiUsageLog(
                    user_id=authz_api.user_a_id,
                    generation_type="quiz",
                    provider="gemini",
                    model="gemini-2.5-flash",
                    success=False,
                    error_category="provider_error",
                ),
            ]
        )
        session.commit()

    response = authz_api.client.get(
        "/api/admin/ai-costs?days=30",
        headers=authz_api.authorization_admin,
    )

    assert response.status_code == 200
    report = response.json()["data"]
    assert report["timezone"] == "UTC"
    assert report["totals"] == {
        "successful_generations": 2,
        "prompt_tokens": 100_050,
        "completion_tokens": 200_100,
        "estimated_cost_usd": 0.5,
        "unpriced_generations": 1,
    }
    assert len(report["daily"]) == 2
    assert {row["pricing_version"] for row in report["daily"]} == {
        "2026-08-24",
        None,
    }


def test_cost_report_requires_an_administrator(authz_api) -> None:
    response = authz_api.client.get(
        "/api/admin/ai-costs",
        headers=authz_api.authorization_a,
    )

    assert response.status_code == 403


def test_cost_report_uses_inclusive_start_and_exclusive_end_utc_boundaries(
    authz_api,
) -> None:
    now = datetime(2026, 8, 25, 12, tzinfo=timezone.utc)
    start = datetime(2026, 8, 24, tzinfo=timezone.utc)
    end = datetime(2026, 8, 26, tzinfo=timezone.utc)
    with authz_api.session_factory() as session:
        for created_at, version in (
            (start - timedelta(microseconds=1), "before"),
            (start, "start"),
            (end - timedelta(microseconds=1), "inside"),
            (end, "end"),
        ):
            session.add(
                AiUsageLog(
                    user_id=authz_api.user_a_id,
                    generation_type="quiz",
                    provider="gemini",
                    model="gemini-2.5-flash",
                    prompt_tokens=1,
                    completion_tokens=1,
                    success=True,
                    estimated_cost_usd=0.1,
                    pricing_version=version,
                    created_at=created_at,
                )
            )
        session.commit()

        report = build_ai_cost_report(session, days=2, now=now)

    assert report.start_date.isoformat() == "2026-08-24"
    assert report.end_date.isoformat() == "2026-08-25"
    assert report.totals.successful_generations == 2
    assert {row.pricing_version for row in report.daily} == {"start", "inside"}
    assert [row.date.isoformat() for row in report.daily] == [
        "2026-08-25",
        "2026-08-24",
    ]


@pytest.mark.parametrize("days", [0, 367])
def test_cost_report_rejects_days_outside_supported_range(authz_api, days: int) -> None:
    response = authz_api.client.get(
        f"/api/admin/ai-costs?days={days}",
        headers=authz_api.authorization_admin,
    )

    assert response.status_code == 422
