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


def test_password_reset_latency_not_distinguishable_by_address_existence(
    api_context, monkeypatch
):
    import time
    from fastapi.testclient import TestClient
    import services.password_reset as password_reset_service

    class TimingASGIApp:
        def __init__(self, app):
            self.app = app
            self.last_latency = None

        async def __call__(self, scope, receive, send):
            if scope["type"] != "http":
                await self.app(scope, receive, send)
                return
            start = time.perf_counter()

            async def tracking_send(message):
                if message["type"] == "http.response.body" and not message.get(
                    "more_body", False
                ):
                    self.last_latency = time.perf_counter() - start
                await send(message)

            await self.app(scope, receive, tracking_send)

    class SlowEmailSender:
        def __init__(self, delay_seconds: float = 0.2):
            self.delay_seconds = delay_seconds
            self.messages = []

        def send(self, message):
            time.sleep(self.delay_seconds)
            self.messages.append(message)

    slow_sender = SlowEmailSender(delay_seconds=0.2)
    monkeypatch.setattr(password_reset_service, "get_email_sender", lambda: slow_sender)

    _register(api_context.client, email="known_reset@example.com")

    client = TestClient(TimingASGIApp(api_context.client.app))

    # Unknown address
    res_unknown = client.post(
        "/api/auth/reset-password", json={"email": "nobody@example.com"}
    )
    assert res_unknown.status_code == 200
    unknown_latency = client.app.last_latency
    assert unknown_latency is not None

    # Known address
    res_known = client.post(
        "/api/auth/reset-password", json={"email": "known_reset@example.com"}
    )
    assert res_known.status_code == 200
    known_latency = client.app.last_latency
    assert known_latency is not None

    # Response latency must not be blocked by the 200ms SMTP delay
    assert known_latency < 0.1
    assert abs(known_latency - unknown_latency) < 0.05
    assert len(slow_sender.messages) == 1
