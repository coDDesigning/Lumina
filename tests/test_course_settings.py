def test_course_settings_get_and_update(api_context) -> None:
    # 1. Register and login
    api_context.client.post(
        "/api/auth/register",
        json={
            "email": "settings-owner@example.com",
            "name": "Settings Owner",
            "password": "strong-password-123",
        },
    )
    login = api_context.client.post(
        "/api/auth/login",
        data={
            "username": "settings-owner@example.com",
            "password": "strong-password-123",
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
