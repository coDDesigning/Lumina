import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

from backend.app import settings_overrides
from backend.app.config import settings
from backend.app.settings_overrides import BASE_REVISION, RestartRequest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
URL = "/api/admin/system-settings"
ENV_EXAMPLE = PROJECT_ROOT / ".env.example"


@pytest.fixture(autouse=True)
def isolated_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "system-settings"
    monkeypatch.setenv("SYSTEM_SETTINGS_DIRECTORY", str(directory))
    monkeypatch.delenv("LUMINA_SYSTEM_SETTINGS_APPLY", raising=False)
    settings_overrides._active_revision = BASE_REVISION
    settings_overrides._applied_keys = ()
    settings_overrides._rolled_back_from = None
    settings_overrides._baseline = None
    return directory


@pytest.fixture
def no_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    from services import system_settings as service

    monkeypatch.setattr(service, "validate_candidate", lambda *a, **k: None)


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _supervise(monkeypatch, value: bool = True):
    from routes import admin_system_settings as route_module

    monkeypatch.setattr(
        route_module, "settings", replace(settings, supervised_restart=value)
    )
    return route_module


def _get(authz_api):
    return authz_api.client.get(URL, headers=authz_api.authorization_admin)


def test_a_learner_cannot_read_system_settings(authz_api):
    response = authz_api.client.get(URL, headers=authz_api.authorization_a)

    assert response.status_code == 403
    assert response.headers["X-Error-Code"] == "admin_required"


def test_an_anonymous_caller_cannot_read_system_settings(authz_api):
    response = authz_api.client.get(URL)

    assert response.status_code == 401


def test_a_learner_cannot_change_system_settings(authz_api):
    response = authz_api.client.patch(
        URL,
        headers=authz_api.authorization_a,
        json={"expected_revision": 0, "values": {"OCR_DPI": "150"}},
    )

    assert response.status_code == 403


def test_a_learner_cannot_request_a_restart(authz_api):
    response = authz_api.client.post(
        f"{URL}/restarts",
        headers=authz_api.authorization_a,
        json={"expected_revision": 0},
    )

    assert response.status_code == 403


def test_an_administrator_sees_every_supported_key(authz_api):
    from backend.app.settings_registry import SETTINGS

    response = _get(authz_api)

    assert response.status_code == 200
    payload = response.json()["data"]
    assert len(payload["settings"]) == len(SETTINGS)
    assert payload["active_revision"] == BASE_REVISION
    assert payload["saved_revision"] == BASE_REVISION
    assert payload["pending_restart"] is False


def test_the_inventory_never_returns_a_secret_value(authz_api, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "super-secret-provider-key")
    settings_overrides._baseline = None

    response = _get(authz_api)

    assert "super-secret-provider-key" not in response.text
    rows = {row["key"]: row for row in response.json()["data"]["settings"]}
    gemini = rows["GEMINI_API_KEY"]
    assert gemini["secret"] is True
    assert gemini["value"] is None
    assert gemini["default"] is None
    assert gemini["configured"] is True
    assert gemini["source"] == "environment"


def test_container_and_compose_keys_are_not_editable(authz_api):
    rows = {row["key"]: row for row in _get(authz_api).json()["data"]["settings"]}

    assert rows["DATABASE_URL"]["editable"] is False
    assert rows["DATABASE_URL"]["scope"] == "container_managed"
    assert rows["LUMINA_PORT"]["editable"] is False
    assert rows["LUMINA_PORT"]["scope"] == "compose_managed"
    assert rows["RETRIEVAL_CHUNK_LIMIT"]["editable"] is True


def test_saving_an_override_reports_it_as_pending(authz_api, no_validation):
    response = authz_api.client.patch(
        URL,
        headers=authz_api.authorization_admin,
        json={"expected_revision": 0, "values": {"RETRIEVAL_CHUNK_LIMIT": "48"}},
    )

    assert response.status_code == 200
    payload = response.json()["data"]
    assert payload["saved_revision"] == 1
    assert payload["active_revision"] == 0
    assert payload["pending_restart"] is True
    assert payload["pending_keys"] == ["RETRIEVAL_CHUNK_LIMIT"]

    rows = {row["key"]: row for row in payload["settings"]}
    assert rows["RETRIEVAL_CHUNK_LIMIT"]["source"] == "override"
    assert rows["RETRIEVAL_CHUNK_LIMIT"]["has_override"] is True
    assert rows["RETRIEVAL_CHUNK_LIMIT"]["value"] == "48"


def test_a_stale_revision_is_refused(authz_api, no_validation):
    authz_api.client.patch(
        URL,
        headers=authz_api.authorization_admin,
        json={"expected_revision": 0, "values": {"OCR_DPI": "150"}},
    )

    response = authz_api.client.patch(
        URL,
        headers=authz_api.authorization_admin,
        json={"expected_revision": 0, "values": {"OCR_DPI": "200"}},
    )

    assert response.status_code == 409
    assert response.headers["X-Error-Code"] == "settings_revision_conflict"
    assert dict(settings_overrides.load_overrides().values) == {"OCR_DPI": "150"}


