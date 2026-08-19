"""API and ownership tests for student profile knowledge (SCRUM-78)."""

from utils.security import create_access_token


def _auth_header(email: str) -> dict[str, str]:
    token = create_access_token({"sub": email})
    return {"Authorization": f"Bearer {token}"}


def test_unauthenticated_requests_are_denied(api_context) -> None:
    client = api_context.client

    assert client.get("/api/profile-knowledge/").status_code == 401
    assert (
        client.post(
            "/api/profile-knowledge/",
            json={"topic": "Physics", "detail": "Mechanics"},
        ).status_code
        == 401
    )
    assert (
        client.post(
            "/api/profile-knowledge/import",
            json={"items": [{"topic": "Physics", "detail": "Mechanics"}]},
        ).status_code
        == 401
    )
    assert client.get("/api/profile-knowledge/1").status_code == 401
    assert (
        client.put(
            "/api/profile-knowledge/1",
            json={"topic": "Physics"},
        ).status_code
        == 401
    )
    assert client.delete("/api/profile-knowledge/1").status_code == 401


def test_profile_knowledge_crud_lifecycle(authz_api) -> None:
    client = authz_api.client
    auth_a = authz_api.authorization_a

    # 1. Create
    create_res = client.post(
        "/api/profile-knowledge/",
        headers=auth_a,
        json={
            "topic": "Linear Algebra",
            "detail": "Understands matrix rank, determinants, and eigenvalues.",
        },
    )
    assert create_res.status_code == 201, create_res.text
    created = create_res.json()["data"]
    item_id = created["id"]
    assert created["topic"] == "Linear Algebra"
    assert (
        created["detail"] == "Understands matrix rank, determinants, and eigenvalues."
    )
    assert created["user_id"] == authz_api.user_a_id
    assert created["created_at"] is not None
    assert created["updated_at"] is not None

    # 2. Read single
    get_res = client.get(f"/api/profile-knowledge/{item_id}", headers=auth_a)
    assert get_res.status_code == 200, get_res.text
    assert get_res.json()["data"]["id"] == item_id

    # 3. List
    list_res = client.get("/api/profile-knowledge/", headers=auth_a)
    assert list_res.status_code == 200, list_res.text
    items = list_res.json()["data"]
    assert any(item["id"] == item_id for item in items)

    # 4. Update
    update_res = client.put(
        f"/api/profile-knowledge/{item_id}",
        headers=auth_a,
        json={
            "topic": "Advanced Linear Algebra",
            "detail": "Also familiar with SVD and Jordan canonical form.",
        },
    )
    assert update_res.status_code == 200, update_res.text
    updated = update_res.json()["data"]
    assert updated["topic"] == "Advanced Linear Algebra"
    assert updated["detail"] == "Also familiar with SVD and Jordan canonical form."

    # 5. Delete
    del_res = client.delete(f"/api/profile-knowledge/{item_id}", headers=auth_a)
    assert del_res.status_code == 200, del_res.text
    assert del_res.json()["data"]["id"] == item_id

    # 6. Read after delete returns 404
    get_again = client.get(f"/api/profile-knowledge/{item_id}", headers=auth_a)
    assert get_again.status_code == 404


def test_profile_knowledge_bulk_import(authz_api) -> None:
    client = authz_api.client
    auth_a = authz_api.authorization_a

    import_res = client.post(
        "/api/profile-knowledge/import",
        headers=auth_a,
        json={
            "items": [
                {"topic": "Calculus", "detail": "Derivatives and integrals."},
                {"topic": "Probability", "detail": "Bayes rule and distributions."},
            ]
        },
    )
    assert import_res.status_code == 201, import_res.text
    data = import_res.json()["data"]
    assert len(data) == 2
    assert data[0]["topic"] == "Calculus"
    assert data[1]["topic"] == "Probability"

    list_res = client.get("/api/profile-knowledge/", headers=auth_a)
    assert list_res.status_code == 200
    topics = [item["topic"] for item in list_res.json()["data"]]
    assert "Calculus" in topics
    assert "Probability" in topics


def test_profile_knowledge_cross_user_isolation(authz_api) -> None:
    client = authz_api.client
    auth_a = authz_api.authorization_a
    auth_b = authz_api.authorization_b

    # User A creates an item
    create_res = client.post(
        "/api/profile-knowledge/",
        headers=auth_a,
        json={"topic": "User A Secret", "detail": "Private background info."},
    )
    assert create_res.status_code == 201
    item_id = create_res.json()["data"]["id"]

    # User B cannot read User A's item (returns 404, not 403, preventing enumeration)
    assert (
        client.get(f"/api/profile-knowledge/{item_id}", headers=auth_b).status_code
        == 404
    )

    # User B cannot update User A's item
    assert (
        client.put(
            f"/api/profile-knowledge/{item_id}",
            headers=auth_b,
            json={"topic": "Hacked"},
        ).status_code
        == 404
    )

    # User B cannot delete User A's item
    assert (
        client.delete(f"/api/profile-knowledge/{item_id}", headers=auth_b).status_code
        == 404
    )

    # User B's list does not show User A's item
    b_list = client.get("/api/profile-knowledge/", headers=auth_b).json()["data"]
    assert not any(item["id"] == item_id for item in b_list)


def test_course_hard_delete_preserves_profile_knowledge(
    authz_api,
) -> None:
    client = authz_api.client
    auth_a = authz_api.authorization_a

    # User A creates a knowledge item
    pk_res = client.post(
        "/api/profile-knowledge/",
        headers=auth_a,
        json={
            "topic": "General Relativity",
            "detail": "Tensor calculus and Einstein field equations.",
        },
    )
    assert pk_res.status_code == 201
    pk_id = pk_res.json()["data"]["id"]

    # User A creates a course
    course_res = client.post(
        "/api/courses/",
        headers=auth_a,
        json={"title": "Temporary Physics Course"},
    )
    assert course_res.status_code == 201
    course_id = course_res.json()["data"]["id"]

    # Hard delete the course
    del_course_res = client.delete(
        f"/api/courses/{course_id}?hard_delete=true",
        headers=auth_a,
    )
    assert del_course_res.status_code == 200

    # Verify course is deleted
    assert client.get(f"/api/courses/{course_id}", headers=auth_a).status_code == 404

    # Verify profile knowledge survived completely intact
    pk_after = client.get(f"/api/profile-knowledge/{pk_id}", headers=auth_a)
    assert pk_after.status_code == 200
    assert pk_after.json()["data"]["topic"] == "General Relativity"


def test_profile_knowledge_validation(authz_api) -> None:
    client = authz_api.client
    auth_a = authz_api.authorization_a

    # Empty / whitespace topic
    assert (
        client.post(
            "/api/profile-knowledge/",
            headers=auth_a,
            json={"topic": "   ", "detail": "Valid detail"},
        ).status_code
        == 422
    )

    # Empty / whitespace detail
    assert (
        client.post(
            "/api/profile-knowledge/",
            headers=auth_a,
            json={"topic": "Valid Topic", "detail": "   "},
        ).status_code
        == 422
    )

    # NUL byte rejection
    assert (
        client.post(
            "/api/profile-knowledge/",
            headers=auth_a,
            json={"topic": "Topic\x00Name", "detail": "Detail"},
        ).status_code
        == 422
    )

    # Bulk import empty items list
    assert (
        client.post(
            "/api/profile-knowledge/import",
            headers=auth_a,
            json={"items": []},
        ).status_code
        == 422
    )
