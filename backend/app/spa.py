"""Serve the built interface beside the API from one origin.

The self-hosted deployment publishes one port. Everything under /api belongs
to the API, and every other path is either a file in the build output or a
client-side route that resolves to the shell. That is the contract the hosted
CloudFront distribution implements in terraform/modules/frontend/, so the
cache regimes and the file-versus-route rule below are deliberately the same
ones; tests/test_static_contract.py holds them together.

The build output is optional. A checkout that has never run a frontend build
serves the API alone rather than failing to start, which is what keeps the
backend test suite and the `npm run dev` loop independent of Vite.
"""

from collections.abc import Awaitable, Callable, MutableMapping
from pathlib import Path
from typing import Any

from starlette.routing import get_route_path
from starlette.staticfiles import StaticFiles

API_PREFIX = "/api"
ASSET_PREFIX = "/assets/"
INDEX_FILENAME = "index.html"

# Build output is content-hashed, so a name can never describe different bytes
# and is held for a year. Everything else revalidates: the shell names the
# current hashes, and a cached stale shell would pin a browser to a bundle
# that no longer exists.
IMMUTABLE_CACHE_CONTROL = "public, max-age=31536000, immutable"
REVALIDATE_CACHE_CONTROL = "no-cache, max-age=0, must-revalidate"

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]


def has_extension(route_path: str) -> bool:
    """Whether a path asks for a file rather than a client-side route.

    This is finalSegment.includes(".") from
    terraform/modules/frontend/viewer_request.js in Python. A request for a
    file that is not there must be a 404: answering it with the shell would
    reach the browser as a module MIME-type error rather than a missing file.
    """
    return "." in route_path.rsplit("/", 1)[-1]


class SinglePageApplication:
    """The answer for every path routing did not claim.

    Installed as ``Router.default`` rather than mounted at "/". A ``Mount("/")``
    matches every path fully, which would silently take FastAPI's trailing
    slash redirect and its 405-for-the-wrong-method away from /api as a side
    effect. ``default`` runs only after routing has already failed, so the API
    keeps the behaviour it has today.
    """

    def __init__(self, directory: Path, fallback: ASGIApp) -> None:
        self._files = StaticFiles(directory=directory, check_dir=False)
        self._fallback = fallback

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._fallback(scope, receive, send)
            return

        route_path = get_route_path(scope)
        if route_path == API_PREFIX or route_path.startswith(f"{API_PREFIX}/"):
            # An unrecognised API path keeps the application's own JSON error
            # contract. Serving the shell here would hand HTML to a client
            # that is parsing JSON.
            await self._fallback(scope, receive, send)
            return

        if route_path.startswith(ASSET_PREFIX):
            response = await self._files.get_response(
                self._files.get_path(scope), scope
            )
            response.headers["cache-control"] = IMMUTABLE_CACHE_CONTROL
        elif has_extension(route_path):
            response = await self._files.get_response(
                self._files.get_path(scope), scope
            )
            response.headers["cache-control"] = REVALIDATE_CACHE_CONTROL
        else:
            # Served unconditionally, without testing whether the path exists,
            # exactly as the CloudFront viewer function rewrites. There is no
            # 404-to-shell fallback anywhere: that would turn an API failure
            # into an HTML 200.
            response = await self._files.get_response(INDEX_FILENAME, scope)
            response.headers["cache-control"] = REVALIDATE_CACHE_CONTROL

        await response(scope, receive, send)
