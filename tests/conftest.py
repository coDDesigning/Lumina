from __future__ import annotations

import os
import shutil
from collections.abc import Iterator
from dataclasses import dataclass, replace
from datetime import timedelta
from pathlib import Path
from uuid import UUID

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Engine, inspect, select, text
from sqlalchemy.orm import Session, sessionmaker

from backend.app.base import Base
from backend.app.config import settings
from backend.app.database import get_db
from backend.app.database_engine import create_database_engine
from backend.app.models import (
    EMBEDDING_DIMENSIONS,
    Course,
    DocumentChunk,
    ProcessingJob,
    Role,
    UploadedDocument,
    User,
)
from main import app
from services import credits as credits_service
from services import semantic_retrieval as semantic_retrieval_service
from services.processing_jobs import claim_next_job, fail_job
from services.vector_store import (
    ChromaVectorStore,
    PgVectorStore,
    VectorRecord,
    VectorStore,
)
from storage.dependencies import get_storage
from storage.local import LocalStorage
from utils.security import create_access_token

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class ApiContext:
    client: TestClient
    session_factory: sessionmaker[Session]
    storage: LocalStorage
    storage_root: Path


@dataclass(frozen=True)
class UploadApiContext(ApiContext):
    user_id: int
    course_id: int
    other_course_id: int
    deleted_course_id: int
    authorization: dict[str, str]


@dataclass(frozen=True)
class AuthorizationApiContext(ApiContext):
    user_a_id: int
    user_b_id: int
    admin_id: int
    a_course_id: int
    a_deleted_course_id: int
    b_course_id: int
    a_document_id: UUID
    a_storage_key: str
    authorization_a: dict[str, str]
    authorization_b: dict[str, str]
    authorization_admin: dict[str, str]


@dataclass(frozen=True)
class ModelGraph:
    user: User
    course: Course
    other_course: Course


def _reset_postgresql_contract_data(engine: Engine) -> None:
    table_names = ", ".join(
        engine.dialect.identifier_preparer.quote(table.name)
        for table in Base.metadata.sorted_tables
    )
    with engine.begin() as connection:
        connection.execute(
            text(f"TRUNCATE TABLE {table_names} RESTART IDENTITY CASCADE")
        )
        connection.execute(
            Role.__table__.insert(),
            [{"name": "admin"}, {"name": "user"}],
        )


def seed_registration_grant(*users: User) -> None:
    """Give a directly-built fixture user the ledger baseline registration gives.

    Fixtures construct users without going through ``UserService.create_user``,
    so they would otherwise start with a balance no ledger row explains and be
    owed the current month's grant on their very first request. Attaching the
    row registration would have written keeps fixture accounts realistic.
    """
    for user in users:
        grant = credits_service.CreditService.build_initial_grant(user)
        if grant is not None:
            user.credit_transactions.append(grant)


