"""The single writer for every credit balance change.

Nothing outside this module may modify ``users.credits``. Every mutation here
updates the balance and appends one ``credit_transactions`` row inside the same
database transaction, which is what keeps the ledger able to explain the
balance: for any account with a non-null balance, the balance equals the sum of
that account's deltas.

A null balance means the account is not metered, which is how administrators
have always worked. ``CREDIT_METERING_ENABLED=false`` extends the same idea to a
whole deployment: nothing is charged, refunded, or granted and no rows are
written, rather than faking exemption with an enormous balance.

The ledger is append-only. This module offers no way to update or delete a
transaction; a mistake is corrected by recording an opposing one.

See docs/credits.md for the policy these mechanics implement.
"""

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.config import settings
from backend.app.models import CreditTransaction, User
from schemas.credits import (
    ADMIN_CREDIT_REASONS,
    POSITIVE_ONLY_ADMIN_REASONS,
    CreditActorType,
    CreditReason,
)
from utils.exceptions import BadRequestException, NotFoundException

DEFAULT_HISTORY_LIMIT = 50
MAX_HISTORY_LIMIT = 200


@dataclass(frozen=True)
class ChargeReceipt:
    """Proof that a charge happened, and the handle a refund reverses.

    ``transaction_id`` is ``None`` when the account was exempt, so no credit
    moved and there is nothing for a refund to reverse.
    """

    user_id: int
    amount: float
    transaction_id: int | None = None

    @property
    def is_exempt(self) -> bool:
        return self.transaction_id is None


@dataclass(frozen=True)
class CreditActor:
    """Who caused a transaction, captured server-side and never from a client."""

    actor_type: CreditActorType
    user_id: int | None = None
    label: str | None = None

    @classmethod
    def system(cls) -> "CreditActor":
        return cls(actor_type=CreditActorType.SYSTEM)

    @classmethod
    def for_user(cls, user: User) -> "CreditActor":
        return cls(actor_type=CreditActorType.USER, user_id=user.id, label=user.email)

    @classmethod
    def admin(cls, user_id: int, label: str | None) -> "CreditActor":
        return cls(actor_type=CreditActorType.ADMIN, user_id=user_id, label=label)


def current_grant_period() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


