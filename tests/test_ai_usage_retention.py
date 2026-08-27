from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from backend.app.models import AiUsageLog, Role, User
from workers.ai_usage_cleanup import main, run_cleanup


def _seed_usage(
    session_factory: sessionmaker[Session],
    timestamps: list[datetime],
) -> tuple[int, list[int]]:
    with session_factory() as session:
        role = session.scalar(select(Role).where(Role.name == "user"))
        if role is None:
            role = Role(name="user")
            session.add(role)
            session.flush()
        user = User(
            name="Retention User",
            email="retention@example.com",
            password_hash="hash",
            role=role,
        )
        session.add(user)
        session.flush()
        rows = [
            AiUsageLog(
                user_id=user.id,
                generation_type="study_guide",
                provider="gemini",
                model="gemini-test",
                success=True,
                created_at=timestamp,
            )
            for timestamp in timestamps
        ]
        session.add_all(rows)
        session.commit()
        return user.id, [row.id for row in rows]


def test_cleanup_deletes_only_rows_older_than_the_retention_boundary(
    session_factory: sessionmaker[Session],
) -> None:
    now = datetime(2026, 8, 27, 12, tzinfo=timezone.utc)
    user_id, identifiers = _seed_usage(
        session_factory,
        [now - timedelta(days=91), now - timedelta(days=90), now - timedelta(days=1)],
    )

    report = run_cleanup(
        session_factory=session_factory,
        retention_days=90,
        batch_size=1,
        now=now,
    )

    assert report.rows_matched == 1
    assert report.rows_deleted == 1
    assert report.batches == 1
    with session_factory() as session:
        assert session.get(AiUsageLog, identifiers[0]) is None
        assert session.get(AiUsageLog, identifiers[1]) is not None
        assert session.get(AiUsageLog, identifiers[2]) is not None
        assert session.get(User, user_id) is not None


def test_cleanup_commits_bounded_batches(
    session_factory: sessionmaker[Session],
) -> None:
    now = datetime(2026, 8, 27, 12, tzinfo=timezone.utc)
    _seed_usage(session_factory, [now - timedelta(days=100)] * 5)

    report = run_cleanup(
        session_factory=session_factory,
        retention_days=90,
        batch_size=2,
        now=now,
    )

    assert report.rows_deleted == 5
    assert report.batches == 3
    with session_factory() as session:
        assert session.scalar(select(AiUsageLog.id)) is None


def test_cleanup_dry_run_reports_without_deleting(
    session_factory: sessionmaker[Session],
) -> None:
    now = datetime(2026, 8, 27, 12, tzinfo=timezone.utc)
    _, identifiers = _seed_usage(
        session_factory,
        [now - timedelta(days=100), now - timedelta(days=1)],
    )

    report = run_cleanup(
        session_factory=session_factory,
        retention_days=90,
        now=now,
        dry_run=True,
    )

    assert report.rows_matched == 1
    assert report.rows_deleted == 0
    assert report.batches == 0
    with session_factory() as session:
        assert all(session.get(AiUsageLog, identifier) for identifier in identifiers)


@pytest.mark.parametrize(
    ("retention_days", "batch_size"),
    [(0, 100), (-1, 100), (90, 0), (90, -1)],
)
def test_cleanup_rejects_invalid_bounds(
    session_factory: sessionmaker[Session],
    retention_days: int,
    batch_size: int,
) -> None:
    with pytest.raises(ValueError):
        run_cleanup(
            session_factory=session_factory,
            retention_days=retention_days,
            batch_size=batch_size,
        )


def test_cleanup_rejects_naive_clock(session_factory: sessionmaker[Session]) -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        run_cleanup(
            session_factory=session_factory,
            now=datetime(2026, 8, 27),
        )


def test_cleanup_cli_rejects_invalid_retention() -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["--retention-days", "0"])

    assert exc_info.value.code == 2
