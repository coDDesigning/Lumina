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
