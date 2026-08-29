"""Fixed-window rate limiting and progressive lockout for abuse-prone routes.

One ``RateLimitBucket`` row per key (see ``backend/app/models.py``), keyed
however the caller wants: by IP, by account, by user+feature. A window
rollover resets the counter in place rather than inserting a new row, so the
table's size tracks the number of active keys, not request volume.

A key already in lockout is rejected immediately without bumping its window,
so retries during a lockout do not themselves extend it -- only a fresh
violation after the lockout expires grows the next one, capped at
``lockout_max_seconds``. This module is the only reader/writer of
``rate_limit_buckets``. See docs/rate_limiting.md.
"""

import hashlib
import logging
import math
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy import delete, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.config import settings
from backend.app.database import begin_serialized_write, get_db
from backend.app.models import RateLimitBucket
from backend.app.observability import emit_emf_metrics
from schemas.user import UserResponse
from utils.deps import get_current_user
from utils.exceptions import TooManyRequestsException

MAX_LOCKOUT_DOUBLINGS = 6
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    retry_after_seconds: int


def rate_limit_key(control: str, value: str) -> str:
    """Build a fixed-length key without retaining the identifying value."""
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    return f"{control}:{digest}"


def get_rate_limit_db(
    request_db: Annotated[Session, Depends(get_db)],
) -> Iterator[Session]:
    """Give abuse controls a transaction isolated from domain persistence."""
    db = Session(
        bind=request_db.get_bind(),
        expire_on_commit=False,
        autoflush=False,
    )
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _begin_write(db: Session) -> None:
    if not db.in_transaction():
        begin_serialized_write(db)


def _retention_seconds() -> int:
    longest_policy = max(
        settings.rate_limit_login_window_seconds,
        settings.rate_limit_register_window_seconds,
        settings.rate_limit_generation_window_seconds,
        settings.rate_limit_verification_window_seconds,
        settings.rate_limit_password_reset_window_seconds,
        settings.rate_limit_lockout_max_seconds,
    )
    return longest_policy * 2


def _prune_stale_buckets(db: Session, now: datetime) -> None:
    cutoff = now - timedelta(seconds=_retention_seconds())
    db.execute(
        delete(RateLimitBucket).where(
            RateLimitBucket.window_start < cutoff,
            or_(
                RateLimitBucket.locked_until.is_(None),
                RateLimitBucket.locked_until <= now,
            ),
        )
    )


def check_and_increment(
    db: Session,
    key: str,
    *,
    window_seconds: int,
    limit: int,
    lockout_base_seconds: int | None = None,
    lockout_max_seconds: int | None = None,
) -> RateLimitDecision:
    """Count one request against ``key``'s current window and decide.

    Passing both ``lockout_base_seconds`` and ``lockout_max_seconds`` turns on
    progressive lockout: exceeding the limit locks the key out for an
    exponentially growing, capped duration instead of just the rest of the
    current window, and a repeat violation after a lockout expires grows the
    next one further. Omit them to reject only for the current window --
    what per-IP and per-user-feature keys use. Account-keyed login limits are
    the one dimension that opts into lockout, since an attacker who knows a
    real email can otherwise keep spending the rest of every window against
    it indefinitely.
    """
    lockout_enabled = (
        lockout_base_seconds is not None and lockout_max_seconds is not None
    )
    now = datetime.now(timezone.utc)
    _begin_write(db)

    bucket = db.scalar(
        select(RateLimitBucket).where(RateLimitBucket.key == key).with_for_update()
    )
    if bucket is None:
        _prune_stale_buckets(db, now)
        bucket = RateLimitBucket(
            key=key,
            window_start=now,
            count=0,
            violation_streak=0,
            locked_until=None,
        )
        db.add(bucket)
        try:
            db.flush()
        except IntegrityError:
            # Lost a race to create this key; the winner's row now exists.
            db.rollback()
            _begin_write(db)
            bucket = db.scalar(
                select(RateLimitBucket)
                .where(RateLimitBucket.key == key)
                .with_for_update()
            )

    if bucket.locked_until is not None and bucket.locked_until > now:
        db.commit()
        return RateLimitDecision(
            allowed=False,
            retry_after_seconds=max(
                1, math.ceil((bucket.locked_until - now).total_seconds())
            ),
        )
    # Any lockout has lapsed by this point; clear the stale marker. Left
    # alone otherwise, since expiry alone doesn't mean the abuse stopped --
    # only a full quiet window below does.
    bucket.locked_until = None

    if now - bucket.window_start >= timedelta(seconds=window_seconds):
        bucket.window_start = now
        bucket.count = 0
        # A whole window with no request landing here at all is what counts
        # as quiet. Resetting on every individual allowed request instead
        # would let a paced attacker -- exactly `limit` requests per window,
        # violating once more each time -- stay at the base lockout forever.
        bucket.violation_streak = 0

    bucket.count += 1
    allowed = bucket.count <= limit

    if allowed:
        db.commit()
        return RateLimitDecision(allowed=True, retry_after_seconds=0)

    if lockout_enabled:
        multiplier = 2 ** min(bucket.violation_streak, MAX_LOCKOUT_DOUBLINGS)
        lockout_seconds = min(lockout_base_seconds * multiplier, lockout_max_seconds)
        bucket.locked_until = now + timedelta(seconds=lockout_seconds)
        bucket.violation_streak += 1
        retry_after = lockout_seconds
    else:
        elapsed = (now - bucket.window_start).total_seconds()
        retry_after = max(1, math.ceil(window_seconds - elapsed))

    db.commit()
    return RateLimitDecision(allowed=False, retry_after_seconds=retry_after)


