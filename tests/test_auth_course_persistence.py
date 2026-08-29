from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import date
from threading import Barrier
from urllib.parse import quote

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

import services.user as user_service_module
from backend.app.config import (
    APP_ENV_PRODUCTION,
    MODE_HOSTED,
    MODE_SELF_HOSTED,
    settings,
)
from backend.app.models import (
    Course,
    DocumentChunk,
    DocumentPage,
    DocumentVisual,
    UploadedDocument,
    User,
)
from schemas.course import CourseCreate, CourseResponse
from schemas.user import Role, UserCreate, UserResponse, UserUpdate
from services.course import CourseService
from services.user import UserService
from utils.exceptions import BadRequestException
from utils.security import create_access_token


def test_registration_login_and_admin_course_creation_persist(api_context) -> None:
    first_registration = api_context.client.post(
        "/api/auth/register",
        json={
            "name": "First User",
            "email": "first@example.com",
            "password": "strong-password",
        },
    )
    second_registration = api_context.client.post(
        "/api/auth/register",
        json={
            "name": "Second User",
            "email": "second@example.com",
            "password": "strong-password",
        },
    )

    assert first_registration.status_code == 200
    assert first_registration.json() == {
        "message": "User registered successfully",
        "user_email": "first@example.com",
        "role": "admin",
        # This deployment does not ask, so the account is told it need not act.
        "email_verification_required": False,
        "is_email_verified": False,
    }
    assert second_registration.status_code == 200
    assert second_registration.json()["role"] == "user"

    with api_context.session_factory() as session:
        users = list(session.scalars(select(User).order_by(User.id)).all())
        assert len(users) == 2
        assert users[0].role.name == "admin"
        assert users[0].is_initial_admin is True
        assert users[0].credits is None
        assert users[1].role.name == "user"
        assert users[1].is_initial_admin is None
        assert users[1].credits == 50.0
        admin_id = users[0].id

    login = api_context.client.post(
        "/api/auth/login",
        data={"username": "first@example.com", "password": "strong-password"},
    )

    assert login.status_code == 200
    assert login.json()["token_type"] == "bearer"
    token = login.json()["access_token"]
    me = api_context.client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert me.status_code == 200
    assert me.json()["id"] == admin_id
    assert me.json()["role"] == "admin"
    assert me.json()["credits"] is None

    course_payload = {
        "title": "Persisted Course",
        "description": "Created through the authenticated API",
        "semester": "Fall",
        "exam_date": "2026-06-15",
    }
    created = api_context.client.post(
        "/api/courses/",
        json=course_payload,
        headers={"Authorization": f"Bearer {token}"},
    )

    assert created.status_code == 201
    assert created.json()["success"] is True
    assert created.json()["data"]["title"] == course_payload["title"]
    course_id = created.json()["data"]["id"]

    with api_context.session_factory() as session:
        persisted = session.get(Course, course_id)
        assert persisted is not None
        assert persisted.owner_id == admin_id
        assert persisted.title == course_payload["title"]
        assert persisted.description == course_payload["description"]
        assert persisted.semester == course_payload["semester"]
        assert persisted.exam_date == date.fromisoformat(course_payload["exam_date"])
        assert persisted.is_deleted is False
        assert session.scalar(select(func.count()).select_from(Course)) == 1


