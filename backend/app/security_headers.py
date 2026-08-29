"""Standard browser response protections, applied at the application boundary.

The hosted CloudFront distribution already attaches these headers to everything
it serves, but it is not the only boundary Lumina runs behind: a self-hosted
Compose deployment has no CDN at all, and the hosted stack answers on the ALB
hostname directly until DNS is cut over. Setting them here means the guarantee
travels with the application rather than with one topology, and the CDN policy
overrides these with its own equivalents where it is in front.

The default policy is written for what this application actually serves: JSON.
``default-src 'none'`` is the correct policy for an API, and the exception is
the interactive documentation, which loads Swagger UI from one named CDN host.
That page gets its own policy naming that exact origin rather than relaxing the
policy everywhere, and no policy here uses a scheme or host wildcard.

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

CONTENT_TYPE_OPTIONS = "nosniff"
FRAME_OPTIONS = "DENY"

DOCUMENTATION_PATHS = frozenset({"/docs", "/docs/oauth2-redirect", "/redoc"})

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
            f"script-src {script_sources} 'unsafe-inline'; "
            f"style-src 'self' 'unsafe-inline'; "
            f"img-src {img_sources}; "
            f"connect-src {connect_sources}; "
            f"frame-src {frame_sources}; "
            f"object-src 'none';"
        )
    return (
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "connect-src 'self'; "
        "frame-src 'none'; "
        "object-src 'none';"
    )


def content_security_policy_for(path: str) -> str:
    return (
        DOCS_CONTENT_SECURITY_POLICY
        if path in DOCUMENTATION_PATHS
        else API_CONTENT_SECURITY_POLICY
    )


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

        policy = content_security_policy_for(scope.get("path", ""))

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
                    (REFERRER_POLICY_HEADER, REFERRER_POLICY.encode("latin-1")),
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
