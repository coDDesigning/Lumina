from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.app.models import RevokedToken
from main import app
from services.token_revocation import TokenRevocationService


def _register(client, email="test@example.com", password="Password123!"):
    return client.post(
        "/api/auth/register",
        json={"name": "Test User", "email": email, "password": password},
    )


def test_logout_revokes_token(api_context):
    _register(api_context.client, email="logout@example.com", password="Password123!")
    # Log in
    login = api_context.client.post(
        "/api/auth/login",
        data={
            "username": "logout@example.com",
            "password": "Password123!",
        },
    )
    assert login.status_code == 200
    token = login.json()["access_token"]

    auth_headers = {"Authorization": f"Bearer {token}"}

    # Verify token works
    me = api_context.client.get("/api/auth/me", headers=auth_headers)
    assert me.status_code == 200

    # Logout
    logout = api_context.client.post("/api/auth/logout", headers=auth_headers)
    assert logout.status_code == 200
    repeated_logout = api_context.client.post("/api/auth/logout", headers=auth_headers)
    assert repeated_logout.status_code == 200

    # Verify token is revoked in db
    with api_context.session_factory() as session:
        # We don't have the jti directly, but we can check if any token was revoked
        revoked = session.execute(select(RevokedToken)).scalars().all()
        assert len(revoked) == 1

    # Verify token no longer works
    me_after = api_context.client.get("/api/auth/me", headers=auth_headers)
    assert me_after.status_code == 401


def test_logout_does_not_report_success_when_revocation_fails(api_context, monkeypatch):
    _register(api_context.client, email="logout-failure@example.com")
    login = api_context.client.post(
        "/api/auth/login",
        data={
            "username": "logout-failure@example.com",
            "password": "Password123!",
        },
    )
    token = login.json()["access_token"]
    auth_headers = {"Authorization": f"Bearer {token}"}

    def fail_revocation(*args, **kwargs):
        raise RuntimeError("simulated revocation persistence failure")

    monkeypatch.setattr(TokenRevocationService, "revoke_token", fail_revocation)
    safe_client = TestClient(app, raise_server_exceptions=False)
    try:
        logout = safe_client.post("/api/auth/logout", headers=auth_headers)
        me_after = safe_client.get("/api/auth/me", headers=auth_headers)
    finally:
        safe_client.close()

    assert logout.status_code == 500
    assert me_after.status_code == 200


def test_logout_keeps_an_invalid_token_as_an_idempotent_noop(api_context):
    response = api_context.client.post(
        "/api/auth/logout",
        headers={"Authorization": "Bearer not-a-jwt"},
    )

    assert response.status_code == 200
    assert response.json() == {"message": "Logged out successfully"}


def test_password_change_invalidates_old_tokens(api_context):
    _register(api_context.client, email="change@example.com", password="Password123!")
    # Log in
    login = api_context.client.post(
        "/api/auth/login",
        data={
            "username": "change@example.com",
            "password": "Password123!",
        },
    )
    assert login.status_code == 200
    token = login.json()["access_token"]

    auth_headers = {"Authorization": f"Bearer {token}"}

    # Change password
    change = api_context.client.put(
        "/api/users/me/password",
        headers=auth_headers,
        json={
            "current_password": "Password123!",
            "new_password": "NewStrongPassword456!",
        },
    )
    assert change.status_code == 200
    assert change.json() == {
        "success": True,
        "message": "Password changed",
        "data": None,
    }

    # Old token should now be invalid
    me_after = api_context.client.get("/api/auth/me", headers=auth_headers)
    assert me_after.status_code == 401
