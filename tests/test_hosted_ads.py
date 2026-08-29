"""Tests for optional hosted advertising configuration, security headers, and privacy telemetry."""

import pytest
from fastapi.testclient import TestClient

from backend.app.config import (
    MODE_HOSTED,
    MODE_SELF_HOSTED,
    load_settings,
)
from backend.app.security_headers import build_csp_header
from main import app


def test_self_hosted_disallows_enable_hosted_ads(monkeypatch):
    """Self-hosted deployment mode strictly refuses ENABLE_HOSTED_ADS=true."""
    monkeypatch.setenv("DEPLOYMENT_MODE", MODE_SELF_HOSTED)
    monkeypatch.setenv("ENABLE_HOSTED_ADS", "true")

    with pytest.raises(
        ValueError,
        match="Self-hosted deployment mode does not permit ENABLE_HOSTED_ADS=true",
    ):
        load_settings()


def test_hosted_mode_allows_enable_hosted_ads(monkeypatch):
    """Hosted deployment mode allows toggling ENABLE_HOSTED_ADS."""
    monkeypatch.setenv("DEPLOYMENT_MODE", MODE_HOSTED)
    monkeypatch.setenv(
        "DATABASE_URL", "postgresql+psycopg://user:pass@localhost:5432/lumina_ci"
    )
    monkeypatch.setenv(
        "JWT_SECRET_KEY", "test-secret-key-that-is-at-least-32-chars-long!"
    )
    monkeypatch.setenv("STORAGE_NAMESPACE", "hosted-test")
    monkeypatch.setenv("BOOTSTRAP_ADMIN_EMAIL", "admin@example.com")
    monkeypatch.setenv(
        "BOOTSTRAP_ADMIN_TOKEN", "token-at-least-32-chars-long-visible-ascii-here!"
    )
    monkeypatch.setenv("EMAIL_VERIFICATION_REQUIRED", "false")
    monkeypatch.setenv("ENABLE_HOSTED_ADS", "true")
    monkeypatch.setenv("HOSTED_ADS_PROVIDER", "ethicalads")
    monkeypatch.setenv("HOSTED_ADS_PUBLISHER_ID", "lumina-pub-123")

    loaded = load_settings()
    assert loaded.enable_hosted_ads is True
    assert loaded.hosted_ads_provider == "ethicalads"
    assert loaded.hosted_ads_publisher_id == "lumina-pub-123"


def test_csp_header_content_in_self_hosted_mode(monkeypatch):
    """In self-hosted mode or when ads are disabled, CSP strictly contains no external ad domains."""
    monkeypatch.setenv("DEPLOYMENT_MODE", MODE_SELF_HOSTED)
    monkeypatch.setenv("ENABLE_HOSTED_ADS", "false")
    s = load_settings()

    csp = build_csp_header(s)
    assert "ethicalads.io" not in csp
    assert "default-src 'self'" in csp
    assert "script-src 'self'" in csp


def test_csp_header_content_with_hosted_ads(monkeypatch):
    """When hosted ads are enabled, CSP contains the narrow ethicalads allowlist."""
    monkeypatch.setenv("DEPLOYMENT_MODE", MODE_HOSTED)
    monkeypatch.setenv(
        "DATABASE_URL", "postgresql+psycopg://user:pass@localhost:5432/lumina_ci"
    )
    monkeypatch.setenv(
        "JWT_SECRET_KEY", "test-secret-key-that-is-at-least-32-chars-long!"
    )
    monkeypatch.setenv("STORAGE_NAMESPACE", "hosted-test")
    monkeypatch.setenv("BOOTSTRAP_ADMIN_EMAIL", "admin@example.com")
    monkeypatch.setenv(
        "BOOTSTRAP_ADMIN_TOKEN", "token-at-least-32-chars-long-visible-ascii-here!"
    )
    monkeypatch.setenv("EMAIL_VERIFICATION_REQUIRED", "false")
    monkeypatch.setenv("ENABLE_HOSTED_ADS", "true")
    s = load_settings()

    csp = build_csp_header(s)
    assert "https://media.ethicalads.io" in csp
    assert "https://server.ethicalads.io" in csp


def test_get_ad_config_endpoint_default():
    """Default deployment returns ads disabled without provider details."""
    client = TestClient(app)
    res = client.get("/api/ads/config")
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert "enabled" in data["data"]


def test_security_headers_present_on_response():
    """Standard security headers and CSP are attached by middleware to HTTP responses."""
    client = TestClient(app)
    res = client.get("/")
    assert res.status_code == 200
    assert res.headers.get("X-Content-Type-Options") == "nosniff"
    assert res.headers.get("X-Frame-Options") == "DENY"
    assert res.headers.get("Referrer-Policy") == "no-referrer"
    assert "Content-Security-Policy" in res.headers


def test_telemetry_rejects_extra_study_content_payloads():
    """Ad telemetry strictly rejects any extraneous fields like course_id, prompt, or user context."""
    client = TestClient(app)

    # Valid telemetry payload
    res = client.post(
        "/api/ads/telemetry/impression",
        json={"placement": "sidebar", "provider": "ethicalads", "status": "rendered"},
    )
    assert res.status_code == 200

    # Payload with leaked study context is rejected by extra='forbid'
    leaked_payload = {
        "placement": "sidebar",
        "provider": "ethicalads",
        "status": "rendered",
        "course_id": 42,
        "document_name": "quantum_physics.pdf",
        "prompt": "What is the Hamiltonian?",
    }
    res_leaked = client.post("/api/ads/telemetry/impression", json=leaked_payload)
    assert res_leaked.status_code == 422
