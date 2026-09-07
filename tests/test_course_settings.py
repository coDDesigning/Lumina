import pytest


def test_course_settings_get_and_update(api_context) -> None:
    # 1. Register and login
    api_context.client.post(
        "/api/auth/register",
        json={
            "email": "settings-owner@example.com",
            "name": "Settings Owner",
            "password": "Strong-password-123!",
        },
    )
    login = api_context.client.post(
        "/api/auth/login",
        data={
            "username": "settings-owner@example.com",
            "password": "Strong-password-123!",
        },
    )
    token = login.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # 2. Create course
    course_res = api_context.client.post(
        "/api/courses",
        json={"title": "Settings Test Course"},
        headers=headers,
    )
    course_id = course_res.json()["data"]["id"]

    # 3. Get default settings
    get_res = api_context.client.get(
        f"/api/courses/{course_id}/settings",
        headers=headers,
    )
    assert get_res.status_code == 200
    data = get_res.json()["data"]
    assert data["study_mode"] == "Exam"
    assert data["difficulty"] == "Adaptive"
    assert data["question_count"] == 10
    assert data["summary_length"] == "Medium"
    assert data["detail_level"] == "Balanced"

    # 4. Update settings
    patch_res = api_context.client.patch(
        f"/api/courses/{course_id}/settings",
        json={
            "difficulty": "Hard",
            "question_count": 20,
            "summary_length": "Long",
            "detail_level": "Detailed",
        },
        headers=headers,
    )
    assert patch_res.status_code == 200
    updated = patch_res.json()["data"]
    assert updated["difficulty"] == "Hard"
    assert updated["question_count"] == 20
    assert updated["summary_length"] == "Long"
    assert updated["detail_level"] == "Detailed"
    assert updated["study_mode"] == "Exam"  # unchanged


def _authenticated_course(api_context) -> tuple[dict, int]:
    api_context.client.post(
        "/api/auth/register",
        json={
            "email": "settings-null-owner@example.com",
            "name": "Settings Null Owner",
            "password": "Strong-password-123!",
        },
    )
    login = api_context.client.post(
        "/api/auth/login",
        data={
            "username": "settings-null-owner@example.com",
            "password": "Strong-password-123!",
        },
    )
    token = login.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    course_res = api_context.client.post(
        "/api/courses",
        json={"title": "Null Guard Test Course"},
        headers=headers,
    )
    course_id = course_res.json()["data"]["id"]
    return headers, course_id


@pytest.mark.parametrize(
    "field",
    ["study_mode", "difficulty", "question_count", "summary_length", "detail_level"],
)
def test_course_settings_update_rejects_explicit_null(api_context, field: str) -> None:
    headers, course_id = _authenticated_course(api_context)

    patch_res = api_context.client.patch(
        f"/api/courses/{course_id}/settings",
        json={field: None},
        headers=headers,
    )
    assert patch_res.status_code == 422, patch_res.text


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("study_mode", "A" * 51),
        ("difficulty", "A" * 51),
        ("summary_length", "A" * 51),
        ("detail_level", "A" * 51),
    ],
)
def test_course_settings_update_rejects_out_of_vocabulary_string(
    api_context, field: str, value: str
) -> None:
    headers, course_id = _authenticated_course(api_context)

    patch_res = api_context.client.patch(
        f"/api/courses/{course_id}/settings",
        json={field: value},
        headers=headers,
    )
    assert patch_res.status_code == 422, patch_res.text


def test_admin_support_view_get_settings_does_not_persist_row(authz_api) -> None:
    from sqlalchemy import func, select
    from backend.app.models import CourseSettings

    # 1. Course A has zero CourseSettings rows before any settings request
    with authz_api.session_factory() as session:
        initial_count = session.scalar(
            select(func.count(CourseSettings.id)).where(
                CourseSettings.course_id == authz_api.a_course_id
            )
        )
        assert initial_count == 0

    # 2. Non-owner administrator opens support view and reads settings
    admin_get_res = authz_api.client.get(
        f"/api/courses/{authz_api.a_course_id}/settings",
        headers=authz_api.authorization_admin,
    )
    assert admin_get_res.status_code == 200, admin_get_res.text
    data = admin_get_res.json()["data"]
    assert data["study_mode"] == "Exam"
    assert data["difficulty"] == "Adaptive"
    assert data["question_count"] == 10
    assert data["summary_length"] == "Medium"
    assert data["detail_level"] == "Balanced"

    # 3. Reading settings as admin must NOT insert or commit any row
    with authz_api.session_factory() as session:
        post_read_count = session.scalar(
            select(func.count(CourseSettings.id)).where(
                CourseSettings.course_id == authz_api.a_course_id
            )
        )
        assert post_read_count == 0

    # 4. Course owner write/PATCH can still persist settings on demand
    owner_patch_res = authz_api.client.patch(
        f"/api/courses/{authz_api.a_course_id}/settings",
        json={"question_count": 25},
        headers=authz_api.authorization_a,
    )
    assert owner_patch_res.status_code == 200, owner_patch_res.text
    updated = owner_patch_res.json()["data"]
    assert updated["question_count"] == 25

    with authz_api.session_factory() as session:
        persisted = session.scalar(
            select(CourseSettings).where(
                CourseSettings.course_id == authz_api.a_course_id
            )
        )
        assert persisted is not None
        assert persisted.question_count == 25