def test_a_container_managed_key_cannot_be_overridden(authz_api, no_validation):
    response = authz_api.client.patch(
        URL,
        headers=authz_api.authorization_admin,
        json={"expected_revision": 0, "values": {"DATABASE_URL": "sqlite:///x.db"}},
    )

    assert response.status_code == 400
    assert response.headers["X-Error-Code"] == "settings_key_not_overridable"
    assert settings_overrides.load_overrides().revision == BASE_REVISION


def test_an_empty_change_set_is_refused(authz_api, no_validation):
    response = authz_api.client.patch(
        URL,
        headers=authz_api.authorization_admin,
        json={"expected_revision": 0, "values": {}, "reset": []},
    )

    assert response.status_code == 400


def test_a_blank_secret_means_unchanged(authz_api, no_validation):
    response = authz_api.client.patch(
        URL,
        headers=authz_api.authorization_admin,
        json={
            "expected_revision": 0,
            "values": {"GEMINI_API_KEY": "", "OCR_DPI": "150"},
        },
    )

    assert response.status_code == 200
    assert dict(settings_overrides.load_overrides().values) == {"OCR_DPI": "150"}


def test_an_invalid_value_is_rejected_before_persistence(authz_api):
    response = authz_api.client.patch(
        URL,
        headers=authz_api.authorization_admin,
        json={"expected_revision": 0, "values": {"RETRIEVAL_CHUNK_LIMIT": "9999"}},
    )

    assert response.status_code == 422
    assert response.headers["X-Error-Code"] == "settings_validation_failed"
    body = response.json()["detail"]
    assert any(item["key"] == "RETRIEVAL_CHUNK_LIMIT" for item in body)
    assert settings_overrides.load_overrides().revision == BASE_REVISION


def test_an_invalid_cross_field_combination_is_rejected(authz_api):
    response = authz_api.client.patch(
        URL,
        headers=authz_api.authorization_admin,
        json={
            "expected_revision": 0,
            "values": {"DOCUMENT_CHUNK_OVERLAP_CHARACTERS": "5000"},
        },
    )

    assert response.status_code == 422
    assert settings_overrides.load_overrides().revision == BASE_REVISION


def test_resetting_one_override_removes_only_that_key(authz_api, no_validation):
    authz_api.client.patch(
        URL,
        headers=authz_api.authorization_admin,
        json={
            "expected_revision": 0,
            "values": {"OCR_DPI": "150", "RETRIEVAL_CHUNK_LIMIT": "48"},
        },
    )

    response = authz_api.client.delete(
        f"{URL}/OCR_DPI?expected_revision=1",
        headers=authz_api.authorization_admin,
    )

    assert response.status_code == 200
    assert dict(settings_overrides.load_overrides().values) == {
        "RETRIEVAL_CHUNK_LIMIT": "48"
    }


def test_resetting_an_unknown_key_is_a_404(authz_api, no_validation):
    response = authz_api.client.delete(
        f"{URL}/NOT_A_REAL_KEY?expected_revision=0",
        headers=authz_api.authorization_admin,
    )

    assert response.status_code == 404


def test_reset_all_requires_confirmation(authz_api, no_validation):
    authz_api.client.patch(
        URL,
        headers=authz_api.authorization_admin,
        json={"expected_revision": 0, "values": {"OCR_DPI": "150"}},
    )

    response = authz_api.client.request(
        "DELETE",
        URL,
        headers=authz_api.authorization_admin,
        json={"expected_revision": 1, "confirm": False},
    )

    assert response.status_code == 400
    assert dict(settings_overrides.load_overrides().values) == {"OCR_DPI": "150"}


def test_reset_all_removes_every_override(authz_api, no_validation):
    authz_api.client.patch(
        URL,
        headers=authz_api.authorization_admin,
        json={
            "expected_revision": 0,
            "values": {"OCR_DPI": "150", "RETRIEVAL_CHUNK_LIMIT": "48"},
        },
    )

    response = authz_api.client.request(
        "DELETE",
        URL,
        headers=authz_api.authorization_admin,
        json={"expected_revision": 1, "confirm": True},
    )

    assert response.status_code == 200
    assert dict(settings_overrides.load_overrides().values) == {}
    rows = {row["key"]: row for row in response.json()["data"]["settings"]}
    assert rows["OCR_DPI"]["has_override"] is False
    assert rows["OCR_DPI"]["source"] in {"environment", "default"}


