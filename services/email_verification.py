"""Issuing and redeeming the link that proves an address belongs to its account.

Verification exists for two reasons and this module keeps both true. It gives
the account a recovery identity that is worth something, and -- where the
operator pays for inference -- it is what stands between one person and fifty
accounts each holding a free introductory balance. So redemption is the single
place those credits are granted, and it grants them exactly once.

Redemption is one transaction. Consuming the token, stamping
``users.email_verified_at`` and appending the ``INITIAL_GRANT`` ledger row
commit together or not at all, because a consumed token beside an ungranted
balance would leave an account permanently empty with no link left to click.

The plaintext token exists only in the message that carries it. This module
returns it once, to the caller that sends the email, and stores nothing but its
digest. It must never be logged. See docs/authentication.md.
"""

import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from backend.app.config import settings
from backend.app.models import EmailVerificationToken, User
from services.credits import CreditService
from services.email_delivery import EmailMessage, EmailSender, get_email_sender

logger = logging.getLogger(__name__)

# 32 bytes of urandom, URL-safe. Long enough that guessing is not a strategy
# and short enough to survive a mail client wrapping the link.
TOKEN_BYTES = 32

VERIFICATION_SUBJECT = "Verify your Lumina email address"


class InvalidVerificationTokenError(RuntimeError):
    """The presented token is unknown, already used, or past its expiry.

    One error for all three on purpose: telling a caller which of the three it
    was would confirm that a token it guessed once existed.
    """


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _verification_url(token: str) -> str:
    base = settings.app_public_base_url or ""
    return f"{base}/verify-email?token={token}"


def _message_body(name: str, token: str) -> str:
    hours = settings.email_verification_token_ttl_hours
    return (
        f"Hello {name},\n\n"
        "Confirm this address to finish setting up your Lumina account:\n\n"
        f"{_verification_url(token)}\n\n"
        f"The link stops working in {hours} hours. If you did not create a "
        "Lumina account, you can ignore this message.\n"
    )


class EmailVerificationService:
    """Issue, deliver and redeem email verification tokens."""

    @staticmethod
    def issue_token(db: Session, user: User) -> str:
        """Mint one token for ``user`` and return the plaintext exactly once.

        Any outstanding token for the account is consumed first, so a resend
        replaces the previous link rather than adding a second live one. The
        caller commits.
        """
        now = datetime.now(timezone.utc)
        db.execute(
            update(EmailVerificationToken)
            .where(
                EmailVerificationToken.user_id == user.id,
                EmailVerificationToken.consumed_at.is_(None),
            )
            .values(consumed_at=now)
            .execution_options(synchronize_session=False)
        )

        token = secrets.token_urlsafe(TOKEN_BYTES)
        db.add(
            EmailVerificationToken(
                user_id=user.id,
                token_hash=_digest(token),
                expires_at=now
                + timedelta(hours=settings.email_verification_token_ttl_hours),
            )
        )
        db.flush()
        return token

    @staticmethod
    def send_verification_email(user: User, token: str, sender: EmailSender) -> None:
        """Hand the link to the mail seam. Raises ``EmailDeliveryError``."""
        sender.send(
            EmailMessage(
                to_address=user.email,
                subject=VERIFICATION_SUBJECT,
                body=_message_body(user.name, token),
            )
        )

    @staticmethod
    def issue_and_send(
        db: Session, user: User, sender: EmailSender | None = None
    ) -> None:
        """Mint a token, commit it, then try to deliver it.

        The token is committed before the send so a relay that accepts the
        message can never race a link the database has not stored yet. A send
        that fails leaves a usable token behind, which is what makes the resend
        endpoint a real remedy rather than a second chance at the same failure.
        """
        token = EmailVerificationService.issue_token(db, user)
        db.commit()
        EmailVerificationService.send_verification_email(
            user, token, sender or get_email_sender()
        )

    @staticmethod
    def redeem(db: Session, token: str) -> tuple[User, float | None]:
        """Consume ``token``, mark its account verified, and grant its credits.

        Returns the account and the credits this redemption granted, which is
        ``None`` when the account was already verified, is not metered, or has
        been granted its opening balance before.
        """
        now = datetime.now(timezone.utc)
        # The guarded update is the single-use mechanism: the row is claimed by
        # the first statement that finds ``consumed_at`` still null, so a second
        # click updates nothing and is rejected below.
        claimed = db.scalar(
            update(EmailVerificationToken)
            .where(
                EmailVerificationToken.token_hash == _digest(token),
                EmailVerificationToken.consumed_at.is_(None),
                EmailVerificationToken.expires_at > now,
            )
            .values(consumed_at=now)
            .returning(EmailVerificationToken.user_id)
            .execution_options(synchronize_session=False)
        )
        if claimed is None:
            db.rollback()
            raise InvalidVerificationTokenError("Invalid or expired verification link.")

        user = db.scalar(select(User).where(User.id == claimed).with_for_update())
        if user is None:
            db.rollback()
            raise InvalidVerificationTokenError("Invalid or expired verification link.")

        granted: float | None = None
        if user.email_verified_at is None:
            user.email_verified_at = now
            granted = CreditService.grant_initial_credits(db, user)

        db.commit()
        db.refresh(user)
        logger.info(
            "Email address verified",
            extra={"event": "email_verified", "user_id": user.id},
        )
        return user, granted
