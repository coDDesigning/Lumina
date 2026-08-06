from pathlib import Path

from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import delete, text

from backend.app.database import get_db
from backend.app.models import Role
from main import app
from storage.base import StorageError
from storage.dependencies import get_storage

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _migration_scripts() -> ScriptDirectory:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("prepend_sys_path", str(PROJECT_ROOT))
    return ScriptDirectory.from_config(config)


def _stamp_database(session_factory, revision: str = "heads") -> None:
    with session_factory() as session:
        MigrationContext.configure(session.connection()).stamp(
            _migration_scripts(),
            revision,
        )
        session.commit()


def _prepare_ready_dependencies(api_context) -> None:
    _stamp_database(api_context.session_factory)
    api_context.storage_root.mkdir(parents=True)


def test_liveness_does_not_resolve_runtime_dependencies(api_context) -> None:
    def fail_dependency():
        raise AssertionError("liveness resolved a runtime dependency")

    app.dependency_overrides[get_db] = fail_dependency
    app.dependency_overrides[get_storage] = fail_dependency

    response = api_context.client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "alive"}


def test_unversioned_database_is_not_ready(api_context) -> None:
    response = api_context.client.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {"status": "not_ready"}


def test_current_database_with_roles_and_storage_is_ready(api_context) -> None:
    _prepare_ready_dependencies(api_context)

    response = api_context.client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready"}
    assert api_context.storage_root.is_dir()
    assert list(api_context.storage_root.iterdir()) == []


def test_stale_or_additional_migration_heads_are_not_ready(api_context) -> None:
    scripts = _migration_scripts()
    _stamp_database(api_context.session_factory, scripts.get_bases()[0])
    assert api_context.client.get("/health/ready").status_code == 503

    _stamp_database(api_context.session_factory)
    with api_context.session_factory() as session:
        session.execute(
            text(
                "INSERT INTO alembic_version (version_num) "
                "VALUES ('unexpected_revision')"
            )
        )
        session.commit()

    assert api_context.client.get("/health/ready").status_code == 503


def test_missing_required_role_is_not_ready(api_context) -> None:
    _stamp_database(api_context.session_factory)
    with api_context.session_factory() as session:
        session.execute(delete(Role).where(Role.name == "admin"))
        session.commit()

    response = api_context.client.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {"status": "not_ready"}


def test_additional_role_does_not_make_database_unready(api_context) -> None:
    _prepare_ready_dependencies(api_context)
    with api_context.session_factory() as session:
        session.add(Role(name="instructor"))
        session.commit()

    response = api_context.client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


def test_storage_failure_returns_generic_not_ready_response(
    api_context,
    monkeypatch,
) -> None:
    _prepare_ready_dependencies(api_context)

    def fail_storage() -> None:
        raise StorageError("secret provider path")

    monkeypatch.setattr(api_context.storage, "check_ready", fail_storage)

    response = api_context.client.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {"status": "not_ready"}
    assert "secret" not in response.text


def test_readiness_openapi_documents_failure_without_authentication() -> None:
    operation = app.openapi()["paths"]["/health/ready"]["get"]

    assert set(operation["responses"]) >= {"200", "503"}
    assert not operation.get("security")