def test_saving_never_touches_the_committed_env_example(authz_api, no_validation):
    before = _digest(ENV_EXAMPLE)

    authz_api.client.patch(
        URL,
        headers=authz_api.authorization_admin,
        json={"expected_revision": 0, "values": {"OCR_DPI": "150"}},
    )
    authz_api.client.delete(
        f"{URL}/OCR_DPI?expected_revision=1",
        headers=authz_api.authorization_admin,
    )

    assert _digest(ENV_EXAMPLE) == before


def test_overrides_are_stored_outside_the_repository(
    authz_api, no_validation, isolated_store: Path
):
    authz_api.client.patch(
        URL,
        headers=authz_api.authorization_admin,
        json={"expected_revision": 0, "values": {"OCR_DPI": "150"}},
    )

    stored = json.loads((isolated_store / "overrides.json").read_text(encoding="utf-8"))

    assert stored["values"] == {"OCR_DPI": "150"}
    assert not (PROJECT_ROOT / "overrides.json").exists()


def test_hosted_mode_refuses_every_mutation(authz_api, monkeypatch):
    monkeypatch.setattr(type(settings), "is_hosted", property(lambda self: True))

    patch = authz_api.client.patch(
        URL,
        headers=authz_api.authorization_admin,
        json={"expected_revision": 0, "values": {"OCR_DPI": "150"}},
    )
    restart = authz_api.client.post(
        f"{URL}/restarts",
        headers=authz_api.authorization_admin,
        json={"expected_revision": 0},
    )

    assert patch.status_code == 409
    assert patch.headers["X-Error-Code"] == "not_available_in_hosted_mode"
    assert restart.status_code == 409
    assert restart.headers["X-Error-Code"] == "not_available_in_hosted_mode"


def test_hosted_mode_still_allows_reading_the_inventory(authz_api, monkeypatch):
    monkeypatch.setattr(type(settings), "is_hosted", property(lambda self: True))

    assert _get(authz_api).status_code == 200


def test_a_restart_is_refused_without_a_supervisor(authz_api, no_validation):
    authz_api.client.patch(
        URL,
        headers=authz_api.authorization_admin,
        json={"expected_revision": 0, "values": {"OCR_DPI": "150"}},
    )

    response = authz_api.client.post(
        f"{URL}/restarts",
        headers=authz_api.authorization_admin,
        json={"expected_revision": 1},
    )

    assert response.status_code == 503
    assert response.headers["X-Error-Code"] == "restart_not_supervised"


def test_a_restart_is_refused_when_nothing_is_pending(authz_api, monkeypatch):
    _supervise(monkeypatch)

    response = authz_api.client.post(
        f"{URL}/restarts",
        headers=authz_api.authorization_admin,
        json={"expected_revision": 0},
    )

    assert response.status_code == 409
    assert response.headers["X-Error-Code"] == "restart_nothing_to_apply"


def test_a_restart_returns_202_with_a_request_id(authz_api, no_validation, monkeypatch):
    route_module = _supervise(monkeypatch)
    started = []

    monkeypatch.setattr(
        route_module.RestartCoordinator, "start", lambda self: started.append(self)
    )
    authz_api.client.patch(
        URL,
        headers=authz_api.authorization_admin,
        json={"expected_revision": 0, "values": {"OCR_DPI": "150"}},
    )

    response = authz_api.client.post(
        f"{URL}/restarts",
        headers=authz_api.authorization_admin,
        json={"expected_revision": 1},
    )

    assert response.status_code == 202
    data = response.json()["data"]
    assert data["request_id"]
    assert data["target_revision"] == 1
    assert data["in_flight"]["total"] == 0
    assert started


def test_a_second_restart_is_refused_while_one_is_running(
    authz_api, no_validation, monkeypatch
):
    route_module = _supervise(monkeypatch)

    monkeypatch.setattr(route_module.RestartCoordinator, "start", lambda self: None)
    authz_api.client.patch(
        URL,
        headers=authz_api.authorization_admin,
        json={"expected_revision": 0, "values": {"OCR_DPI": "150"}},
    )
    authz_api.client.post(
        f"{URL}/restarts",
        headers=authz_api.authorization_admin,
        json={"expected_revision": 1},
    )

    response = authz_api.client.post(
        f"{URL}/restarts",
        headers=authz_api.authorization_admin,
        json={"expected_revision": 1},
    )

    assert response.status_code == 409
    assert response.headers["X-Error-Code"] == "restart_already_in_progress"


def test_restart_status_reports_the_recorded_request(authz_api):
    settings_overrides.write_restart_request(
        RestartRequest(
            request_id="req-1",
            state="restarting",
            target_revision=3,
            requested_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
        )
    )

    response = authz_api.client.get(
        f"{URL}/restarts/req-1", headers=authz_api.authorization_admin
    )

    assert response.status_code == 200
    assert response.json()["data"]["restart"]["state"] == "restarting"


def test_an_unknown_restart_request_is_a_404(authz_api):
    response = authz_api.client.get(
        f"{URL}/restarts/nope", headers=authz_api.authorization_admin
    )

    assert response.status_code == 404
