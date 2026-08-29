"""Issuing and redeeming password reset links.

This module provides the ability to securely issue single-use password reset
links and redeem them to rotate credentials. It shares its design with the
email verification flow: tokens are bearer credentials stored only as a digest,
and redemption is a single guarded transaction using `consumed_at`.

See docs/authentication.md.
"""

import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from backend.app.config import settings
from backend.app.models import PasswordResetToken, User
from services.email_delivery import EmailMessage, EmailSender, get_email_sender

logger = logging.getLogger(__name__)

TOKEN_BYTES = 32
RESET_SUBJECT = "Reset your Lumina password"


class InvalidPasswordResetTokenError(RuntimeError):
    """The presented token is unknown, already used, or past its expiry.

    One error for all three on purpose.
    """


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _reset_url(token: str) -> str:
    base = settings.app_public_base_url or ""
    return f"{base}/reset-password?token={token}"


def _message_body(name: str, token: str) -> str:
    minutes = settings.password_reset_token_ttl_minutes
    return (
        f"Hello {name},\n\n"
        "We received a request to reset your Lumina password. "
        "Click the link below to choose a new one:\n\n"
        f"{_reset_url(token)}\n\n"
        f"The link stops working in {minutes} minutes. If you did not request "
        "a password reset, you can safely ignore this message.\n"
    )


class PasswordResetService:
    """Issue, deliver and redeem password reset tokens."""

    @staticmethod
    def issue_token(db: Session, user: User) -> str:
        """Mint one token for ``user`` and return the plaintext exactly once.

        Outstanding tokens are consumed first.
        """
        now = datetime.now(timezone.utc)
        db.execute(
            update(PasswordResetToken)
            .where(
                PasswordResetToken.user_id == user.id,
                PasswordResetToken.consumed_at.is_(None),
            )
            .values(consumed_at=now)
            .execution_options(synchronize_session=False)
        )

        token = secrets.token_urlsafe(TOKEN_BYTES)
        db.add(
            PasswordResetToken(
                user_id=user.id,
                token_hash=_digest(token),
                expires_at=now
                + timedelta(minutes=settings.password_reset_token_ttl_minutes),
            )
        )
        db.flush()
        return token

    @staticmethod
    def send_reset_email(user: User, token: str, sender: EmailSender) -> None:
        """Hand the link to the mail seam."""
        sender.send(
            EmailMessage(
                to_address=user.email,
                subject=RESET_SUBJECT,
                body=_message_body(user.name, token),
            )
        )

    @staticmethod
    def issue_and_send(
        db: Session, user: User, sender: EmailSender | None = None
    ) -> None:
        """Mint a token, commit it, then try to deliver it."""
        token = PasswordResetService.issue_token(db, user)
        db.commit()
        PasswordResetService.send_reset_email(user, token, sender or get_email_sender())

    @staticmethod
    def verify_token(db: Session, token: str) -> User:
        """Verify the token and return the associated user, without consuming it.

        This allows the frontend to check if a token is still valid before
        prompting the user for a new password.
        """
        now = datetime.now(timezone.utc)
        user_id = db.scalar(
            select(PasswordResetToken.user_id).where(
                PasswordResetToken.token_hash == _digest(token),
                PasswordResetToken.consumed_at.is_(None),
                PasswordResetToken.expires_at > now,
            )
        )
        if user_id is None:
            raise InvalidPasswordResetTokenError(
                "Invalid or expired password reset link."
            )

        user = db.scalar(select(User).where(User.id == user_id))
        if user is None:
            raise InvalidPasswordResetTokenError(
                "Invalid or expired password reset link."
            )

        return user

    @staticmethod
    def redeem(db: Session, token: str) -> User:
        """Consume ``token`` and return the associated user.

        It is the caller's responsibility to change the password and commit.
        """
        now = datetime.now(timezone.utc)
        claimed = db.scalar(
            update(PasswordResetToken)
            .where(
                PasswordResetToken.token_hash == _digest(token),
                PasswordResetToken.consumed_at.is_(None),
                PasswordResetToken.expires_at > now,
            )
            .values(consumed_at=now)
            .returning(PasswordResetToken.user_id)
            .execution_options(synchronize_session=False)
        )
        if claimed is None:
            db.rollback()
            raise InvalidPasswordResetTokenError(
                "Invalid or expired password reset link."
            )

        user = db.scalar(select(User).where(User.id == claimed).with_for_update())
        if user is None:
            db.rollback()
            raise InvalidPasswordResetTokenError(
                "Invalid or expired password reset link."
            )

        return user