class CreditService:
    """Charge, refund, replenish and administer credits, always with a ledger entry."""

    @staticmethod
    def metering_enabled() -> bool:
        return settings.credit_metering_enabled

    @staticmethod
    def is_metered(user: User) -> bool:
        """A metered account has metering switched on and a real balance."""
        return settings.credit_metering_enabled and user.credits is not None

    @staticmethod
    def reported_balance(user: User) -> float | None:
        """The balance as the API should present it.

        A deployment with metering disabled reports null, the same value that
        has always meant unmetered, so every existing surface renders it as
        unlimited. The stored column is left alone, so re-enabling metering
        restores the real balances rather than resetting anyone.
        """
        if not settings.credit_metering_enabled:
            return None
        return user.credits

    @staticmethod
    def charge(
        db: Session,
        user_id: int,
        amount: float,
        *,
        source_type: str,
        source_id: int | None = None,
    ) -> "ChargeReceipt | None":
        """Deduct credits, returning None when the balance cannot cover them.

        A refused charge changes nothing at all: no balance mutation and no
        ledger row, because the ledger records balance changes rather than
        attempts.
        """
        if amount <= 0:
            # A non-positive charge would add credit while filing it as spending.
            raise ValueError("A credit charge must be positive")

        user = db.get(User, user_id)
        if user is None:
            return None
        if not CreditService.is_metered(user):
            return ChargeReceipt(user_id=user_id, amount=amount)

        CreditService.ensure_current_period_grant(db, user_id)

        result = db.execute(
            update(User)
            .where(
                User.id == user_id,
                User.credits.is_not(None),
                User.credits >= amount,
            )
            .values(credits=User.credits - amount)
        )
        if result.rowcount == 0:
            # The guarded update matched nothing, so there is no balance change
            # to undo. Rolling back here would instead discard whatever the
            # caller had pending, which is not this function's to throw away.
            return None

        transaction = CreditService._record(
            db,
            user_id=user_id,
            delta=-amount,
            reason=CreditReason.GENERATION_CHARGE,
            actor=CreditActor.for_user(user),
            source_type=source_type,
            source_id=source_id,
        )
        transaction_id = transaction.id
        db.commit()
        return ChargeReceipt(
            user_id=user_id, amount=amount, transaction_id=transaction_id
        )

    @staticmethod
    def refund(db: Session, receipt: "ChargeReceipt | None") -> None:
        """Reverse one charge, at most once.

        A refund is not trimmed to the balance ceiling: it returns credit the
        account already held, so capping it would quietly confiscate credit the
        user never spent.
        """
        if receipt is None or receipt.is_exempt:
            return

        already_refunded = db.scalar(
            select(CreditTransaction.id).where(
                CreditTransaction.refunds_transaction_id == receipt.transaction_id
            )
        )
        if already_refunded is not None:
            return

        user = db.get(User, receipt.user_id)
        if user is None or not CreditService.is_metered(user):
            return

        original = db.get(CreditTransaction, receipt.transaction_id)
        try:
            db.execute(
                update(User)
                .where(User.id == receipt.user_id, User.credits.is_not(None))
                .values(credits=User.credits + receipt.amount)
            )
            CreditService._record(
                db,
                user_id=receipt.user_id,
                delta=receipt.amount,
                reason=CreditReason.GENERATION_REFUND,
                actor=CreditActor.system(),
                source_type=original.source_type if original else None,
                source_id=original.source_id if original else None,
                refunds_transaction_id=receipt.transaction_id,
            )
            db.commit()
        except IntegrityError:
            db.rollback()

    @staticmethod
    def ensure_current_period_grant(db: Session, user_id: int) -> None:
        """Grant this calendar month's credits if they are still owed.

        This is the whole periodic mechanism. There is no scheduler: the grant
        is evaluated when a balance is charged or read, so a month can never be
        missed by a job that failed to run, and the unique constraint on
        ``(user_id, grant_period)`` makes granting twice impossible however many
        requests race.

        A balance already at the ceiling is left alone and writes no row, so the
        account becomes eligible again the moment spending drops it back under.

        The write is guarded rather than the commit alone: the duplicate is
        rejected when the row is flushed, which is before any commit, so a
        caller that lost the race must see the same quiet skip as one that
        never started it.
        """
        user = db.get(User, user_id)
        if user is None or not CreditService.is_metered(user) or user.is_banned:
            return

        period = current_grant_period()
        if CreditService._period_grant_id(db, user_id, period) is not None:
            return

        headroom = settings.credit_max_balance - (user.credits or 0.0)
        delta = min(settings.credit_periodic_grant, headroom)
        if delta <= 0:
            return

        try:
            db.execute(
                update(User)
                .where(User.id == user_id, User.credits.is_not(None))
                .values(credits=User.credits + delta)
            )
            CreditService._record(
                db,
                user_id=user_id,
                delta=delta,
                reason=CreditReason.PERIODIC_GRANT,
                actor=CreditActor.system(),
                grant_period=period,
            )
            db.commit()
        except IntegrityError:
            db.rollback()
            db.expire_all()

    @staticmethod
    def _period_grant_id(db: Session, user_id: int, period: str) -> int | None:
        """The id of this account's grant for one period, if it already has one."""
        return db.scalar(
            select(CreditTransaction.id).where(
                CreditTransaction.user_id == user_id,
                CreditTransaction.grant_period == period,
            )
        )

    @staticmethod
    def apply_admin_change(
        db: Session,
        user_id: int,
        delta: float,
        *,
        reason: CreditReason,
        actor: "CreditActor",
        note: str | None = None,
    ) -> CreditTransaction:
        """Move a balance deliberately, in either direction, never below zero.

        One entry point rather than a grant and an adjust: they differed only by
        the reason they chose for the caller, and a stated reason is more honest
        than an inferred one. The balance ceiling does not apply, because a
        manual change is a decision rather than an allowance.

        The sign rules are checked here as well as at the request boundary. This
        module is the only writer of ``users.credits``, and a single writer that
        trusts its callers is not one.
        """
        if not settings.credit_metering_enabled:
            raise BadRequestException("Credit metering is disabled for this deployment")
        if reason not in ADMIN_CREDIT_REASONS:
            raise BadRequestException(
                "A manual credit change must state an administrative reason"
            )
        if delta == 0:
            raise BadRequestException("A credit adjustment must change the balance")
        if reason in POSITIVE_ONLY_ADMIN_REASONS and delta < 0:
            raise BadRequestException(f"The reason {reason.value} may only add credits")

        user = db.get(User, user_id)
        if user is None:
            raise NotFoundException("User not found")
        if user.credits is None:
            raise BadRequestException(
                "This account is not metered and holds no credit balance"
            )
        if user.credits + delta < 0:
            raise BadRequestException(
                "A credit adjustment cannot take the balance below zero"
            )

        db.execute(
            update(User)
            .where(User.id == user_id, User.credits.is_not(None))
            .values(credits=User.credits + delta)
        )
        transaction = CreditService._record(
            db,
            user_id=user_id,
            delta=delta,
            reason=reason,
            actor=actor,
            note=note,
        )
        db.commit()
        db.refresh(transaction)
        return transaction

    @staticmethod
    def build_initial_grant(user: User) -> CreditTransaction | None:
        """The registration grant, as a row that flushes with the user itself.

        Returned rather than written so the caller can attach it to the pending
        user and let one flush persist both, which keeps the grant and the
        account it belongs to in a single transaction through the existing
        registration retry path.

        The period is stamped with the registration month so the unique
        constraint alone stops a brand-new account also collecting that month's
        periodic grant.

        This is written whether or not metering is enabled, because it explains
        the balance the column actually received. Disabling metering suppresses
        charging, not the record of where a stored balance came from, so a
        deployment that later switches metering on still finds the ledger and
        the balance in agreement.
        """
        if user.credits is None:
            return None
        return CreditTransaction(
            delta=user.credits,
            balance_after=user.credits,
            reason=CreditReason.INITIAL_GRANT.value,
            actor_type=CreditActorType.SYSTEM.value,
            grant_period=current_grant_period(),
        )

    @staticmethod
    def record_metering_reset(db: Session, user: User) -> None:
        """Re-baseline an account that just re-entered metering.

        An account leaving metering keeps its history but stops having a
        derivable balance. When it comes back, its stored deltas no longer sum
        to the balance it was given, so this writes the one row that makes them
        agree again.
        """
        if user.credits is None:
            return

        recorded = (
            db.scalar(
                select(func.coalesce(func.sum(CreditTransaction.delta), 0.0)).where(
                    CreditTransaction.user_id == user.id
                )
            )
            or 0.0
        )
        delta = user.credits - recorded
        if delta == 0:
            return

        CreditService._record(
            db,
            user_id=user.id,
            delta=delta,
            reason=CreditReason.METERING_RESET,
            actor=CreditActor.system(),
            balance_after=user.credits,
        )

    @staticmethod
    def list_transactions(
        db: Session,
        user_id: int,
        *,
        limit: int = DEFAULT_HISTORY_LIMIT,
        offset: int = 0,
    ) -> list[CreditTransaction]:
        """Most recent transactions first."""
        bounded_limit = max(1, min(limit, MAX_HISTORY_LIMIT))
        return list(
            db.scalars(
                select(CreditTransaction)
                .where(CreditTransaction.user_id == user_id)
                .order_by(CreditTransaction.id.desc())
                .limit(bounded_limit)
                .offset(max(0, offset))
            ).all()
        )

    @staticmethod
    def _record(
        db: Session,
        *,
        user_id: int,
        delta: float,
        reason: CreditReason,
        actor: "CreditActor",
        source_type: str | None = None,
        source_id: int | None = None,
        refunds_transaction_id: int | None = None,
        grant_period: str | None = None,
        note: str | None = None,
        balance_after: float | None = None,
    ) -> CreditTransaction:
        """Append one row. The only place a CreditTransaction is constructed."""
        if balance_after is None:
            balance_after = db.scalar(select(User.credits).where(User.id == user_id))
        transaction = CreditTransaction(
            user_id=user_id,
            delta=delta,
            balance_after=balance_after if balance_after is not None else 0.0,
            reason=reason.value,
            actor_type=actor.actor_type.value,
            actor_user_id=actor.user_id,
            actor_label=actor.label,
            source_type=source_type,
            source_id=source_id,
            refunds_transaction_id=refunds_transaction_id,
            grant_period=grant_period,
            note=note,
        )
        db.add(transaction)
        db.flush()
        return transaction
