"""Security headers and Content Security Policy middleware."""

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from backend.app.config import Settings


def build_csp_header(settings: Settings) -> str:
    """Build the Content-Security-Policy header value based on deployment and ad settings."""
    if settings.enable_hosted_ads and settings.is_hosted:
        script_sources = " ".join(["'self'", "https://media.ethicalads.io"])
        connect_sources = " ".join(["'self'", "https://server.ethicalads.io"])
        img_sources = " ".join(["'self'", "data:", "https://media.ethicalads.io", "https://server.ethicalads.io"])
        return (
            f"default-src 'self'; "
            f"script-src {script_sources}; "
            f"style-src 'self' 'unsafe-inline'; "
            f"img-src {img_sources}; "
            f"connect-src {connect_sources}; "
            f"frame-src 'none'; "
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


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Adds defensive browser security and Content-Security-Policy headers."""

    def __init__(self, app, settings: Settings):
        super().__init__(app)
        self.settings = settings
        self.csp_header = build_csp_header(settings)

    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Content-Security-Policy"] = self.csp_header
        return response