@pytest.mark.database_contract
def test_user_delete_cascades_loaded_document_content(
    db_session,
    model_graph,
) -> None:
    document = UploadedDocument(
        original_file_name="cascade.txt",
        file_type="txt",
        mime_type="text/plain",
        file_size=7,
        file_hash="a" * 64,
        uploader=model_graph.user,
        course=model_graph.course,
        storage_provider="local:test",
        storage_key="cascade.txt",
    )
    chunk = DocumentChunk(
        document=document,
        course=model_graph.course,
        chunk_index=0,
        page_number=None,
        text="cascade",
    )
    page = DocumentPage(
        document=document,
        course=model_graph.course,
        content_index=0,
        page_number=None,
        raw_text="raw cascade",
        text="raw cascade",
        raw_extraction_method="decoded",
        extraction_method="decoded",
        has_images=False,
        needs_ocr=False,
        has_visual_content=True,
        visual_analysis_status="completed",
    )
    visual = DocumentVisual(
        page=page,
        visual_index=0,
        visual_type="diagram",
        source="drawing",
        bbox_x0=1.0,
        bbox_y0=2.0,
        bbox_x1=20.0,
        bbox_y1=30.0,
        description="Cascade visual",
        analysis_status="succeeded",
    )
    db_session.add_all((chunk, page, visual))
    db_session.commit()
    document_id = document.id
    chunk_id = chunk.id
    page_id = page.id
    visual_id = visual.id

    assert list(model_graph.user.uploaded_documents) == [document]
    assert list(page.visuals) == [visual]
    db_session.delete(model_graph.user)
    db_session.commit()

    assert db_session.get(UploadedDocument, document_id) is None
    assert db_session.get(DocumentChunk, chunk_id) is None
    assert db_session.get(DocumentPage, page_id) is None
    assert db_session.get(DocumentVisual, visual_id) is None


