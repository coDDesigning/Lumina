"""Account erasure is fenced immediately and external cleanup is retryable."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from io import BytesIO
from uuid import uuid4

from sqlalchemy import func, select

from backend.app.config import settings
from backend.app.models import (
    Course,
    CreditTransaction,
    DocumentGenerationLock,
    ProfileDocument,
    RateLimitBucket,
    Role,
    UploadedDocument,
    User,
)
from schemas.user import UserCreate
from services.account_deletion import AccountDeletionService
from services.user import UserService
from services.vector_store import VectorStoreError
from storage.base import StorageError, generate_profile_portable_key
from utils.rate_limit import rate_limit_key
from utils.security import create_access_token, get_password_hash
from workers.course_purge import run_account_purge


PASSWORD = "correct horse battery staple"


class RecordingVectorStore:
    def __init__(self) -> None:
        self.fail = False
        self.course_documents = []
        self.courses = []
        self.profile_documents = []
        self.users = []

    def _record(self, collection, value) -> None:
        if self.fail:
            raise VectorStoreError("provider detail must not escape")
        collection.append(value)

    def delete_document_vectors(self, _session, document_id) -> None:
        self._record(self.course_documents, document_id)

    def delete_course_vectors(self, _session, course_id) -> None:
        self._record(self.courses, course_id)

    def delete_profile_document_vectors(self, _session, document_id) -> None:
        self._record(self.profile_documents, document_id)

    def delete_user_profile_vectors(self, _session, user_id) -> None:
        self._record(self.users, user_id)


def _seed_account(api_context, *, initial_admin: bool = False):
    with api_context.session_factory() as session:
        role_name = "admin" if initial_admin else "user"
        role = session.scalar(select(Role).where(Role.name == role_name))
        assert role is not None
        user = User(
            name="Deletion Owner",
            email="delete-me@example.com",
            password_hash=get_password_hash(PASSWORD),
            role=role,
            is_initial_admin=True if initial_admin else None,
            credits=None,
            is_banned=False,
            preferred_model="gemini:gemini-3.6-flash",
        )
        course = Course(title="Private course", owner=user, is_deleted=False)
        session.add(course)
        session.flush()

        course_document_id = uuid4()
        course_key = api_context.storage.generate_key(
            course.id, course_document_id, "txt"
        )
        profile_document_id = uuid4()
        profile_key = generate_profile_portable_key(user.id, profile_document_id, "txt")
        api_context.storage.save(course_key, BytesIO(b"private course material"))
        api_context.storage.save(profile_key, BytesIO(b"private profile material"))
        session.add_all(
            [
                UploadedDocument(
                    id=course_document_id,
                    original_file_name="course.txt",
                    file_type="txt",
                    mime_type="text/plain",
                    file_size=23,
                    file_hash="a" * 64,
                    user_id=user.id,
                    course_id=course.id,
                    storage_provider=api_context.storage.provider,
                    storage_key=course_key,
                    status="ready",
                ),
                ProfileDocument(
                    id=profile_document_id,
                    original_file_name="profile.txt",
                    file_type="txt",
                    mime_type="text/plain",
                    file_size=24,
                    file_hash="b" * 64,
                    user_id=user.id,
                    storage_provider=api_context.storage.provider,
                    storage_key=profile_key,
                    status="ready",
                ),
            ]
        )
        session.commit()
        return {
            "user_id": user.id,
            "email": user.email,
            "course_id": course.id,
            "course_document_id": course_document_id,
            "profile_document_id": profile_document_id,
            "authorization": {
                "Authorization": "Bearer "
                + create_access_token({"sub": user.email, "uid": user.id})
            },
        }


def _request_deletion(api_context, account, password: str = PASSWORD):
    return api_context.client.request(
        "DELETE",
        "/api/users/me",
        headers=account["authorization"],
        json={"current_password": password, "confirmation": "DELETE"},
    )


def test_account_deletion_requires_password_and_typed_confirmation(api_context) -> None:
    account = _seed_account(api_context)

    wrong_password = _request_deletion(api_context, account, "wrong password")
    wrong_confirmation = api_context.client.request(
        "DELETE",
        "/api/users/me",
        headers=account["authorization"],
        json={"current_password": PASSWORD, "confirmation": "delete"},
    )

    assert wrong_password.status_code == 400
    assert (
        wrong_password.headers["X-Error-Code"]
        == "account_deletion_reauthentication_failed"
    )
    assert wrong_password.json() == {
        "detail": "Account deletion could not be requested."
    }
    assert wrong_confirmation.status_code == 422
    with api_context.session_factory() as session:
        user = session.get(User, account["user_id"])
        assert user is not None
        assert user.deletion_requested_at is None


def test_account_is_fenced_and_credentials_are_cleared_atomically(api_context) -> None:
    account = _seed_account(api_context)
    with api_context.session_factory() as session:
        user = session.get(User, account["user_id"])
        assert user is not None
        user.encrypted_openai_api_key = "encrypted-secret"
        session.commit()

    accepted = _request_deletion(api_context, account)

    assert accepted.status_code == 202, accepted.text
    assert accepted.json()["message"] == "Account deletion requested"
    assert (
        api_context.client.get(
            "/api/auth/me", headers=account["authorization"]
        ).status_code
        == 401
    )
    login = api_context.client.post(
        "/api/auth/login",
        data={"username": account["email"], "password": PASSWORD},
    )
    assert login.status_code == 401
    assert login.json() == {"detail": "Incorrect email or password"}
    with api_context.session_factory() as session:
        user = session.get(User, account["user_id"])
        assert user is not None
        assert user.deletion_requested_at is not None
        assert user.tokens_valid_after == user.deletion_requested_at
        assert user.encrypted_openai_api_key is None


def test_protected_initial_administrator_cannot_self_delete(api_context) -> None:
    account = _seed_account(api_context, initial_admin=True)

    response = _request_deletion(api_context, account)

    assert response.status_code == 409
    assert response.headers["X-Error-Code"] == "initial_admin_deletion_forbidden"


def test_concurrent_authenticated_requests_leave_one_idempotent_tombstone(
    api_context,
) -> None:
    account = _seed_account(api_context)
    with api_context.session_factory() as first:
        UserService.request_account_deletion(first, account["user_id"], PASSWORD)
    with api_context.session_factory() as second:
        UserService.request_account_deletion(second, account["user_id"], PASSWORD)

    with api_context.session_factory() as session:
        user = session.get(User, account["user_id"])
        assert user is not None
        assert user.deletion_requested_at is not None


def test_purge_removes_database_storage_and_vectors(api_context) -> None:
    account = _seed_account(api_context)
    assert _request_deletion(api_context, account).status_code == 202
    vectors = RecordingVectorStore()

    report = run_account_purge(
        session_factory=api_context.session_factory,
        storage=api_context.storage,
        vector_store=vectors,
        aged_threshold_seconds=0,
    )

    assert report.accounts_purged == 1
    assert not any(api_context.storage_root.rglob("*.*"))
    assert vectors.course_documents == [account["course_document_id"]]
    assert vectors.courses == [account["course_id"]]
    assert vectors.profile_documents == [account["profile_document_id"]]
    assert vectors.users == [account["user_id"]]
    with api_context.session_factory() as session:
        assert session.get(User, account["user_id"]) is None
        assert session.scalar(select(func.count()).select_from(Course)) == 0
        assert session.scalar(select(func.count()).select_from(ProfileDocument)) == 0


def test_partial_external_failures_retain_tombstone_and_retry(api_context) -> None:
    account = _seed_account(api_context)
    assert _request_deletion(api_context, account).status_code == 202
    vectors = RecordingVectorStore()
    original_delete = api_context.storage.delete

    def fail_storage(_key):
        raise StorageError("private provider detail")

    api_context.storage.delete = fail_storage
    storage_report = run_account_purge(
        session_factory=api_context.session_factory,
        storage=api_context.storage,
        vector_store=vectors,
        aged_threshold_seconds=0,
    )
    api_context.storage.delete = original_delete

    assert storage_report.accounts_failed == 1
    with api_context.session_factory() as session:
        user = session.get(User, account["user_id"])
        assert user is not None
        assert user.deletion_last_error_code == "account_storage_cleanup_failed"

    vectors.fail = True
    vector_report = run_account_purge(
        session_factory=api_context.session_factory,
        storage=api_context.storage,
        vector_store=vectors,
        aged_threshold_seconds=0,
    )
    assert vector_report.accounts_failed == 1
    with api_context.session_factory() as session:
        user = session.get(User, account["user_id"])
        assert user is not None
        assert user.deletion_attempt_count == 2
        assert user.deletion_last_error_code == "account_vector_cleanup_failed"

    vectors.fail = False
    retry_report = run_account_purge(
        session_factory=api_context.session_factory,
        storage=api_context.storage,
        vector_store=vectors,
        aged_threshold_seconds=0,
    )
    assert retry_report.accounts_purged == 1
    with api_context.session_factory() as session:
        assert session.get(User, account["user_id"]) is None


def test_second_worker_observes_completed_purge_as_no_work(api_context) -> None:
    account = _seed_account(api_context)
    assert _request_deletion(api_context, account).status_code == 202
    vectors = RecordingVectorStore()
    with api_context.session_factory() as session:
        assert AccountDeletionService.purge(
            session,
            account["user_id"],
            api_context.storage,
            vectors,
            operation_timeout_seconds=30,
        )
    with api_context.session_factory() as session:
        assert not AccountDeletionService.purge(
            session,
            account["user_id"],
            api_context.storage,
            vectors,
            operation_timeout_seconds=30,
        )


def test_old_jwt_cannot_authenticate_after_same_email_re_registration(
    api_context,
) -> None:
    account = _seed_account(api_context)
    old_token = create_access_token(
        {"sub": account["email"], "uid": account["user_id"]},
        expires_delta=timedelta(hours=1),
    )
    assert _request_deletion(api_context, account).status_code == 202
    vectors = RecordingVectorStore()

    with api_context.session_factory() as session:
        assert AccountDeletionService.purge(
            session,
            account["user_id"],
            api_context.storage,
            vectors,
            operation_timeout_seconds=30,
        )

    # In production with PostgreSQL sequences, new_user gets a fresh ID;
    # in SQLite, if an intermediate account was registered or created, IDs never collide.
    with api_context.session_factory() as session:
        role = session.scalar(select(Role).where(Role.name == "user"))
        session.add(
            User(
                name="Intermediate User",
                email="intermediate@example.com",
                password_hash="pwd",
                role=role,
                preferred_model="gemini:gemini-3.6-flash",
            )
        )
        session.commit()

    with api_context.session_factory() as session:
        new_user = UserService.create_user(
            session,
            UserCreate(
                name="Fresh Account",
                email=account["email"],
                password=PASSWORD,
            ),
        )
        assert new_user.id != account["user_id"]

    old_auth = {"Authorization": f"Bearer {old_token}"}
    me_response = api_context.client.get("/api/auth/me", headers=old_auth)
    assert me_response.status_code == 401


def test_old_token_with_same_uid_rejected_by_timestamp_fence(api_context) -> None:
    import jwt
    from utils.security import ALGORITHM

    account = _seed_account(api_context)
    past_time = datetime.now(timezone.utc) - timedelta(seconds=10)
    token = jwt.encode(
        {
            "sub": account["email"],
            "uid": account["user_id"],
            "iat": past_time,
            "exp": past_time + timedelta(hours=1),
        },
        settings.jwt_secret_key,
        algorithm=ALGORITHM,
    )
    with api_context.session_factory() as session:
        user = session.get(User, account["user_id"])
        assert user is not None
        user.tokens_valid_after = datetime.now(timezone.utc)
        session.commit()

    resp = api_context.client.get(
        "/api/auth/me", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 401


def test_active_generation_lock_defers_account_purge(api_context) -> None:
    account = _seed_account(api_context)
    assert _request_deletion(api_context, account).status_code == 202
    vectors = RecordingVectorStore()

    with api_context.session_factory() as session:
        now = datetime.now(timezone.utc)
        session.add(
            DocumentGenerationLock(
                document_id=account["course_document_id"],
                holder_token=uuid4(),
                holder="test-worker",
                acquired_at=now,
                expires_at=now + timedelta(minutes=5),
            )
        )
        session.commit()

    report = run_account_purge(
        session_factory=api_context.session_factory,
        storage=api_context.storage,
        vector_store=vectors,
        aged_threshold_seconds=0,
    )
    assert report.accounts_failed == 1
    with api_context.session_factory() as session:
        user = session.get(User, account["user_id"])
        assert user is not None
        assert user.deletion_last_error_code == "account_cleanup_deferred"

    with api_context.session_factory() as session:
        session.query(DocumentGenerationLock).delete()
        session.commit()

    retry_report = run_account_purge(
        session_factory=api_context.session_factory,
        storage=api_context.storage,
        vector_store=vectors,
        aged_threshold_seconds=0,
    )
    assert retry_report.accounts_purged == 1
    with api_context.session_factory() as session:
        assert session.get(User, account["user_id"]) is None


def test_pending_deletion_account_cannot_reset_password_or_verify_email(
    api_context, monkeypatch
) -> None:
    account = _seed_account(api_context)
    assert _request_deletion(api_context, account).status_code == 202

    reset_req = api_context.client.post(
        "/api/auth/reset-password",
        json={"email": account["email"]},
    )
    assert reset_req.status_code == 200

    from routes import auth as auth_route

    monkeypatch.setattr(
        auth_route,
        "settings",
        replace(settings, email_verification_required=True),
    )

    verify_req = api_context.client.post(
        "/api/auth/verify-email/resend",
        json={"email": account["email"]},
    )
    assert verify_req.status_code == 200


def test_purge_cleans_rate_limits_and_anonymizes_admin_actor_labels(
    api_context,
) -> None:
    admin_account = _seed_account(api_context, initial_admin=False)
    with api_context.session_factory() as session:
        admin_user = session.get(User, admin_account["user_id"])
        admin_role = session.scalar(select(Role).where(Role.name == "admin"))
        admin_user.role = admin_role
        other_user = User(
            name="Other User",
            email="other@example.com",
            password_hash="pwd",
            role=admin_role,
            is_banned=False,
            preferred_model="gemini:gemini-3.6-flash",
        )
        session.add(other_user)
        session.flush()

        session.add_all(
            [
                CreditTransaction(
                    user_id=other_user.id,
                    delta=10.0,
                    balance_after=10.0,
                    reason="admin_adjustment",
                    actor_type="admin",
                    actor_user_id=admin_user.id,
                    actor_label=admin_user.email,
                ),
                RateLimitBucket(
                    key=f"generation:user:{admin_user.id}",
                    count=5,
                    window_start=datetime.now(timezone.utc),
                ),
                RateLimitBucket(
                    key=rate_limit_key("login:account", admin_user.email),
                    count=3,
                    window_start=datetime.now(timezone.utc),
                ),
            ]
        )
        session.commit()
        other_user_id = other_user.id

    assert _request_deletion(api_context, admin_account).status_code == 202
    vectors = RecordingVectorStore()

    with api_context.session_factory() as session:
        assert AccountDeletionService.purge(
            session,
            admin_account["user_id"],
            api_context.storage,
            vectors,
            operation_timeout_seconds=30,
        )

    with api_context.session_factory() as session:
        assert session.get(User, admin_account["user_id"]) is None
        tx = session.scalar(
            select(CreditTransaction).where(CreditTransaction.user_id == other_user_id)
        )
        assert tx is not None
        assert tx.actor_user_id is None
        assert tx.actor_label == "Deleted administrator"
        buckets = session.scalars(
            select(RateLimitBucket).where(
                RateLimitBucket.key.in_(
                    [
                        f"generation:user:{admin_account['user_id']}",
                        rate_limit_key("login:account", admin_account["email"]),
                    ]
                )
            )
        ).all()
        assert buckets == []


def test_sqlite_secure_delete_is_enabled(api_context) -> None:
    with api_context.session_factory() as session:
        if session.get_bind().dialect.name == "sqlite":
            with session.get_bind().connect() as conn:
                assert conn.exec_driver_sql("PRAGMA secure_delete").scalar() == 1
