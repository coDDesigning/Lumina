"""The routing contract the application answers for everything outside /api.

These drive the real application, with its real middleware, so they assert
behaviour rather than intent: the reverse proxy this replaced was covered by
text assertions over its configuration, which could only ever prove what the
file said. The paths exercised here are the same ones
terraform/modules/frontend/viewer_request.js implements for the hosted
distribution.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.spa import (
    IMMUTABLE_CACHE_CONTROL,
    REVALIDATE_CACHE_CONTROL,
    SinglePageApplication,
    has_extension,
)
from main import app

# Large enough that the compression threshold is crossed, so one fixture serves
# both the routing tests and the encoding test.
SHELL = "<!doctype html><title>Lumina</title>" + ("<!-- shell -->" * 200)
BUNDLE = "export const version = 1;" + ("// padding\n" * 200)


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    web_root = tmp_path / "web"
    (web_root / "assets").mkdir(parents=True)
    (web_root / "index.html").write_text(SHELL, encoding="utf-8")
    (web_root / "assets" / "app-abc123.js").write_text(BUNDLE, encoding="utf-8")
    (web_root / "favicon.ico").write_bytes(b"\x00\x00\x01\x00")

    original_default = app.router.default
    app.router.default = SinglePageApplication(web_root, original_default)
    try:
        yield TestClient(app)
    finally:
        app.router.default = original_default


def test_the_root_serves_the_shell(client: TestClient) -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["cache-control"] == REVALIDATE_CACHE_CONTROL


def test_a_client_side_route_serves_the_shell_unchanged(client: TestClient) -> None:
    deep_link = client.get("/courses/123/progress")

    assert deep_link.status_code == 200
    assert deep_link.text == client.get("/").text


def test_a_client_side_route_is_served_without_testing_that_it_exists(
    client: TestClient,
) -> None:
    assert client.get("/there/is/no/such/page").status_code == 200


def test_build_output_is_immutable(client: TestClient) -> None:
    response = client.get("/assets/app-abc123.js")

    assert response.status_code == 200
    assert response.headers["cache-control"] == IMMUTABLE_CACHE_CONTROL


def test_a_missing_hash_is_a_real_404_and_never_the_shell(client: TestClient) -> None:
    response = client.get("/assets/app-deleted.js")

    assert response.status_code == 404
    # Serving the shell here would reach the browser as a module MIME-type
    # error rather than a missing file.
    assert "<!doctype html>" not in response.text.lower()
    assert response.json()["detail"]


def test_a_missing_file_outside_assets_is_also_a_404(client: TestClient) -> None:
    assert client.get("/robots.txt").status_code == 404


def test_a_file_that_exists_outside_assets_revalidates(client: TestClient) -> None:
    response = client.get("/favicon.ico")

    assert response.status_code == 200
    assert response.headers["cache-control"] == REVALIDATE_CACHE_CONTROL


def test_the_shell_answers_only_get_and_head(client: TestClient) -> None:
    assert client.head("/dashboard").status_code == 200
    assert client.post("/dashboard").status_code == 405


def test_an_unknown_api_path_keeps_the_json_error_contract(client: TestClient) -> None:
    response = client.get("/api/definitely-not-a-route")

    assert response.status_code == 404
    assert response.json()["detail"]


def test_an_unknown_api_path_is_not_turned_into_a_method_error(
    client: TestClient,
) -> None:
    # The shell answers GET and HEAD only. If it were mounted rather than
    # installed as the router's fallback it would claim this path and answer
    # 405, hiding the fact that the route does not exist.
    assert client.post("/api/definitely-not-a-route").status_code == 404


def test_the_trailing_slash_redirect_still_applies_to_the_api(
    client: TestClient,
) -> None:
    response = client.get("/api/ads/config/", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"].endswith("/api/ads/config")


def test_the_shell_is_compressed(client: TestClient) -> None:
    response = client.get("/", headers={"Accept-Encoding": "gzip"})

    assert response.headers["content-encoding"] == "gzip"


def test_the_request_id_is_echoed_on_a_static_response(client: TestClient) -> None:
    response = client.get("/", headers={"X-Request-ID": "abc123"})

    assert response.headers["X-Request-ID"] == "abc123"


def test_the_shell_gets_a_browser_policy_and_the_api_does_not(
    client: TestClient,
) -> None:
    shell = client.get("/").headers["content-security-policy"]
    api = client.get("/api/definitely-not-a-route").headers["content-security-policy"]

    assert "default-src 'self'" in shell
    assert "default-src 'none'" in api


def test_the_referrer_policy_differs_between_the_shell_and_the_api(
    client: TestClient,
) -> None:
    assert (
        client.get("/").headers["referrer-policy"] == "strict-origin-when-cross-origin"
    )
    assert (
        client.get("/api/definitely-not-a-route").headers["referrer-policy"]
        == "no-referrer"
    )


@pytest.mark.parametrize(
    "escape",
    [
        "/assets/%2e%2e%2f%2e%2e%2fsecret.txt",
        "/assets/..%2f..%2fsecret.txt",
        "/%2e%2e%2fsecret.txt",
    ],
)
def test_a_traversal_attempt_cannot_escape_the_build_output(
    tmp_path: Path, escape: str
) -> None:
    # Percent-encoded, because a bare ".." is collapsed by the client before it
    # is ever sent. The server decodes these into the path, so this is the
    # shape a traversal actually arrives in.
    web_root = tmp_path / "web"
    (web_root / "assets").mkdir(parents=True)
    (web_root / "index.html").write_text(SHELL, encoding="utf-8")
    (tmp_path / "secret.txt").write_text("not part of the build output")

    original_default = app.router.default
    app.router.default = SinglePageApplication(web_root, original_default)
    try:
        response = TestClient(app).get(escape)
    finally:
        app.router.default = original_default

    assert response.status_code == 404
    assert "not part of the build output" not in response.text


@pytest.mark.parametrize(
    ("route_path", "expected"),
    [
        ("/assets/app-abc123.js", True),
        ("/favicon.ico", True),
        ("/", False),
        ("/courses/123/progress", False),
        ("/courses/v1.2/notes", False),
    ],
)
def test_only_a_dotted_final_segment_asks_for_a_file(
    route_path: str, expected: bool
) -> None:
    assert has_extension(route_path) is expected