@pytest.mark.database_contract
def test_course_creation_recovers_lost_commit_acknowledgement(
    session_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with session_factory() as session:
        owner = UserService.create_user(
            session,
            UserCreate(
                name="Owner",
                email="owner@example.com",
                password="strong-password",
            ),
        )
        original_commit = session.commit

        def commit_then_fail() -> None:
            original_commit()
            raise SQLAlchemyError("simulated lost acknowledgement")

        monkeypatch.setattr(session, "commit", commit_then_fail)
        created = CourseService.create_course(
            session,
            CourseCreate(
                title="Idempotent Course",
                semester="Fall",
                exam_date=date(2026, 6, 15),
            ),
            owner.id,
        )

        assert created.title == "Idempotent Course"
        with session_factory() as verification:
            assert verification.scalar(select(func.count()).select_from(Course)) == 1


def test_database_length_and_nullability_rules_are_validated_by_api(
    api_context,
) -> None:
    registered = api_context.client.post(
        "/api/auth/register",
        json={
            "name": "Admin",
            "email": "admin@example.com",
            "password": "strong-password",
        },
    )
    assert registered.status_code == 200
    login = api_context.client.post(
        "/api/auth/login",
        data={"username": "admin@example.com", "password": "strong-password"},
    )
    authorization = {"Authorization": f"Bearer {login.json()['access_token']}"}

    too_long = api_context.client.post(
        "/api/courses/",
        headers=authorization,
        json={
            "title": "x" * 201,
            "semester": "Fall",
            "exam_date": "2026-06-15",
        },
    )
    assert too_long.status_code == 422

    non_finite_exam_date = api_context.client.post(
        "/api/courses/",
        headers={**authorization, "Content-Type": "application/json"},
        content=('{"title":"Infinite","semester":"Admin","exam_date":1e400}'),
    )
    assert non_finite_exam_date.status_code == 422

    created = api_context.client.post(
        "/api/courses/",
        headers=authorization,
        json={"title": "Course", "semester": "Fall", "exam_date": "2026-06-15"},
    )
    assert created.status_code == 201

    null_update = api_context.client.put(
        f"/api/courses/{created.json()['data']['id']}",
        headers=authorization,
        json={"title": None},
    )
    assert null_update.status_code == 422

    invalid_model = api_context.client.put(
        "/api/users/me/model",
        headers=authorization,
        params={"model_name": "x" * 101},
    )
    assert invalid_model.status_code == 422


def test_registration_rejects_nul_name(api_context) -> None:
    response = api_context.client.post(
        "/api/auth/register",
        json={
            "name": "Unsafe\x00name",
            "email": "name@example.com",
            "password": "strong-password",
        },
    )

    assert response.status_code == 422


def test_nul_password_is_refused_rather_than_stored(api_context) -> None:
    """The password policy names the NUL byte. See docs/authentication.md."""
    password = "pass\x00word-long-enough"
    registered = api_context.client.post(
        "/api/auth/register",
        json={
            "name": "Nul Byte",
            "email": "nul-byte@example.com",
            "password": password,
        },
    )
    login = api_context.client.post(
        "/api/auth/login",
        data={"username": "nul-byte@example.com", "password": password},
    )

    assert registered.status_code == 422
    # No account was opened, so the credential cannot be used either.
    assert login.status_code == 401


@pytest.mark.parametrize("field", ["title", "description", "semester"])
def test_course_writes_reject_nul_text(api_context, field: str) -> None:
    registered = api_context.client.post(
        "/api/auth/register",
        json={
            "name": "Admin",
            "email": f"{field}@example.com",
            "password": "strong-password",
        },
    )
    assert registered.status_code == 200
    login = api_context.client.post(
        "/api/auth/login",
        data={"username": f"{field}@example.com", "password": "strong-password"},
    )
    authorization = {"Authorization": f"Bearer {login.json()['access_token']}"}
    payload = {
        "title": "Course",
        "description": "Description",
        "semester": "Fall",
        "exam_date": "2026-06-15",
    }
    payload[field] = "Unsafe\x00value"

    response = api_context.client.post(
        "/api/courses/",
        headers=authorization,
        json=payload,
    )
    assert response.status_code == 422

    payload[field] = "Valid value"
    created = api_context.client.post(
        "/api/courses/",
        headers=authorization,
        json=payload,
    )
    assert created.status_code == 201

    updated = api_context.client.put(
        f"/api/courses/{created.json()['data']['id']}",
        headers=authorization,
        json={field: "Unsafe\x00value"},
    )
    assert updated.status_code == 422


def test_preferred_model_rejects_nul_text(api_context) -> None:
    registered = api_context.client.post(
        "/api/auth/register",
        json={
            "name": "Admin",
            "email": "model-nul@example.com",
            "password": "strong-password",
        },
    )
    assert registered.status_code == 200
    login = api_context.client.post(
        "/api/auth/login",
        data={
            "username": "model-nul@example.com",
            "password": "strong-password",
        },
    )

    response = api_context.client.put(
        "/api/users/me/model",
        headers={"Authorization": f"Bearer {login.json()['access_token']}"},
        params={"model_name": "unsafe\x00model"},
    )

    assert response.status_code == 422


def test_response_schemas_can_read_legacy_nul_text() -> None:
    with pytest.raises(ValidationError):
        UserUpdate(preferred_model="unsafe\x00model")

    course = CourseResponse(
        id=1,
        owner_id=1,
        title="Legacy\x00 course",
        description=None,
        semester="Fall",
        exam_date=date(2026, 6, 15),
        syllabus="Legacy\x00 syllabus",
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
    )
    user = UserResponse(
        id=1,
        name="Legacy\x00 user",
        email="legacy@example.com",
        role=Role.USER,
        is_banned=False,
        is_email_verified=False,
        credits=100,
        preferred_model="legacy\x00model",
    )

    assert course.title == "Legacy\x00 course"
    assert course.syllabus == "Legacy\x00 syllabus"
    assert user.name == "Legacy\x00 user"
    assert user.preferred_model == "legacy\x00model"


def test_passwords_over_bcrypt_byte_limit_are_rejected(api_context) -> None:
    response = api_context.client.post(
        "/api/auth/register",
        json={
            "name": "Long Password",
            "email": "long-password@example.com",
            "password": "\u0151" * 37,
        },
    )

    assert response.status_code == 422


def test_email_identity_is_canonicalized_case_insensitively(api_context) -> None:
    created = api_context.client.post(
        "/api/auth/register",
        json={
            "name": "Mixed Case",
            "email": "Mixed.Case@Example.COM",
            "password": "strong-password",
        },
    )
    duplicate = api_context.client.post(
        "/api/auth/register",
        json={
            "name": "Duplicate",
            "email": "mixed.case@example.com",
            "password": "another-password",
        },
    )
    login = api_context.client.post(
        "/api/auth/login",
        data={"username": "MIXED.CASE@EXAMPLE.COM", "password": "strong-password"},
    )

    assert created.status_code == 200
    assert created.json()["user_email"] == "mixed.case@example.com"
    assert duplicate.status_code == 400
    assert login.status_code == 200


def test_idna_email_lookup_uses_same_canonical_form_as_registration(
    api_context,
) -> None:
    created = api_context.client.post(
        "/api/auth/register",
        json={
            "name": "IDNA User",
            "email": "user@xn--bcher-kva.de",
            "password": "strong-password",
        },
    )
    login = api_context.client.post(
        "/api/auth/login",
        data={"username": "user@xn--bcher-kva.de", "password": "strong-password"},
    )

    assert created.status_code == 200
    assert login.status_code == 200


def test_non_string_token_subject_is_rejected_as_unauthorized(api_context) -> None:
    token = create_access_token({"sub": 123})

    response = api_context.client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 401


def test_admin_can_manage_email_with_encoded_path_separator(api_context) -> None:
    for name, email in (
        ("Admin", "admin@example.com"),
        ("Slash", "foo/bar@example.com"),
    ):
        response = api_context.client.post(
            "/api/auth/register",
            json={"name": name, "email": email, "password": "strong-password"},
        )
        assert response.status_code == 200

    login = api_context.client.post(
        "/api/auth/login",
        data={"username": "admin@example.com", "password": "strong-password"},
    )
    encoded_email = quote("foo/bar@example.com", safe="")
    banned = api_context.client.put(
        f"/api/admin/users/{encoded_email}/ban",
        params={"is_banned": "true"},
        headers={"Authorization": f"Bearer {login.json()['access_token']}"},
    )

    assert banned.status_code == 200
    assert banned.json()["data"]["email"] == "foo/bar@example.com"
    assert banned.json()["data"]["is_banned"] is True

    listed = api_context.client.get(
        "/api/admin/users",
        headers={"Authorization": f"Bearer {login.json()['access_token']}"},
    )
    assert listed.status_code == 200
    assert listed.json()["success"] is True
    emails = [u["email"] for u in listed.json()["data"]]
    assert "admin@example.com" in emails
    assert "foo/bar@example.com" in emails


def test_user_listing_requires_an_administrator(authz_api) -> None:
    response = authz_api.client.get(
        "/api/admin/users",
        headers=authz_api.authorization_a,
    )

    assert response.status_code == 403


def test_losing_initial_admin_race_retries_as_normal_user(
    session_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    initial_admin_barrier = Barrier(2)
    original_new_user = UserService._new_user

    def synchronized_new_user(db, user_data, role, *, claims_initial_admin):
        user = original_new_user(
            db,
            user_data,
            role,
            claims_initial_admin=claims_initial_admin,
        )
        if claims_initial_admin:
            initial_admin_barrier.wait(timeout=5)
        return user

    monkeypatch.setattr(
        UserService,
        "_new_user",
        staticmethod(synchronized_new_user),
    )

    def register(index: int) -> tuple[str, bool | None]:
        with session_factory() as session:
            user = UserService.create_user(
                session,
                UserCreate(
                    name=f"User {index}",
                    email=f"user-{index}@example.com",
                    password="strong-password",
                ),
            )
            return user.role.name, user.is_initial_admin

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(register, range(2)))

    assert sorted(role for role, _marker in results) == ["admin", "user"]
    assert sorted(marker is True for _role, marker in results) == [False, True]


def test_hosted_mode_only_configured_email_becomes_initial_admin(
    session_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        user_service_module,
        "settings",
        replace(
            settings,
            deployment_mode=MODE_HOSTED,
            bootstrap_admin_email="bootstrap@example.com",
            bootstrap_admin_token="b" * 32,
        ),
    )

    with session_factory() as session:
        regular = UserService.create_user(
            session,
            UserCreate(
                name="Regular",
                email="regular@example.com",
                password="strong-password",
            ),
        )
        bootstrap = UserService.create_user(
            session,
            UserCreate(
                name="Bootstrap",
                email="bootstrap@example.com",
                password="strong-password",
            ),
            bootstrap_token="b" * 32,
        )

        assert regular.role.name == "user"
        assert regular.is_initial_admin is None
        assert bootstrap.role.name == "admin"
        assert bootstrap.is_initial_admin is True


def test_hosted_bootstrap_email_requires_secret_token(
    session_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        user_service_module,
        "settings",
        replace(
            settings,
            deployment_mode=MODE_HOSTED,
            bootstrap_admin_email="bootstrap@example.com",
            bootstrap_admin_token="b" * 32,
        ),
    )

    with session_factory() as session:
        with pytest.raises(BadRequestException, match="Invalid bootstrap"):
            UserService.create_user(
                session,
                UserCreate(
                    name="Attacker",
                    email="bootstrap@example.com",
                    password="strong-password",
                ),
                bootstrap_token="wrong-token",
            )
        assert UserService.get_user_by_email(session, "bootstrap@example.com") is None


def test_production_self_hosted_admin_requires_bootstrap_credentials(
    session_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        user_service_module,
        "settings",
        replace(
            settings,
            app_env=APP_ENV_PRODUCTION,
            deployment_mode=MODE_SELF_HOSTED,
            bootstrap_admin_email="bootstrap@example.com",
            bootstrap_admin_token="b" * 32,
        ),
    )

    with session_factory() as session:
        regular = UserService.create_user(
            session,
            UserCreate(
                name="Regular",
                email="regular@example.com",
                password="strong-password",
            ),
        )
        with pytest.raises(BadRequestException, match="Invalid bootstrap"):
            UserService.create_user(
                session,
                UserCreate(
                    name="Attacker",
                    email="bootstrap@example.com",
                    password="strong-password",
                ),
                bootstrap_token="wrong-token",
            )
        bootstrap = UserService.create_user(
            session,
            UserCreate(
                name="Bootstrap",
                email="bootstrap@example.com",
                password="strong-password",
            ),
            bootstrap_token="b" * 32,
        )

        assert regular.role.name == "user"
        assert regular.is_initial_admin is None
        assert bootstrap.role.name == "admin"
        assert bootstrap.is_initial_admin is True


def test_admin_cannot_self_lock_and_role_changes_keep_credit_invariant(
    api_context,
) -> None:
    for name, email in (("Admin", "admin@example.com"), ("User", "user@example.com")):
        response = api_context.client.post(
            "/api/auth/register",
            json={"name": name, "email": email, "password": "strong-password"},
        )
        assert response.status_code == 200

    login = api_context.client.post(
        "/api/auth/login",
        data={"username": "admin@example.com", "password": "strong-password"},
    )
    authorization = {"Authorization": f"Bearer {login.json()['access_token']}"}

    self_ban = api_context.client.put(
        "/api/admin/users/admin@example.com/ban",
        params={"is_banned": "true"},
        headers=authorization,
    )
    self_demote = api_context.client.put(
        "/api/admin/users/admin@example.com/role",
        params={"role": "user"},
        headers=authorization,
    )
    assert self_ban.status_code == 400
    assert self_demote.status_code == 400

    promoted = api_context.client.put(
        "/api/admin/users/user@example.com/role",
        params={"role": "admin"},
        headers=authorization,
    )
    assert promoted.status_code == 200
    assert promoted.json()["data"]["credits"] is None

    second_login = api_context.client.post(
        "/api/auth/login",
        data={"username": "user@example.com", "password": "strong-password"},
    )
    second_authorization = {
        "Authorization": f"Bearer {second_login.json()['access_token']}"
    }
    demote_initial = api_context.client.put(
        "/api/admin/users/admin@example.com/role",
        params={"role": "user"},
        headers=second_authorization,
    )
    ban_initial = api_context.client.put(
        "/api/admin/users/admin@example.com/ban",
        params={"is_banned": "true"},
        headers=second_authorization,
    )
    assert demote_initial.status_code == 400
    assert ban_initial.status_code == 400

    encoded_self_demote = api_context.client.put(
        "/api/admin/users/%20user@example.com%20/role",
        params={"role": "user"},
        headers=second_authorization,
    )
    assert encoded_self_demote.status_code == 400

    demoted = api_context.client.put(
        "/api/admin/users/user@example.com/role",
        params={"role": "user"},
        headers=authorization,
    )
    assert demoted.status_code == 200
    assert demoted.json()["data"]["credits"] == 50.0

    with api_context.session_factory() as session:
        user = UserService.get_user_by_email(session, "user@example.com")
        assert user is not None
        user.credits = 0.0
        session.commit()
        unchanged = UserService.update_user(
            session,
            user.email,
            UserUpdate(role=Role.USER),
        )
        assert unchanged.credits == 0.0
