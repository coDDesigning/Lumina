from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from backend.app.models import AiUsageLog
from schemas.ai_usage import AiCostDailyRow, AiCostReport, AiCostTotals


def build_ai_cost_report(
    db: Session,
    *,
    days: int,
    now: datetime | None = None,
) -> AiCostReport:
    """Aggregate immutable successful-generation estimates into UTC days."""
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    end_date = current.date()
    start_date = end_date - timedelta(days=days - 1)
    start_at = datetime.combine(start_date, time.min, tzinfo=timezone.utc)
    end_at = datetime.combine(
        end_date + timedelta(days=1), time.min, tzinfo=timezone.utc
    )

    dialect = db.get_bind().dialect.name
    day = (
        func.date(func.timezone("UTC", AiUsageLog.created_at))
        if dialect == "postgresql"
        else func.date(AiUsageLog.created_at)
    ).label("usage_date")
    rows = db.execute(
        select(
            day,
            AiUsageLog.provider,
            AiUsageLog.model,
            AiUsageLog.pricing_version,
            func.count(AiUsageLog.id).label("successful_generations"),
            func.coalesce(func.sum(AiUsageLog.prompt_tokens), 0).label("prompt_tokens"),
            func.coalesce(func.sum(AiUsageLog.completion_tokens), 0).label(
                "completion_tokens"
            ),
            func.coalesce(func.sum(AiUsageLog.estimated_cost_usd), 0.0).label(
                "estimated_cost_usd"
            ),
            func.sum(case((AiUsageLog.estimated_cost_usd.is_(None), 1), else_=0)).label(
                "unpriced_generations"
            ),
        )
        .where(
            AiUsageLog.success.is_(True),
            AiUsageLog.created_at >= start_at,
            AiUsageLog.created_at < end_at,
        )
        .group_by(
            day,
            AiUsageLog.provider,
            AiUsageLog.model,
            AiUsageLog.pricing_version,
        )
        .order_by(day.desc(), AiUsageLog.provider, AiUsageLog.model)
    ).all()

    daily: list[AiCostDailyRow] = []
    for row in rows:
        usage_date = row.usage_date
        if isinstance(usage_date, str):
            usage_date = date.fromisoformat(usage_date)
        daily.append(
            AiCostDailyRow(
                date=usage_date,
                provider=row.provider,
                model=row.model,
                pricing_version=row.pricing_version,
                successful_generations=row.successful_generations,
                prompt_tokens=row.prompt_tokens,
                completion_tokens=row.completion_tokens,
                estimated_cost_usd=round(float(row.estimated_cost_usd), 12),
                unpriced_generations=row.unpriced_generations,
            )
        )

    totals = AiCostTotals(
        successful_generations=sum(row.successful_generations for row in daily),
        prompt_tokens=sum(row.prompt_tokens for row in daily),
        completion_tokens=sum(row.completion_tokens for row in daily),
        estimated_cost_usd=round(sum(row.estimated_cost_usd for row in daily), 12),
        unpriced_generations=sum(row.unpriced_generations for row in daily),
    )
    return AiCostReport(
        start_date=start_date,
        end_date=end_date,
        totals=totals,
        daily=daily,
    )
