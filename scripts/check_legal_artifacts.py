from __future__ import annotations

import json
import re
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REQUIRED_ARTIFACTS = (
    "LICENSE",
    "SECURITY.md",
    "THIRD_PARTY_NOTICES.md",
    "THIRD_PARTY_LICENSES/OFL-1.1.txt",
    "docs/legal/README.md",
    "docs/legal/release-checklist.md",
)


def normalized_distribution_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def python_runtime_dependencies() -> set[str]:
    dependencies: set[str] = set()
    for raw_line in (
        (PROJECT_ROOT / "requirements.in").read_text(encoding="utf-8").splitlines()
    ):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        name = re.split(r"[<>=!~\[]", line, maxsplit=1)[0]
        dependencies.add(normalized_distribution_name(name))
    return dependencies


def browser_runtime_dependencies() -> set[str]:
    package = json.loads(
        (PROJECT_ROOT / "frontend" / "package.json").read_text(encoding="utf-8")
    )
    return {normalized_distribution_name(name) for name in package["dependencies"]}


def main() -> int:
    missing_files = [
        path for path in REQUIRED_ARTIFACTS if not (PROJECT_ROOT / path).is_file()
    ]
    notices = (
        (PROJECT_ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8").lower()
    )
    dependencies = python_runtime_dependencies() | browser_runtime_dependencies()
    missing_notices = sorted(
        dependency
        for dependency in dependencies
        if f"`{dependency}`" not in notices
        and dependency not in {"react-dom", "psycopg-binary"}
    )
    dockerfile = (PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8")
    missing_from_image = [
        name
        for name in ("LICENSE", "SECURITY.md", "THIRD_PARTY_NOTICES.md")
        if name not in dockerfile
    ]

    problems = []
    if missing_files:
        problems.append(f"missing legal artifacts: {', '.join(missing_files)}")
    if missing_notices:
        problems.append(
            f"dependencies missing from notices: {', '.join(missing_notices)}"
        )
    if missing_from_image:
        problems.append(
            f"legal artifacts missing from Dockerfile: {', '.join(missing_from_image)}"
        )
    if problems:
        raise SystemExit("; ".join(problems))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
