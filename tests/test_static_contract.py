"""What the application and the hosted distribution must keep agreeing on.

The interface is served two ways: by the application (backend/app/spa.py) for a
self-hosted deployment, and by CloudFront (terraform/modules/frontend/) for the
hosted one. Neither can see the other, so the rules they both implement are
asserted here rather than left to review. The behavioural gate is the container
smoke test in the Container quality CI job, which drives a running image
through the same path table.
"""

import re
from dataclasses import replace
from pathlib import Path

from backend.app.config import MODE_HOSTED, MODE_SELF_HOSTED, settings
from backend.app.security_headers import build_csp_header
from backend.app.spa import (
    IMMUTABLE_CACHE_CONTROL,
    REVALIDATE_CACHE_CONTROL,
    has_extension,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TERRAFORM_FRONTEND = PROJECT_ROOT / "terraform" / "modules" / "frontend"
TERRAFORM_FRONTEND_MAIN = TERRAFORM_FRONTEND / "main.tf"
VIEWER_REQUEST = TERRAFORM_FRONTEND / "viewer_request.js"
PUBLISH_FRONTEND = PROJECT_ROOT / ".github" / "scripts" / "publish-frontend.sh"
DOCKERFILE = PROJECT_ROOT / "Dockerfile"

# The distribution terminates TLS and the self-hosted deployment need not, so
# it declares one directive the application deliberately omits: on
# http://<lan-address>:10312 it would upgrade every same-origin subresource to
# https on a plain HTTP port and nothing would load.
TLS_ONLY_DIRECTIVES = {"upgrade-insecure-requests"}


def _uncommented(path: Path) -> str:
    return "\n".join(
        line.split("#", 1)[0] for line in path.read_text(encoding="utf-8").splitlines()
    )


def _directives(policy: str) -> dict[str, set[str]]:
    parsed: dict[str, set[str]] = {}
    for chunk in policy.split(";"):
        sources = chunk.split()
        if sources:
            parsed[sources[0]] = set(sources[1:])
    return parsed


def _hosted_settings():
    return replace(settings, deployment_mode=MODE_HOSTED, enable_hosted_ads=True)


def _distribution_policy() -> str:
    body = _uncommented(TERRAFORM_FRONTEND_MAIN)
    resolved = dict(re.findall(r'^  (\w+)\s*=\s*"([^"]*)"\s*$', body, re.MULTILINE))
    resolved["connect_src"] = "'self'"

    block = body.split('content_security_policy = join(" ", [', 1)[1].split("])", 1)[0]
    policy = " ".join(re.findall(r'"([^"]+)"', block))
    return re.sub(r"\$\{local\.(\w+)\}", lambda m: resolved[m.group(1)], policy)


def test_the_application_policy_matches_the_hosted_distribution() -> None:
    application = _directives(build_csp_header(_hosted_settings()))
    distribution = _directives(_distribution_policy())

    assert set(distribution) - set(application) == TLS_ONLY_DIRECTIVES
    assert set(application) - set(distribution) == set()
    for directive, sources in application.items():
        assert sources == distribution[directive], directive


def test_the_hosted_policy_admits_the_adsense_loader() -> None:
    hosted = _directives(build_csp_header(_hosted_settings()))

    assert "https://pagead2.googlesyndication.com" in hosted["script-src"]
    assert "https://tpc.googlesyndication.com" in hosted["frame-src"]
    assert "https://googleads.g.doubleclick.net" in hosted["frame-src"]


def test_a_self_hosted_deployment_names_no_ad_host_at_all() -> None:
    self_hosted = build_csp_header(
        replace(settings, deployment_mode=MODE_SELF_HOSTED, enable_hosted_ads=False)
    )

    assert "ethicalads" not in self_hosted
    assert "googlesyndication" not in self_hosted
    assert "doubleclick" not in self_hosted
    # Still able to load its own bundle, which default-src 'none' would forbid.
    assert "default-src 'self'" in self_hosted


def test_no_policy_uses_a_scheme_or_host_wildcard() -> None:
    for policy in (build_csp_header(_hosted_settings()), build_csp_header(settings)):
        for sources in _directives(policy).values():
            for source in sources:
                assert not source.startswith("*")
                assert source not in {"https:", "http:", "data:*"}


def test_the_file_rule_is_spelled_the_same_way_in_both_places() -> None:
    viewer = VIEWER_REQUEST.read_text(encoding="utf-8")

    # If this spelling changes, has_extension must change with it or a deep
    # link will resolve differently on the two deployments.
    assert 'finalSegment.includes(".")' in viewer
    assert 'uri.lastIndexOf("/")' in viewer

    assert has_extension("/assets/index-abc123.js") is True
    assert has_extension("/courses/1/progress") is False
    # The viewer function rewrites "/" and any trailing slash to the shell, and
    # neither has a dotted final segment here either.
    assert has_extension("/") is False
    assert has_extension("/courses/") is False


def test_the_cache_regimes_match_the_hosted_upload() -> None:
    publish = PUBLISH_FRONTEND.read_text(encoding="utf-8")

    def normalised(value: str) -> str:
        return value.replace(" ", "")

    assert normalised(IMMUTABLE_CACHE_CONTROL) in publish
    assert normalised(REVALIDATE_CACHE_CONTROL) in publish


def test_a_forwarded_address_is_believed_only_when_a_proxy_is_declared() -> None:
    """The guarantee the reverse proxy used to provide, now expressed in the CMD.

    utils/rate_limit.py keys per-IP limits on the peer address, and uvicorn
    replaces that address from X-Forwarded-For only when --proxy-headers is
    passed. The container is published directly, so passing it unconditionally
    would let any caller send its own X-Forwarded-For and choose its own
    rate-limit identity.
    """
    cmd = DOCKERFILE.read_text(encoding="utf-8").split("CMD [", 1)[1]

    assert "--proxy-headers" in cmd
    # Guarded by the parameter expansion, so an unset or empty
    # FORWARDED_ALLOW_IPS removes both flags rather than passing an empty one.
    assert "${FORWARDED_ALLOW_IPS:+--proxy-headers --forwarded-allow-ips" in cmd
    assert "--proxy-headers" not in cmd.split("${FORWARDED_ALLOW_IPS:+", 1)[0]
