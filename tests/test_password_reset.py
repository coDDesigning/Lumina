from sqlalchemy import select
from backend.app.models import User, PasswordResetToken


def _register(client, email="test@example.com", password="Password123!"):
    return client.post(
        "/api/auth/register",
        json={"name": "Test User", "email": email, "password": password},
    )


def test_request_password_reset_creates_token(api_context, monkeypatch):
    import services.password_reset as password_reset_service

    class RecordingEmailSender:
        def __init__(self):
            self.messages = []

        def send(self, message):
            self.messages.append(message)

    sender = RecordingEmailSender()
    monkeypatch.setattr(password_reset_service, "get_email_sender", lambda: sender)

    _register(api_context.client, email="reset@example.com")

    response = api_context.client.post(
        "/api/auth/reset-password", json={"email": "reset@example.com"}
    )
    assert response.status_code == 200

    assert len(sender.messages) == 1
    import re

    match = re.search(r"[?&]token=([A-Za-z0-9_-]+)", sender.messages[0].body)
    assert match is not None
    plaintext_token = match.group(1)

    with api_context.session_factory() as session:
        user = session.execute(
            select(User).where(User.email == "reset@example.com")
        ).scalar_one()
        token = session.execute(
            select(PasswordResetToken).where(PasswordResetToken.user_id == user.id)
        ).scalar_one_or_none()
        assert token is not None
        assert token.consumed_at is None

        # Test confirm password reset
        confirm_response = api_context.client.post(
            "/api/auth/reset-password/confirm",
            json={"token": plaintext_token, "new_password": "NewStrongPassword123!"},
        )
        assert confirm_response.status_code == 200

        # Verify the token is consumed
        session.refresh(token)
        assert token.consumed_at is not None
