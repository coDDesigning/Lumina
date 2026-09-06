"""Test ensuring internal links in tracked Markdown files resolve and forbid file:/// URLs."""

import re
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _get_tracked_markdown_files() -> list[Path]:
    try:
        output = subprocess.check_output(
            ["git", "ls-files", "*.md"],
            cwd=PROJECT_ROOT,
            text=True,
        )
        files = [
            PROJECT_ROOT / line.strip() for line in output.splitlines() if line.strip()
        ]
    except Exception:
        files = [
            p
            for p in PROJECT_ROOT.rglob("*.md")
            if not any(
                part in p.parts
                for part in (".venv", "node_modules", "data", ".user", ".git")
            )
        ]
    return [f for f in files if f.is_file()]


def test_markdown_links_resolve_and_contain_no_file_urls() -> None:
    markdown_files = _get_tracked_markdown_files()
    assert markdown_files, "No tracked markdown files found."

    link_pattern = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
    broken_links: list[str] = []

    for md_file in markdown_files:
        content = md_file.read_text(encoding="utf-8")
        rel_md_path = md_file.relative_to(PROJECT_ROOT).as_posix()

        for match in link_pattern.finditer(content):
            label, url = match.group(1), match.group(2).strip()

            # Ignore external protocols and empty anchors
            if url.startswith(("http://", "https://", "mailto:")) or url.startswith(
                "#"
            ):
                continue

            if url.startswith("file:///"):
                broken_links.append(
                    f"{rel_md_path}: link '[{label}]({url})' uses 'file:///' protocol. "
                    "Use repository-relative markdown links instead."
                )
                continue

            # Strip anchor fragment from relative link
            path_part = url.split("#", 1)[0].strip()
            if not path_part:
                continue

            target_path = (md_file.parent / path_part).resolve()
            if not target_path.exists():
                broken_links.append(
                    f"{rel_md_path}: broken link '[{label}]({url})' -> target '{target_path}' does not exist."
                )

    assert not broken_links, (
        f"Found {len(broken_links)} broken or invalid Markdown link(s):\n"
        + "\n".join(f"  - {err}" for err in broken_links)
    )


def test_docs_run_the_combined_worker_not_the_partial_one() -> None:
    """P2-019: `workers.document_processor` no longer runs generation jobs.

    Any doc that tells an operator to start it as *the* background worker leaves
    every queued study guide / quiz / flashcard unclaimed with its credit spent.
    The combined `workers.worker` is the only entry point that starts both
    queues, and it is what compose and terraform run.
    """
    worker_src = (PROJECT_ROOT / "workers" / "worker.py").read_text(encoding="utf-8")
    assert "run_document_worker" in worker_src and "run_generation_worker" in worker_src

    # Matches both `-m workers.document_processor` and the JSON-array ECS form
    # `"-m", "workers.document_processor"`; a bare module mention in prose is fine.
    run_command = re.compile(
        r'-m",?\s*"?workers\.(document_processor|generation_processor)\b'
    )
    offenders: list[str] = []
    for md_file in (PROJECT_ROOT / "docs").rglob("*.md"):
        for lineno, line in enumerate(
            md_file.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if run_command.search(line):
                rel = md_file.relative_to(PROJECT_ROOT).as_posix()
                offenders.append(f"{rel}:{lineno}: {line.strip()}")

    assert not offenders, (
        "docs run a partial background worker; use `python -m workers.worker`:\n"
        + "\n".join(f"  - {o}" for o in offenders)
    )


def test_readme_local_model_profile_uses_the_single_material_ceiling() -> None:
    """P2-031: the local profile must cap *every* material budget. It does that
    with one ceiling rather than listing five of the thirteen budgets, so a new
    budget cannot silently escape the profile."""
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    block_start = readme.index("Configure the local model profile")
    block = readme[block_start : block_start + 2000]
    assert "MATERIAL_MAX_CHARS_CEILING=" in block
    # The old per-feature lines must be gone from the profile so it cannot drift.
    assert "STUDY_GUIDE_MATERIAL_MAX_CHARS=" not in block


def test_compose_worker_stop_grace_period_covers_generation_timeout() -> None:
    """P2-048: worker stop_grace_period in compose files must cover the 600s
    generation job attempt timeout plus 45s margin (645s)."""
    for filename in ("docker-compose.yml", "docker-compose.hosted.yml"):
        content = (PROJECT_ROOT / filename).read_text(encoding="utf-8")
        assert "stop_grace_period: ${WORKER_STOP_GRACE_PERIOD:-645s}" in content


def test_docs_uvicorn_commands_include_no_access_log() -> None:
    """P2-051: manual uvicorn main:app run commands in docs must include --no-access-log."""
    uvicorn_invocation = re.compile(r"\buvicorn\s+main:app\b")
    offenders: list[str] = []
    for md_file in (PROJECT_ROOT / "docs").rglob("*.md"):
        for lineno, line in enumerate(
            md_file.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if uvicorn_invocation.search(line) and "--no-access-log" not in line:
                rel = md_file.relative_to(PROJECT_ROOT).as_posix()
                offenders.append(f"{rel}:{lineno}: {line.strip()}")

    assert not offenders, (
        "docs uvicorn run commands must include --no-access-log:\n"
        + "\n".join(f"  - {o}" for o in offenders)
    )


def test_compose_services_define_resource_limits() -> None:
    """BUG-027: Self-hosted Docker Compose services define deploy.resources.limits
    (cpus and memory ceilings) on the shared anchor and services."""
    import yaml

    for filename in ("docker-compose.yml", "docker-compose.hosted.yml"):
        content = (PROJECT_ROOT / filename).read_text(encoding="utf-8")
        data = yaml.safe_load(content)

        anchor = data.get("x-lumina-service") or data.get("x-lumina")
        assert anchor is not None, f"missing lumina anchor in {filename}"
        anchor_limits = anchor.get("deploy", {}).get("resources", {}).get("limits", {})
        assert "cpus" in anchor_limits, f"missing cpus limit on anchor in {filename}"
        assert "memory" in anchor_limits, f"missing memory limit on anchor in {filename}"

        services = data.get("services", {})
        assert services, f"no services declared in {filename}"
        for name, conf in services.items():
            limits = conf.get("deploy", {}).get("resources", {}).get("limits", {})
            assert "cpus" in limits, f"service {name} in {filename} missing cpus limit"
            assert "memory" in limits, f"service {name} in {filename} missing memory limit"

    # Verify frontend in docker-compose.yml specifically
    compose_content = (PROJECT_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    compose_data = yaml.safe_load(compose_content)
    frontend = compose_data["services"]["frontend"]
    frontend_limits = frontend["deploy"]["resources"]["limits"]
    assert "cpus" in frontend_limits
    assert "memory" in frontend_limits

