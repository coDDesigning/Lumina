"""The self-hosted entrypoint's routing contract, asserted over its configuration.

These are text assertions over ops/nginx/, so they prove intent rather than
behaviour: they exist to stop a later edit from quietly reintroducing a
distribution-wide SPA fallback, stripping the /api prefix, or attaching a second
set of security headers to proxied API responses. The behavioural gate is the
container smoke test in the Container quality CI job, which drives a running
image through the same path table as terraform/modules/frontend's viewer
function.
"""

import re
from pathlib import Path

import pytest

NGINX_DIRECTORY = Path(__file__).resolve().parents[1] / "ops" / "nginx"
DEFAULT_CONF = NGINX_DIRECTORY / "default.conf"
PROXY_CONF = NGINX_DIRECTORY / "proxy-api.conf"
HEADERS_CONF = NGINX_DIRECTORY / "security-headers.conf"
TERRAFORM_FRONTEND_MAIN = (
    Path(__file__).resolve().parents[1]
    / "terraform"
    / "modules"
    / "frontend"
    / "main.tf"
)
TLS_ONLY_DIRECTIVES = {"upgrade-insecure-requests"}


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _uncommented(path: Path) -> str:
    lines = (line.split("#", 1)[0] for line in _text(path).splitlines())
    return "\n".join(lines)


@pytest.mark.parametrize("path", [DEFAULT_CONF, PROXY_CONF, HEADERS_CONF])
def test_the_configuration_files_exist(path: Path) -> None:
    assert path.is_file()


def test_no_blanket_error_fallback_rewrites_failures_to_the_shell() -> None:
    body = _uncommented(DEFAULT_CONF)
    assert not re.search(r"error_page\s+[^;]*\b40[34]\b[^;]*index\.html", body)
    assert not re.search(r"error_page\s+[^;]*\b404\b\s*/index\.html", body)


def test_upstream_errors_are_never_intercepted() -> None:
    assert "proxy_intercept_errors off;" in _uncommented(PROXY_CONF)


def test_the_api_prefix_reaches_the_application_unmodified() -> None:
    body = _uncommented(DEFAULT_CONF)
    assert "proxy_pass http://$lumina_api$request_uri;" in body
    assert "rewrite" not in body


def test_both_api_shapes_are_proxied() -> None:
    body = _uncommented(DEFAULT_CONF)
    assert re.search(r"location\s+~\s+\^/api\(/\|\$\)", body)


def test_the_same_origin_health_endpoints_map_onto_the_application_probes() -> None:
    body = _uncommented(DEFAULT_CONF)
    assert "location = /api/health/live" in body
    assert "location = /api/health/ready" in body
    assert "proxy_pass http://$lumina_api/health/live;" in body
    assert "proxy_pass http://$lumina_api/health/ready;" in body


def test_the_container_probe_does_not_depend_on_the_application() -> None:
    body = _uncommented(DEFAULT_CONF)
    assert "location = /healthz" in body
    healthz = body.split("location = /healthz", 1)[1].split("}", 1)[0]
    assert "proxy_pass" not in healthz


def test_assets_are_immutable_and_never_fall_back_to_the_shell() -> None:
    body = _uncommented(DEFAULT_CONF)
    assert "location ^~ /assets/" in body
    assets = body.split("location ^~ /assets/", 1)[1].split("\n    }", 1)[0]
    assert "try_files $uri =404;" in assets
    assert "index.html" not in assets
    assert "max-age=31536000" in assets and "immutable" in assets


def test_the_shell_is_served_only_for_extensionless_get_and_head() -> None:
    body = _uncommented(DEFAULT_CONF)
    assert re.search(r"location\s+~\s+\\.\[\^/\]\*\$", body)
    assert re.search(r"\$request_method\s+!~\s+\^\(GET\|HEAD\)\$", body)


def test_the_api_locations_attach_no_response_headers_of_their_own() -> None:
    assert "add_header" not in _uncommented(PROXY_CONF)


def test_the_shell_headers_are_not_declared_at_server_level() -> None:
    body = _uncommented(DEFAULT_CONF)
    server = body.split("server {", 1)[1]
    before_first_location = server.split("location", 1)[0]
    assert "add_header" not in before_first_location


def test_the_browser_policy_omits_the_directives_that_assume_tls() -> None:
    body = _uncommented(HEADERS_CONF)
    assert "upgrade-insecure-requests" not in body
    assert "Strict-Transport-Security" not in body


def _directives(policy: str) -> dict[str, set[str]]:
    parsed: dict[str, set[str]] = {}
    for chunk in policy.split(";"):
        sources = chunk.split()
        if sources:
            parsed[sources[0]] = set(sources[1:])
    return parsed


