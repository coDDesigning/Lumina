from dataclasses import replace
from datetime import datetime

import pytest
from sqlalchemy import select

import routes.legal as legal_routes
import services.user as user_service_module
from backend.app.config import load_settings, settings
from backend.app.legal import (
    PRIVACY_POLICY_KEY,
    PRIVACY_POLICY_VERSION,
    TERMS_POLICY_KEY,
    TERMS_POLICY_VERSION,
)
from backend.app.models import PolicyAcknowledgement

REGISTRATION = {
    "name": "Deniz",
    "email": "deniz@example.com",
    "password": "Strong-password!",
}


def _enable_legal_policies(monkeypatch: pytest.MonkeyPatch, enabled: bool) -> None:
    patched = replace(settings, legal_policies_enabled=enabled)
    monkeypatch.setattr(user_service_module, "settings", patched)
    monkeypatch.setattr(legal_routes, "settings", patched)


def test_legal_policies_are_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LEGAL_POLICIES_ENABLED", raising=False)

    assert load_settings().legal_policies_enabled is False


@pytest.mark.parametrize("raw", ["yes", "true", "1", "on"])
def test_legal_policies_accept_yes_style_values(
    monkeypatch: pytest.MonkeyPatch, raw: str
) -> None:
    monkeypatch.setenv("LEGAL_POLICIES_ENABLED", raw)

    assert load_settings().legal_policies_enabled is True


def test_legal_policies_reject_an_unknown_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LEGAL_POLICIES_ENABLED", "maybe")

    with pytest.raises(ValueError, match="LEGAL_POLICIES_ENABLED"):
        load_settings()


def test_config_reports_disabled_without_policy_versions(
    api_context, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_legal_policies(monkeypatch, False)

    response = api_context.client.get("/api/legal/config")

    assert response.status_code == 200
    assert response.json()["data"] == {
        "enabled": False,
        "terms_version": None,
        "privacy_version": None,
        "effective_date": None,
    }


def test_config_reports_published_versions_when_enabled(
    api_context, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_legal_policies(monkeypatch, True)

    response = api_context.client.get("/api/legal/config")

    assert response.status_code == 200
    assert response.json()["data"] == {
        "enabled": True,
        "terms_version": TERMS_POLICY_VERSION,
        "privacy_version": PRIVACY_POLICY_VERSION,
        "effective_date": "2026-09-12",
    }


def test_disabled_registration_records_no_acknowledgement(
    api_context, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_legal_policies(monkeypatch, False)

    response = api_context.client.post("/api/auth/register", json=REGISTRATION)

    assert response.status_code == 200
    with api_context.session_factory() as session:
        assert session.scalars(select(PolicyAcknowledgement)).all() == []


def test_enabled_registration_requires_acknowledgement(
    api_context, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_legal_policies(monkeypatch, True)

    response = api_context.client.post("/api/auth/register", json=REGISTRATION)

    assert response.status_code == 400
    assert response.headers["X-Error-Code"] == "policy_acknowledgement_required"
    with api_context.session_factory() as session:
        assert session.scalars(select(PolicyAcknowledgement)).all() == []


def test_enabled_registration_records_versioned_acknowledgements(
    api_context, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_legal_policies(monkeypatch, True)

    response = api_context.client.post(
        "/api/auth/register",
        json={**REGISTRATION, "policies_acknowledged": True},
    )

    assert response.status_code == 200
    with api_context.session_factory() as session:
        rows = session.scalars(select(PolicyAcknowledgement)).all()
        assert {(row.policy_key, row.policy_version) for row in rows} == {
            (TERMS_POLICY_KEY, TERMS_POLICY_VERSION),
            (PRIVACY_POLICY_KEY, PRIVACY_POLICY_VERSION),
        }
        assert all(isinstance(row.acknowledged_at, datetime) for row in rows)