def enforce(
    db: Session,
    key: str,
    *,
    window_seconds: int,
    limit: int,
    lockout_base_seconds: int | None = None,
    lockout_max_seconds: int | None = None,
    error_code: str = "rate_limited",
    control: str = "generic",
    feature: str | None = None,
) -> None:
    """Raise ``TooManyRequestsException`` if ``key`` is over its limit."""
    decision = check_and_increment(
        db,
        key,
        window_seconds=window_seconds,
        limit=limit,
        lockout_base_seconds=lockout_base_seconds,
        lockout_max_seconds=lockout_max_seconds,
    )
    if not decision.allowed:
        dimensions = {"Control": control, "ErrorCode": error_code}
        if feature is not None:
            dimensions["Feature"] = feature
        logger.warning(
            "Rate limit rejected request",
            extra={
                "event": "rate_limit_rejected",
                "error_code": error_code,
                "rate_limit_control": control,
                "rate_limit_feature": feature,
                "retry_after_seconds": decision.retry_after_seconds,
            },
        )
        emit_emf_metrics(
            {"RateLimitRejections": 1},
            dimensions=dimensions,
            namespace="Lumina/API",
        )
        raise TooManyRequestsException(
            "Too many requests. Try again later.",
            retry_after_seconds=decision.retry_after_seconds,
            error_code=error_code,
        )


def clear(db: Session, key: str) -> None:
    """Forget a failure bucket after successful authentication."""
    _begin_write(db)
    db.execute(delete(RateLimitBucket).where(RateLimitBucket.key == key))
    db.commit()


def client_ip(request: Request) -> str:
    """Best-effort caller IP for keying per-IP limits.

    ``request.client.host`` is the peer address Starlette resolves the
    connection to. Behind the hosted ALB this is only the real client address
    if the ASGI server is configured to trust and parse ``X-Forwarded-For``
    from that proxy (uvicorn ``--proxy-headers`` with a trusted-host list);
    this module does not parse forwarded headers itself, so per-IP limits are
    effectively a single shared bucket for all hosted traffic until that
    proxy trust is configured. See docs/rate_limiting.md.
    """
    return request.client.host if request.client else "unknown"


def rate_limit_register(
    request: Request,
    db: Annotated[Session, Depends(get_rate_limit_db)],
) -> None:
    """Per-IP registration throttle, composed as a route dependency."""
    enforce(
        db,
        rate_limit_key("register:ip", client_ip(request)),
        window_seconds=settings.rate_limit_register_window_seconds,
        limit=settings.rate_limit_register_max_attempts,
        error_code="registration_rate_limited",
        control="registration_ip",
    )


def rate_limit_verification(
    request: Request,
    db: Annotated[Session, Depends(get_rate_limit_db)],
) -> None:
    """Per-IP throttle for issuing and redeeming verification links.

    One key covers both the resend and the redeem route, because they are two
    halves of the same abuse: resends are outbound mail somebody else pays for,
    and redemptions are guesses at a token. Keying by IP rather than by account
    is deliberate -- the address in a resend request is unauthenticated and an
    attacker chooses it freely, so an account key would let them lock a victim
    out of their own verification email.
    """
    enforce(
        db,
        rate_limit_key("verification:ip", client_ip(request)),
        window_seconds=settings.rate_limit_verification_window_seconds,
        limit=settings.rate_limit_verification_max_attempts,
        error_code="verification_rate_limited",
        control="verification_ip",
    )


def rate_limit_generation(feature: str):
    """Per-user-per-feature generation throttle, behind the credit gate.

    Runs as a route dependency, so a rejection raises before the route body
    (and therefore before ``CreditService.charge``) ever executes -- a
    throttled request never spends credit.
    """

    def _dependency(
        current_user: Annotated[UserResponse, Depends(get_current_user)],
        db: Annotated[Session, Depends(get_rate_limit_db)],
    ) -> None:
        enforce(
            db,
            f"generation:user:{current_user.id}",
            window_seconds=settings.rate_limit_generation_window_seconds,
            limit=settings.rate_limit_generation_max_attempts,
            error_code="generation_rate_limited",
            control="generation_user",
            feature=feature,
        )

    _dependency.rate_limit_kind = "generation"  # type: ignore[attr-defined]
    _dependency.rate_limit_feature = feature  # type: ignore[attr-defined]
    return _dependency


def rate_limit_password_reset(
    request: Request,
    db: Annotated[Session, Depends(get_rate_limit_db)],
) -> None:
    """Per-IP throttle for issuing password reset links."""
    enforce(
        db,
        rate_limit_key("password_reset:ip", client_ip(request)),
        window_seconds=settings.rate_limit_password_reset_window_seconds,
        limit=settings.rate_limit_password_reset_max_attempts,
        error_code="password_reset_rate_limited",
        control="password_reset_ip",
    )
