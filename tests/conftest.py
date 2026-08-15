from __future__ import annotations

import os
import shutil
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session, sessionmaker

from backend.app.base import Base
from backend.app.config import settings
from backend.app.database import get_db
from backend.app.database_engine import create_database_engine
from backend.app.models import Course, Role, User
from main import app
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


@pytest.fixture
def db_session(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    session = session_factory()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


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
        instructor="Instructor One",
        price=0.0,
        owner=user,
        is_deleted=False,
    )
    other_course = Course(
        title="Other Course",
        description=None,
        instructor="Instructor Two",
        price=12.5,
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
        role = session.scalar(select(Role).where(Role.name == "admin"))
        assert role is not None
        user = User(
            name="Upload Admin",
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
            instructor="Ada",
            price=0.0,
            owner=user,
            is_deleted=False,
        )
        other_course = Course(
            title="Second Active Course",
            description=None,
            instructor="Grace",
            price=5.0,
            owner=user,
            is_deleted=False,
        )
        deleted_course = Course(
            title="Deleted Course",
            description=None,
            instructor="Linus",
            price=0.0,
            owner=user,
            is_deleted=True,
        )
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
