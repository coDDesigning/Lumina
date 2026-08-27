"""Bounded retention cleanup for privacy-safe AI usage telemetry."""

import argparse
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from backend.app.config import settings
from backend.app.database import SessionLocal
from backend.app.models import AiUsageLog
from backend.app.observability import configure_logging, emit_emf_metrics

logger = logging.getLogger(__name__)
SessionFactory = Callable[[], Session]


@dataclass(frozen=True)
class CleanupReport:
    rows_matched: int
    rows_deleted: int
    batches: int

    def summary(self) -> str:
        return (
            f"matched={self.rows_matched} deleted={self.rows_deleted} "
            f"batches={self.batches}"
        )


def run_cleanup(
    *,
    session_factory: SessionFactory = SessionLocal,
    retention_days: int | None = None,
    batch_size: int | None = None,
    now: datetime | None = None,
    dry_run: bool = False,
) -> CleanupReport:
    if retention_days is None:
        retention_days = settings.ai_usage_retention_days
    if batch_size is None:
        batch_size = settings.ai_usage_cleanup_batch_size
    if retention_days <= 0:
        raise ValueError("retention_days must be a positive integer")
    if batch_size <= 0:
        raise ValueError("batch_size must be a positive integer")

    current_time = now or datetime.now(timezone.utc)
    if current_time.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    cutoff = current_time.astimezone(timezone.utc) - timedelta(days=retention_days)

    if dry_run:
        with session_factory() as session:
            matched = session.scalar(
                select(func.count(AiUsageLog.id)).where(AiUsageLog.created_at < cutoff)
            )
        return CleanupReport(rows_matched=matched or 0, rows_deleted=0, batches=0)

    deleted_total = 0
    batches = 0
    while True:
        with session_factory() as session:
            identifiers = list(
                session.scalars(
                    select(AiUsageLog.id)
                    .where(AiUsageLog.created_at < cutoff)
                    .order_by(AiUsageLog.created_at, AiUsageLog.id)
                    .limit(batch_size)
                ).all()
            )
            if not identifiers:
                break
            result = session.execute(
                delete(AiUsageLog).where(AiUsageLog.id.in_(identifiers))
            )
            session.commit()
            deleted_total += result.rowcount or 0
            batches += 1

    report = CleanupReport(
        rows_matched=deleted_total,
        rows_deleted=deleted_total,
        batches=batches,
    )
    emit_emf_metrics(
        {"AiUsageRowsDeleted": report.rows_deleted},
        dimensions={"Service": "ai_usage_cleanup", "Environment": settings.app_env},
    )
    logger.info("AI usage cleanup finished: %s", report.summary())
    return report


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Delete masked AI usage telemetry beyond its retention period."
    )
    parser.add_argument("--retention-days", type=int, default=None)
    parser.add_argument("--batch-size", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    arguments = parser.parse_args(argv)
    if arguments.retention_days is not None and arguments.retention_days <= 0:
        parser.error("--retention-days must be a positive integer")
    if arguments.batch_size is not None and arguments.batch_size <= 0:
        parser.error("--batch-size must be a positive integer")

    configure_logging(service="maintenance", environment=settings.app_env)
    report = run_cleanup(
        retention_days=arguments.retention_days,
        batch_size=arguments.batch_size,
        dry_run=arguments.dry_run,
    )
    print(report.summary())


if __name__ == "__main__":
    main()
