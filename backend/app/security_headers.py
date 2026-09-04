"""Standard browser response protections, applied at the application boundary.

The hosted CloudFront distribution already attaches these headers to everything
it serves, but it is not the only boundary Lumina runs behind: a self-hosted
Compose deployment has no CDN at all, and the hosted stack answers on the ALB
hostname directly until DNS is cut over. Setting them here means the guarantee
travels with the application rather than with one topology, and the CDN policy
overrides these with its own equivalents where it is in front.

This application serves three kinds of response from one origin, so there are
three policies rather than one. JSON gets ``default-src 'none'``, which is the
correct policy for an API. The interactive documentation loads Swagger UI from
one named CDN host and gets a policy naming that exact origin. The built
interface (backend/app/spa.py) is a browsing context and gets the policy the
hosted CloudFront distribution declares, so the two stay comparable; the
self-hosted deployment serves no ads and so gets a narrower one still. No
policy here uses a scheme or host wildcard.

HSTS is a promise a browser remembers for a year, so it is emitted only where
the deployment says TLS is terminated in front of the API. See
docs/authentication.md.
"""

from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

from backend.app.config import Settings

CONTENT_SECURITY_POLICY_HEADER = b"content-security-policy"
STRICT_TRANSPORT_SECURITY_HEADER = b"strict-transport-security"
CONTENT_TYPE_OPTIONS_HEADER = b"x-content-type-options"
FRAME_OPTIONS_HEADER = b"x-frame-options"
REFERRER_POLICY_HEADER = b"referrer-policy"

# What an API that returns JSON and owns no browsing context needs: nothing
# may load, nothing may frame it, and no form or base tag inside a response
# can be aimed anywhere.
API_CONTENT_SECURITY_POLICY = (
    "default-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)

# FastAPI serves Swagger UI and ReDoc from jsdelivr with an inline bootstrap
# script, so this policy names that host exactly and allows inline script and
# style only on these paths. It is the loosest policy in the application and it
# applies to three documentation URLs.
DOCS_CDN_ORIGIN = "https://cdn.jsdelivr.net"
DOCS_FAVICON_ORIGIN = "https://fastapi.tiangolo.com"
DOCS_CONTENT_SECURITY_POLICY = (
    "default-src 'none'; "
    "base-uri 'none'; "
    "form-action 'none'; "
    "frame-ancestors 'none'; "
    f"script-src 'self' 'unsafe-inline' {DOCS_CDN_ORIGIN}; "
    f"style-src 'self' 'unsafe-inline' {DOCS_CDN_ORIGIN}; "
    f"font-src 'self' {DOCS_CDN_ORIGIN}; "
    f"img-src 'self' data: {DOCS_FAVICON_ORIGIN}; "
    "connect-src 'self'; "
    "worker-src 'self' blob:"
)

# An API response has no referrer worth leaking and its URLs name resources,
# so nothing is sent rather than an origin.
REFERRER_POLICY = "no-referrer"

# The interface is a browsing context and does have outbound links, so it sends
# the origin cross-site and the full URL same-site. This is the value the
# hosted CloudFront response-headers policy sets in
# terraform/modules/frontend/main.tf.
STATIC_REFERRER_POLICY = "strict-origin-when-cross-origin"

CONTENT_TYPE_OPTIONS = "nosniff"
FRAME_OPTIONS = "DENY"

DOCUMENTATION_PATHS = frozenset({"/docs", "/docs/oauth2-redirect", "/redoc"})

# Everything the application answers as an API rather than as a page. The
# remaining paths are the built interface served by backend/app/spa.py, which
# is a browsing context and needs a policy that lets it load its own bundle.
API_PATH_PREFIXES = ("/api/", "/health/")
API_PATHS = frozenset({"/api", "/openapi.json"})

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]


