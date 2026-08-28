"""The four browser response protections, at the application boundary.

The last two tests drive the real application, including the response the size
limiter refuses before routing. The rest build a small app around the
middleware, because the configured one installs it once at import from settings
and cannot be asked what it would do with Strict-Transport-Security turned on.
"""

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from backend.app.security_headers import (
    API_CONTENT_SECURITY_POLICY,
    CONTENT_TYPE_OPTIONS,
    DOCS_CDN_ORIGIN,
    DOCS_CONTENT_SECURITY_POLICY,
    DOCUMENTATION_PATHS,
    REFERRER_POLICY,
    SecurityHeadersMiddleware,
    content_security_policy_for,
)

AUDITED_HEADERS = (
    "content-security-policy",
    "x-content-type-options",
    "referrer-policy",
)


def _app(*, hsts_enabled: bool = False, max_age: int = 31536000) -> TestClient:
    app = FastAPI()

    @app.get("/api/thing")
    def thing() -> dict[str, str]:
        return {"ok": "yes"}

    @app.get("/api/its-own-policy")
    def its_own_policy():
        from fastapi.responses import JSONResponse

        return JSONResponse(
            {"ok": "yes"},
            headers={"Content-Security-Policy": "default-src 'self'"},
        )

    @app.get("/api/refused")
    def refused() -> dict[str, str]:
        raise HTTPException(status_code=403, detail="deliberate")

    app.add_middleware(
        SecurityHeadersMiddleware,
        hsts_enabled=hsts_enabled,
        hsts_max_age_seconds=max_age,
    )
    return TestClient(app, raise_server_exceptions=False)


# --- the policy --------------------------------------------------------------


def test_an_api_response_denies_everything() -> None:
    """This service returns JSON and owns no browsing context."""
    assert API_CONTENT_SECURITY_POLICY == (
        "default-src 'none'; base-uri 'none'; form-action 'none'; "
        "frame-ancestors 'none'"
    )


def test_no_policy_wildcards_a_scheme_or_every_host() -> None:
    # `data:` and `blob:` name a fetch scheme with no host behind it. The
    # network schemes are the ones that would permit every host on the
    # internet, and `*` permits them under any scheme.
    forbidden = {"*", "https:", "http:", "ws:", "wss:"}
    for policy in (API_CONTENT_SECURITY_POLICY, DOCS_CONTENT_SECURITY_POLICY):
        for directive in policy.split(";"):
            for source in directive.strip().split()[1:]:
                assert source not in forbidden
                assert not source.startswith("*")


def test_the_documentation_pages_name_their_cdn_exactly() -> None:
    assert content_security_policy_for("/docs") == DOCS_CONTENT_SECURITY_POLICY
    assert DOCS_CDN_ORIGIN in DOCS_CONTENT_SECURITY_POLICY
    # The one place inline script and style are allowed, and only for those two.
    assert DOCS_CONTENT_SECURITY_POLICY.count("'unsafe-inline'") == 2
    assert "default-src 'none'" in DOCS_CONTENT_SECURITY_POLICY


@pytest.mark.parametrize("path", sorted(DOCUMENTATION_PATHS))
def test_only_the_documentation_paths_get_the_looser_policy(path: str) -> None:
    assert content_security_policy_for(path) == DOCS_CONTENT_SECURITY_POLICY
    assert content_security_policy_for(path + "/x") == API_CONTENT_SECURITY_POLICY
    assert content_security_policy_for("/api/courses") == API_CONTENT_SECURITY_POLICY


# --- what a response actually carries ----------------------------------------


def test_every_audited_header_is_attached() -> None:
    response = _app().get("/api/thing")

    assert response.status_code == 200
    assert response.headers["content-security-policy"] == API_CONTENT_SECURITY_POLICY
    assert response.headers["x-content-type-options"] == CONTENT_TYPE_OPTIONS
    assert response.headers["referrer-policy"] == REFERRER_POLICY


@pytest.mark.parametrize(
    ("path", "expected_status"), [("/api/refused", 403), ("/api/nothing-here", 404)]
)
def test_a_refused_request_carries_them_too(path: str, expected_status: int) -> None:
    """A rejection is a response, and it is the one an attacker sees most."""
    response = _app().get(path)

    assert response.status_code == expected_status
    for header in AUDITED_HEADERS:
        assert header in response.headers


def test_a_route_keeps_a_policy_it_set_for_itself() -> None:
    response = _app().get("/api/its-own-policy")

    assert response.headers["content-security-policy"] == "default-src 'self'"
    # Exactly one answer per header, whoever supplied it.
    assert (
        len(
            [
                name
                for name, _ in response.headers.raw
                if name.lower() == b"content-security-policy"
            ]
        )
        == 1
    )


# --- Strict-Transport-Security ----------------------------------------------


def test_hsts_is_absent_unless_the_deployment_says_tls_terminates_ahead() -> None:
    """A LAN deployment on plain HTTP must not lock browsers out of itself."""
    response = _app(hsts_enabled=False).get("/api/thing")

    assert "strict-transport-security" not in response.headers


def test_hsts_is_emitted_with_its_configured_lifetime_when_enabled() -> None:
    response = _app(hsts_enabled=True, max_age=1234).get("/api/thing")

    assert (
        response.headers["strict-transport-security"]
        == "max-age=1234; includeSubDomains"
    )


# --- the configured application ----------------------------------------------


def test_the_running_api_attaches_the_headers(api_context) -> None:
    response = api_context.client.get("/api/auth/me")

    # Unauthenticated, which is the point: a rejected request is a response too.
    assert response.status_code == 401
    assert response.headers["content-security-policy"] == API_CONTENT_SECURITY_POLICY
    assert response.headers["x-content-type-options"] == CONTENT_TYPE_OPTIONS
    assert response.headers["referrer-policy"] == REFERRER_POLICY


def test_a_request_refused_before_routing_still_carries_the_headers(
    api_context,
) -> None:
    """The size limiter answers ahead of the router, so it is wrapped, not skipped."""
    response = api_context.client.post(
        "/api/auth/register",
        content=b"x" * 4096,
        headers={
            "Content-Type": "application/json",
            "Content-Length": str(64 * 1024 * 1024),
        },
    )

    assert response.status_code == 413
    for header in AUDITED_HEADERS:
        assert header in response.headers
