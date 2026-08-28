"""The one way transactional mail leaves Lumina.

There is a single seam, :class:`EmailSender`, with two implementations: SMTP
for a deployment that configured a relay, and an unconfigured sender that
refuses loudly for one that did not. Nothing here retries or queues -- a
verification link that fails to send is re-requested by the user through the
resend endpoint, which is a shorter path to a working link than a background
queue that has to be operated.

Nothing in this module logs a recipient address, a subject, or a body. A
failure is reported by exception and logged as a category, because an address
is personal data and a verification link is a live credential.
"""

import logging
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage as MimeMessage
from typing import Protocol

from backend.app.config import settings

logger = logging.getLogger(__name__)


class EmailDeliveryError(RuntimeError):
    """Outbound mail could not be handed to a relay."""


@dataclass(frozen=True)
class EmailMessage:
    to_address: str
    subject: str
    body: str


class EmailSender(Protocol):
    def send(self, message: EmailMessage) -> None: ...


class UnconfiguredEmailSender:
    """The sender a deployment with no relay gets.

    It refuses rather than silently dropping the message, so "the email never
    arrived" is a server-side error somebody can find rather than a mystery.
    """

    def send(self, message: EmailMessage) -> None:
        raise EmailDeliveryError("No outbound mail relay is configured.")


class SmtpEmailSender:
    """Hands one message to an SMTP relay and closes the connection."""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        from_address: str,
        username: str | None = None,
        password: str | None = None,
        use_tls: bool = True,
        timeout_seconds: int = 10,
    ) -> None:
        self._host = host
        self._port = port
        self._from_address = from_address
        self._username = username
        self._password = password
        self._use_tls = use_tls
        self._timeout_seconds = timeout_seconds

    def send(self, message: EmailMessage) -> None:
        mime = MimeMessage()
        mime["From"] = self._from_address
        mime["To"] = message.to_address
        mime["Subject"] = message.subject
        mime.set_content(message.body)

        try:
            with smtplib.SMTP(
                self._host, self._port, timeout=self._timeout_seconds
            ) as client:
                if self._use_tls:
                    client.starttls()
                if self._username and self._password:
                    client.login(self._username, self._password)
                client.send_message(mime)
        except (OSError, smtplib.SMTPException) as exc:
            # The exception text can carry the recipient the relay rejected, so
            # only the class name is kept.
            logger.warning(
                "Outbound mail delivery failed",
                extra={
                    "event": "email_delivery_failed",
                    "exception_type": type(exc).__name__,
                },
            )
            raise EmailDeliveryError("Outbound mail delivery failed.") from exc


def get_email_sender() -> EmailSender:
    """Build the sender the current configuration describes."""
    if not settings.smtp_host or not settings.email_from_address:
        return UnconfiguredEmailSender()
    return SmtpEmailSender(
        host=settings.smtp_host,
        port=settings.smtp_port,
        from_address=settings.email_from_address,
        username=settings.smtp_username,
        password=settings.smtp_password,
        use_tls=settings.smtp_use_tls,
        timeout_seconds=settings.smtp_timeout_seconds,
    )