def build_csp_header(settings: Settings | None = None) -> str:
    """Build the Content-Security-Policy header value based on deployment and ad settings."""
    if settings and settings.enable_hosted_ads and settings.is_hosted:
        script_sources = " ".join(
            [
                "'self'",
                "https://media.ethicalads.io",
                "https://server.ethicalads.io",
                "https://pagead2.googlesyndication.com",
                "https://adservice.google.com",
                "https://www.googletagservices.com",
                "https://tpc.googlesyndication.com",
            ]
        )
        connect_sources = " ".join(
            [
                "'self'",
                "https://server.ethicalads.io",
                "https://pagead2.googlesyndication.com",
                "https://googleads.g.doubleclick.net",
            ]
        )
        img_sources = " ".join(
            [
                "'self'",
                "data:",
                "blob:",
                "https://media.ethicalads.io",
                "https://server.ethicalads.io",
                "https://pagead2.googlesyndication.com",
                "https://tpc.googlesyndication.com",
            ]
        )
        frame_sources = " ".join(
            [
                "'self'",
                "https://googleads.g.doubleclick.net",
                "https://tpc.googlesyndication.com",
            ]
        )
        return (
            f"default-src 'self'; "
            f"base-uri 'self'; "
            f"font-src 'self'; "
            f"form-action 'self'; "
            f"frame-ancestors 'none'; "
            f"script-src {script_sources} 'unsafe-inline'; "
            f"style-src 'self' 'unsafe-inline'; "
            f"img-src {img_sources}; "
            f"connect-src {connect_sources}; "
            f"frame-src {frame_sources}; "
            f"object-src 'none';"
        )
    # No ad host appears here at all: a self-hosted deployment serves no ads,
    # so its interface gets a strictly narrower policy than the hosted one.
    return (
        "default-src 'self'; "
        "base-uri 'self'; "
        "font-src 'self'; "
        "form-action 'self'; "
        "frame-ancestors 'none'; "
        "script-src 'self'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob:; "
        "connect-src 'self'; "
        "frame-src 'none'; "
        "object-src 'none';"
    )


def is_api_path(path: str) -> bool:
    return path in API_PATHS or path.startswith(API_PATH_PREFIXES)


def content_security_policy_for(path: str, settings: Settings | None = None) -> str:
    """The policy for one path: documentation, API, or the interface.

    The application serves the built interface from the same origin as the API
    (see backend/app/spa.py), so one policy can no longer cover both.
    ``default-src 'none'`` is right for JSON and would render the interface as
    a blank page.
    """
    if path in DOCUMENTATION_PATHS:
        return DOCS_CONTENT_SECURITY_POLICY
    if is_api_path(path):
        return API_CONTENT_SECURITY_POLICY
    return build_csp_header(settings)


def referrer_policy_for(path: str) -> str:
    return REFERRER_POLICY if is_api_path(path) else STATIC_REFERRER_POLICY


class SecurityHeadersMiddleware:
    """Attach the audited response headers to every HTTP response.

    Written as plain ASGI rather than ``BaseHTTPMiddleware`` so it also covers
    the responses the request-size limiter rejects before routing, and so it
    costs nothing per request beyond appending headers.

    Existing headers are left alone. Nothing in this application sets these,
    but a route that deliberately needed its own policy should be able to keep
    it rather than have it silently replaced.
    """

    def __init__(
        self,
        app: Callable[[Scope, Receive, Send], Awaitable[None]],
        *,
        hsts_enabled: bool = False,
        hsts_max_age_seconds: int = 31536000,
        settings: Settings | None = None,
    ) -> None:
        self.app = app
        self._settings = settings
        if settings is not None:
            self._hsts_value = (
                f"max-age={settings.hsts_max_age_seconds}; includeSubDomains"
                if settings.hsts_enabled
                else None
            )
        else:
            self._hsts_value = (
                f"max-age={hsts_max_age_seconds}; includeSubDomains"
                if hsts_enabled
                else None
            )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_path = scope.get("path", "")
        policy = content_security_policy_for(request_path, self._settings)
        referrer_policy = referrer_policy_for(request_path)

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = message.setdefault("headers", [])
                present = {name.lower() for name, _ in headers}
                additions: list[tuple[bytes, bytes]] = [
                    (
                        CONTENT_TYPE_OPTIONS_HEADER,
                        CONTENT_TYPE_OPTIONS.encode("latin-1"),
                    ),
                    (FRAME_OPTIONS_HEADER, FRAME_OPTIONS.encode("latin-1")),
                    (REFERRER_POLICY_HEADER, referrer_policy.encode("latin-1")),
                    (CONTENT_SECURITY_POLICY_HEADER, policy.encode("latin-1")),
                ]
                if self._hsts_value is not None:
                    additions.append(
                        (
                            STRICT_TRANSPORT_SECURITY_HEADER,
                            self._hsts_value.encode("latin-1"),
                        )
                    )
                headers.extend(
                    (name, value) for name, value in additions if name not in present
                )
            await send(message)

        await self.app(scope, receive, send_with_headers)