@pytest.fixture(autouse=True)
def metered_credits(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run the suite as a deployment that meters credits.

    CI exercises the backend in self-hosted mode, where credits are exempt by
    default, but nearly every credit assertion in this suite is about the
    metered behavior a hosted deployment gets. Making metering the ambient state
    keeps those tests honest; the exempt path is proven by the tests that turn
    metering back off explicitly.
    """
    monkeypatch.setattr(
        credits_service,
        "settings",
        replace(settings, credit_metering_enabled=True),
    )


def set_credit_policy(
    monkeypatch: pytest.MonkeyPatch,
    **overrides: object,
) -> None:
    """Point services.credits at a variant policy for one test."""
    monkeypatch.setattr(
        credits_service,
        "settings",
        replace(settings, credit_metering_enabled=True, **overrides),
    )


@pytest.fixture(scope="session")
def sqlite_contract_template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    database_path = tmp_path_factory.mktemp("database-contract") / "template.sqlite3"
    database_url = f"sqlite:///{database_path.as_posix()}"
    previous_database_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = database_url
    try:
        config = Config()
        config.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
        config.set_main_option("prepend_sys_path", str(PROJECT_ROOT))
        config.set_main_option("path_separator", "os")
        command.upgrade(config, "head")
    finally:
        if previous_database_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous_database_url
    return database_path


@pytest.fixture
def database_engine(
    request: pytest.FixtureRequest,
    tmp_path: Path,
) -> Iterator[Engine]:
    if settings.is_hosted:
        engine = create_database_engine(settings.database_url)
        validated_database = False
        try:
            with engine.connect() as connection:
                database_name = connection.scalar(text("SELECT current_database()"))
            if database_name != "lumina_ci":
                raise RuntimeError(
                    "PostgreSQL contract tests require the disposable lumina_ci database"
                )
            validated_database = True
            _reset_postgresql_contract_data(engine)
            yield engine
        finally:
            try:
                if validated_database:
                    _reset_postgresql_contract_data(engine)
            finally:
                engine.dispose()
        return

    database_path = tmp_path / "test.sqlite3"
    if request.node.get_closest_marker("database_contract") is not None:
        template = request.getfixturevalue("sqlite_contract_template")
        shutil.copyfile(template, database_path)
        engine = create_database_engine(f"sqlite:///{database_path.as_posix()}")
        try:
            yield engine
        finally:
            engine.dispose()
        return

    engine = create_database_engine(f"sqlite:///{database_path.as_posix()}")

    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all([Role(name="admin"), Role(name="user")])
        session.commit()
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def session_factory(database_engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(
        bind=database_engine,
        class_=Session,
        expire_on_commit=False,
        autoflush=False,
    )


def _schema_drift(engine: Engine) -> dict[str, dict[str, list[str]]]:
    """Report every column Alembic and the ORM models disagree about."""
    inspector = inspect(engine)
    reflected_tables = set(inspector.get_table_names())
    drift: dict[str, dict[str, list[str]]] = {}
    for table in Base.metadata.sorted_tables:
        modelled = {column.name for column in table.columns}
        if table.name not in reflected_tables:
            drift[table.name] = {
                "missing_from_database": sorted(modelled),
                "missing_from_models": [],
            }
            continue
        reflected = {column["name"] for column in inspector.get_columns(table.name)}
        if reflected != modelled:
            drift[table.name] = {
                "missing_from_database": sorted(modelled - reflected),
                "missing_from_models": sorted(reflected - modelled),
            }
    return drift


@pytest.fixture
def schema_drift(database_engine: Engine) -> dict[str, dict[str, list[str]]]:
    return _schema_drift(database_engine)


@pytest.fixture
def db_session(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    session = session_factory()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


EMBEDDING_PROVIDER_NAME = "ollama"
EMBEDDING_MODEL_NAME = "nomic-embed-text"


def directional_vector(seed: float) -> list[float]:
    """A unit-ish vector whose cosine against ``directional_vector(0.0)`` is 1/sqrt(1+seed**2).

    Tests pick a seed to place a chunk deliberately above or below a similarity
    floor: seed 0.0 scores 1.00, seed 1.0 scores 0.71, seed 4.0 scores 0.24.
    """
    values = [0.0] * EMBEDDING_DIMENSIONS
    values[0] = 1.0
    values[1] = seed
    return values


class StubEmbeddingProvider:
    """Embeds queries to a settable vector and refuses to embed documents."""

    def __init__(self, *, query_vector: list[float] | None = None) -> None:
        self.query_vector = (
            query_vector if query_vector is not None else directional_vector(0.0)
        )
        self.embed_query_calls: list[str] = []

    def embed_documents(self, texts):
        raise AssertionError("retrieval must never embed documents")

    def embed_query(self, text: str) -> list[float]:
        self.embed_query_calls.append(text)
        return list(self.query_vector)


@dataclass
class RetrievalContext:
    provider: StubEmbeddingProvider
    store: VectorStore

    def index(
        self,
        session: Session,
        document: UploadedDocument,
        chunks: list[DocumentChunk],
        *,
        seeds: list[float] | None = None,
    ) -> None:
        """Give every chunk a vector so retrieval can find it."""
        resolved = seeds if seeds is not None else [0.0] * len(chunks)
        self.store.replace_document_vectors(
            session,
            document_id=document.id,
            course_id=document.course_id,
            records=[
                VectorRecord(
                    chunk_id=chunk.id,
                    document_id=document.id,
                    course_id=document.course_id,
                    chunk_index=chunk.chunk_index,
                    embedding=directional_vector(seed),
                )
                for chunk, seed in zip(chunks, resolved, strict=True)
            ],
            embedding_provider=EMBEDDING_PROVIDER_NAME,
            embedding_model=EMBEDDING_MODEL_NAME,
        )


@pytest.fixture
def retrieval_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> RetrievalContext:
    """Wire semantic retrieval to a real vector store and a deterministic embedder.

    Patching ``services.semantic_retrieval`` is enough because that module
    resolves both factories at call time; every feature reaches the store through
    it.
    """
    store: VectorStore = (
        PgVectorStore()
        if settings.is_hosted
        else ChromaVectorStore(persist_directory=str(tmp_path / "chroma"))
    )
    provider = StubEmbeddingProvider()
    monkeypatch.setattr(
        semantic_retrieval_service, "get_embedding_provider", lambda: provider
    )
    monkeypatch.setattr(semantic_retrieval_service, "get_vector_store", lambda: store)
    return RetrievalContext(provider=provider, store=store)


@pytest.fixture
def model_graph(db_session: Session) -> ModelGraph:
    role = db_session.scalar(select(Role).where(Role.name == "admin"))
    assert role is not None
    user = User(
        name="Test Admin",
        email="admin@example.com",
        password_hash="not-used-by-these-tests",
        role=role,
        credits=None,
        is_banned=False,
        preferred_model="gpt-4o-mini",
    )
    course = Course(
        title="Primary Course",
        description="Repository test course",
        semester="Fall",
        exam_date="2026",
        owner=user,
        is_deleted=False,
    )
    other_course = Course(
        title="Other Course",
        description=None,
        semester="Fall",
        exam_date="2026",
        owner=user,
        is_deleted=False,
    )
    db_session.add_all([course, other_course])
    db_session.commit()
    return ModelGraph(user=user, course=course, other_course=other_course)


@pytest.fixture
def api_context(
    tmp_path: Path,
    session_factory: sessionmaker[Session],
) -> Iterator[ApiContext]:
    storage_root = tmp_path / "uploads"
    storage = LocalStorage(storage_root, chunk_size=17)

    def override_get_db() -> Iterator[Session]:
        session = session_factory()
        try:
            yield session
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def override_get_storage() -> LocalStorage:
        return storage

    missing = object()
    previous_db_override = app.dependency_overrides.get(get_db, missing)
    previous_storage_override = app.dependency_overrides.get(get_storage, missing)
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_storage] = override_get_storage

    try:
        with TestClient(app) as client:
            yield ApiContext(
                client=client,
                session_factory=session_factory,
                storage=storage,
                storage_root=storage_root,
            )
    finally:
        if previous_db_override is missing:
            app.dependency_overrides.pop(get_db, None)
        else:
            app.dependency_overrides[get_db] = previous_db_override

        if previous_storage_override is missing:
            app.dependency_overrides.pop(get_storage, None)
        else:
            app.dependency_overrides[get_storage] = previous_storage_override


@pytest.fixture
def upload_api(api_context: ApiContext) -> UploadApiContext:
    with api_context.session_factory() as session:
        role = session.scalar(select(Role).where(Role.name == "user"))
        assert role is not None
        user = User(
            name="Upload Owner",
            email="uploader@example.com",
            password_hash="not-used-by-these-tests",
            role=role,
            credits=None,
            is_banned=False,
            preferred_model="gpt-4o-mini",
        )
        course = Course(
            title="Active Course",
            description="Document upload tests",
            semester="Fall",
            exam_date="2026",
            owner=user,
            is_deleted=False,
        )
        other_course = Course(
            title="Second Active Course",
            description=None,
            semester="Fall",
            exam_date="2026",
            owner=user,
            is_deleted=False,
        )
        deleted_course = Course(
            title="Deleted Course",
            description=None,
            semester="Fall",
            exam_date="2026",
            owner=user,
            is_deleted=True,
        )
        seed_registration_grant(user)
        session.add_all([course, other_course, deleted_course])
        session.commit()
        user_id = user.id
        course_id = course.id
        other_course_id = other_course.id
        deleted_course_id = deleted_course.id

    token = create_access_token({"sub": user.email})
    return UploadApiContext(
        client=api_context.client,
        session_factory=api_context.session_factory,
        storage=api_context.storage,
        storage_root=api_context.storage_root,
        user_id=user_id,
        course_id=course_id,
        other_course_id=other_course_id,
        deleted_course_id=deleted_course_id,
        authorization={"Authorization": f"Bearer {token}"},
    )


def _authorization_header(email: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token({'sub': email})}"}


@pytest.fixture
def authz_api(api_context: ApiContext) -> AuthorizationApiContext:
    """Two separate owners plus an administrator, with real state under user A.

    Cross-user tests must prove that a denied request changes nothing, so user
    A owns a genuinely uploaded document: a stored file, a database row, and a
    processing job driven to ``failed`` so retry is reachable.
    """
    with api_context.session_factory() as session:
        user_role = session.scalar(select(Role).where(Role.name == "user"))
        admin_role = session.scalar(select(Role).where(Role.name == "admin"))
        assert user_role is not None
        assert admin_role is not None

        user_a = User(
            name="Owner A",
            email="owner-a@example.com",
            password_hash="not-used-by-these-tests",
            role=user_role,
            credits=50.0,
            is_banned=False,
            preferred_model="gemini:gemini-3.6-flash",
        )
        user_b = User(
            name="Owner B",
            email="owner-b@example.com",
            password_hash="not-used-by-these-tests",
            role=user_role,
            credits=50.0,
            is_banned=False,
            preferred_model="gemini:gemini-3.6-flash",
        )
        administrator = User(
            name="Course Administrator",
            email="authz-admin@example.com",
            password_hash="not-used-by-these-tests",
            role=admin_role,
            credits=None,
            is_banned=False,
            preferred_model="gemini:gemini-3.6-flash",
        )

        seed_registration_grant(user_a, user_b, administrator)

        a_course = Course(
            title="Owner A Active Course",
            description="Private study material",
            semester="Fall",
            exam_date="2026",
            owner=user_a,
            is_deleted=False,
        )
        a_deleted_course = Course(
            title="Owner A Deleted Course",
            description=None,
            semester="Fall",
            exam_date="2026",
            owner=user_a,
            is_deleted=True,
        )
        b_course = Course(
            title="Owner B Active Course",
            description=None,
            semester="Fall",
            exam_date="2026",
            owner=user_b,
            is_deleted=False,
        )
        session.add_all([administrator, a_course, a_deleted_course, b_course])
        session.commit()

        user_a_id = user_a.id
        user_b_id = user_b.id
        admin_id = administrator.id
        a_course_id = a_course.id
        a_deleted_course_id = a_deleted_course.id
        b_course_id = b_course.id

    authorization_a = _authorization_header("owner-a@example.com")
    uploaded = api_context.client.post(
        f"/api/courses/{a_course_id}/documents",
        headers=authorization_a,
        files={"document": ("owner-a-notes.txt", b"Owner A notes", "text/plain")},
    )
    assert uploaded.status_code == 201, uploaded.text
    a_document_id = UUID(uploaded.json()["document"]["id"])

    with api_context.session_factory() as session:
        queued_job = session.scalar(
            select(ProcessingJob).where(ProcessingJob.document_id == a_document_id)
        )
        assert queued_job is not None
        claim_at = queued_job.available_at + timedelta(seconds=1)
    with api_context.session_factory() as session:
        claim = claim_next_job(
            session,
            "authz-fixture-worker",
            api_context.storage.provider,
            60,
            now=claim_at,
        )
    assert claim is not None
    with api_context.session_factory() as session:
        assert (
            fail_job(
                session,
                claim.id,
                claim.claim_token,
                error_code="OCR_REQUIRED",
                error_message="The document requires OCR.",
                retryable=False,
                now=claim_at + timedelta(seconds=1),
            )
            == "failed"
        )

    with api_context.session_factory() as session:
        document = session.get(UploadedDocument, a_document_id)
        assert document is not None
        assert document.status == "failed"
        a_storage_key = document.storage_key

    return AuthorizationApiContext(
        client=api_context.client,
        session_factory=api_context.session_factory,
        storage=api_context.storage,
        storage_root=api_context.storage_root,
        user_a_id=user_a_id,
        user_b_id=user_b_id,
        admin_id=admin_id,
        a_course_id=a_course_id,
        a_deleted_course_id=a_deleted_course_id,
        b_course_id=b_course_id,
        a_document_id=a_document_id,
        a_storage_key=a_storage_key,
        authorization_a=authorization_a,
        authorization_b=_authorization_header("owner-b@example.com"),
        authorization_admin=_authorization_header("authz-admin@example.com"),
    )