def _self_hosted_policy() -> str:
    match = re.search(
        r'add_header\s+Content-Security-Policy\s+"([^"]+)"', _uncommented(HEADERS_CONF)
    )
    assert match, "security-headers.conf declares no Content-Security-Policy"
    return match.group(1)


def _hosted_policy() -> str:
    body = "\n".join(
        line.split("#", 1)[0]
        for line in TERRAFORM_FRONTEND_MAIN.read_text(encoding="utf-8").splitlines()
    )
    resolved = dict(re.findall(r'^  (\w+)\s*=\s*"([^"]*)"\s*$', body, re.MULTILINE))
    resolved["connect_src"] = "'self'"

    block = body.split('content_security_policy = join(" ", [', 1)[1].split("])", 1)[0]
    policy = " ".join(re.findall(r'"([^"]+)"', block))
    return re.sub(r"\$\{local\.(\w+)\}", lambda m: resolved[m.group(1)], policy)


def test_the_browser_policy_matches_the_hosted_distribution() -> None:
    self_hosted = _directives(_self_hosted_policy())
    hosted = _directives(_hosted_policy())

    assert set(hosted) - set(self_hosted) == TLS_ONLY_DIRECTIVES
    assert set(self_hosted) - set(hosted) == set()
    for directive, sources in self_hosted.items():
        assert sources == hosted[directive], directive

    headers = _uncommented(HEADERS_CONF)
    assert "nosniff" in headers
    assert "DENY" in headers


def test_the_hosted_policy_admits_the_adsense_loader() -> None:
    hosted = _directives(_hosted_policy())
    assert "https://pagead2.googlesyndication.com" in hosted["script-src"]
    assert "https://tpc.googlesyndication.com" in hosted["frame-src"]
    assert "https://googleads.g.doubleclick.net" in hosted["frame-src"]


def test_upstream_resolution_is_deferred_so_a_recreated_api_is_followed() -> None:
    body = _uncommented(DEFAULT_CONF)
    assert "resolver" in body
    assert "set $lumina_api" in body


def _nginx_size_to_bytes(value: str) -> int:
    units = {"k": 1024, "m": 1024 * 1024, "g": 1024 * 1024 * 1024}
    suffix = value[-1].lower()
    if suffix in units:
        return int(value[:-1]) * units[suffix]
    return int(value)


def _env_example_int(key: str) -> int:
    text = (Path(__file__).resolve().parents[1] / ".env.example").read_text(
        encoding="utf-8"
    )
    match = re.search(rf"^{key}=(\d+)\s*$", text, re.MULTILINE)
    assert match, f"{key} is missing from .env.example"
    return int(match.group(1))


def test_the_proxy_body_limit_clears_the_documented_upload_ceiling() -> None:
    from backend.app.request_size import MULTIPART_OVERHEAD_BYTES

    body = _uncommented(DEFAULT_CONF)
    api_block = body.split("location ~ ^/api(/|$)", 1)[1]
    match = re.search(r"client_max_body_size\s+(\S+?);", api_block)
    assert match

    proxy_limit = _nginx_size_to_bytes(match.group(1))
    application_limit = (
        _env_example_int("MAX_UPLOAD_SIZE_BYTES") + MULTIPART_OVERHEAD_BYTES
    )

    assert proxy_limit >= application_limit, (
        "The reverse proxy would refuse an upload the application is configured "
        "to accept. Raise client_max_body_size in ops/nginx/default.conf to at "
        f"least {application_limit} bytes, or lower MAX_UPLOAD_SIZE_BYTES."
    )


def test_the_static_locations_do_not_inherit_the_upload_limit() -> None:
    body = _uncommented(DEFAULT_CONF)
    server_prelude = body.split("server {", 1)[1].split("location", 1)[0]
    assert "client_max_body_size  1m;" in server_prelude


def test_generation_is_not_cut_off_before_the_application_deadline() -> None:
    body = _uncommented(PROXY_CONF)
    for directive in ("proxy_read_timeout", "proxy_send_timeout"):
        match = re.search(rf"{directive}\s+(\d+)s;", body)
        assert match
        assert int(match.group(1)) >= 300


def test_the_forwarded_host_keeps_the_port_the_browser_used() -> None:
    body = _uncommented(PROXY_CONF)
    assert "proxy_set_header Host              $http_host;" in body
    assert "$host;" not in body


def test_the_client_address_and_scheme_are_forwarded() -> None:
    body = _uncommented(PROXY_CONF)
    assert "X-Forwarded-For   $remote_addr;" in body
    assert "X-Forwarded-Proto $scheme;" in body


def test_a_client_supplied_forwarded_for_cannot_survive_the_proxy() -> None:
    body = _uncommented(PROXY_CONF)
    assert "$proxy_add_x_forwarded_for" not in body
