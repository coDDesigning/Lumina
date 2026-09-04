from sqlalchemy import select
from backend.app.models import RevokedToken


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

    # Verify token is revoked in db
    with api_context.session_factory() as session:
        # We don't have the jti directly, but we can check if any token was revoked
        revoked = session.execute(select(RevokedToken)).scalars().all()
        assert len(revoked) == 1

    # Verify token no longer works
    me_after = api_context.client.get("/api/auth/me", headers=auth_headers)
    assert me_after.status_code == 401


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
