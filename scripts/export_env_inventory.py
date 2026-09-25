"""Regenerate everything derived from .env.example.

.env.example is the source. Two committed artifacts are rendered from it:

- docs/env.json, the machine-readable inventory.
- The SECTION_ORDER and _RAW tables inside backend/app/settings_registry.py,
  which the administrator interface reads. The policy sets in that module
  (scope, secret, risk, confirmation) are hand-maintained and left untouched.

Run this after every .env.example change. Each key must carry its own comment
directly above it; nothing is inherited from a neighbouring key's block,
because that silently attaches the wrong prose to a setting.
"""

import ast
import json
import re
import sys
import textwrap
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_EXAMPLE_PATH = PROJECT_ROOT / ".env.example"
ENV_INVENTORY_PATH = PROJECT_ROOT / "docs" / "env.json"
REGISTRY_PATH = PROJECT_ROOT / "backend" / "app" / "settings_registry.py"

SECTION_PATTERN = re.compile(r"^#\s*──\s*(.+?)\s*─+\s*$")
KEY_PATTERN = re.compile(r"^(#\s*)?([A-Z][A-Z0-9_]*)=(.*)$")

HELPER_KINDS = {
    "_boolean_setting": "boolean",
    "_positive_integer_setting": "integer",
    "_nonnegative_integer_setting": "integer",
    "_bounded_positive_integer_setting": "integer",
    "_positive_float_setting": "float",
    "_nonnegative_float_setting": "float",
    "_bounded_float_setting": "float",
    "_http_url_setting": "text",
}

BOUND_OVERRIDES: dict[str, tuple[Any, Any]] = {
    "SYSTEM_RESTART_DRAIN_TIMEOUT_SECONDS": (0, 3600),
}

LABEL_OVERRIDES = {
    "APP_ENV": "Application environment",
    "APP_DEBUG": "Debug mode",
    "APP_PUBLIC_BASE_URL": "Public base URL",
    "CORS_ALLOWED_ORIGINS": "Allowed browser origins",
    "LUMINA_TMPFS_SIZE_BYTES": "Container /tmp size",
    "LUMINA_CPU_LIMIT": "CPU limit",
    "LUMINA_MEMORY_LIMIT": "Memory limit",
    "LUMINA_PORT": "Published port",
    "LUMINA_BIND_ADDRESS": "Bind address",
    "LUMINA_IMAGE": "Container image",
    "LUMINA_WEB_ROOT": "Web root",
    "LUMINA_SUPERVISED_RESTART": "Supervised restart",
    "COMPOSE_PROJECT_NAME": "Compose project name",
    "DATABASE_URL": "Database URL",
    "OPERATIONAL_LOG_CLOUDWATCH_GROUP": "CloudWatch log group",
    "OPERATIONAL_LOG_CLOUDWATCH_REGION": "CloudWatch region",
    "AI_DEFAULT_MODEL": "Default AI model",
    "AI_MODEL_CATALOG": "AI model catalog",
    "AI_MODEL_COST_RATES": "AI model cost rates",
    "OCR_DPI": "OCR resolution (DPI)",
    "SECURITY_HSTS_ENABLED": "HSTS enabled",
    "SECURITY_HSTS_MAX_AGE_SECONDS": "HSTS maximum age",
    "SMTP_USE_TLS": "SMTP uses TLS",
    "ENABLE_HOSTED_ADS": "Hosted advertising enabled",
    "SYSTEM_SETTINGS_DIRECTORY": "Override store directory",
    "SYSTEM_RESTART_DRAIN_TIMEOUT_SECONDS": "Restart drain timeout",
    "VECTOR_BACKEND": "Vector backend",
    "WORKER_SHUTDOWN_MODE": "Worker shutdown mode",
    "WORKER_STOP_GRACE_PERIOD": "Worker stop grace period",
}

_LABEL_WORDS = {
    "AI": "AI",
    "URL": "URL",
    "ID": "ID",
    "TTL": "TTL",
    "S3": "S3",
    "SMTP": "SMTP",
    "JWT": "JWT",
    "PDF": "PDF",
    "OCR": "OCR",
    "CORS": "CORS",
    "HSTS": "HSTS",
    "QA": "Q&A",
    "CPU": "CPU",
    "DPI": "DPI",
    "IPS": "IPs",
}


def _normalize(text: str) -> str:
    return " ".join(text.split())


def parse_env_example(source: str) -> list[dict[str, Any]]:
    """Read every declaration, taking each key's own comment as its description."""
    entries: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    section = "Application"
    comment: list[str] = []
    key_since_block = False

    for raw in source.splitlines():
        line = raw.rstrip()

        section_match = SECTION_PATTERN.match(line)
        if section_match:
            section = section_match.group(1).strip()
            comment = []
            key_since_block = False
            continue

        key_match = KEY_PATTERN.match(line)
        if key_match:
            commented, key, value = key_match.groups()
            value = value.strip()
            inline = ""
            if not commented and "  #" in value:
                value, inline = value.split("  #", 1)
                value, inline = value.strip(), inline.strip()
            description = _normalize(
                inline[0].upper() + inline[1:] if inline else " ".join(comment)
            )
            existing = entries.get(key)
            if existing is not None:
                if existing["commented"] and not commented:
                    existing["default"] = value or None
                    existing["commented"] = False
                    if description:
                        existing["description"] = description
                key_since_block = True
                continue
            entries[key] = {
                "key": key,
                "section": section,
                "default": value or None,
                "commented": bool(commented),
                "description": description,
            }
            order.append(key)
            key_since_block = True
            continue

        if line.startswith("#"):
            body = line.lstrip("#").strip()
            if body:
                if key_since_block:
                    comment = []
                    key_since_block = False
                comment.append(body)
        elif not line:
            comment = []
            key_since_block = False

    return [entries[key] for key in order]


def section_order(source: str) -> list[str]:
    sections: list[str] = []
    for line in source.splitlines():
        match = SECTION_PATTERN.match(line)
        if match:
            name = match.group(1).strip()
            if name not in sections:
                sections.append(name)
    return sections


def parse_config() -> tuple[dict[str, tuple[Any, Any]], dict[str, str]]:
    """Read numeric bounds and value kinds from the validators in config.py."""
    config = (PROJECT_ROOT / "backend" / "app" / "config.py").read_text("utf-8")
    tree = ast.parse(config)
    bounds: dict[str, tuple[Any, Any]] = {}
    kinds: dict[str, str] = {}

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        if not node.args:
            continue
        first = node.args[0]
        if not (isinstance(first, ast.Constant) and isinstance(first.value, str)):
            continue
        key = first.value
        if node.func.id in HELPER_KINDS:
            kinds[key] = HELPER_KINDS[node.func.id]
        if "bounded" not in node.func.id:
            continue
        low = high = None
        for keyword in node.keywords:
            value = keyword.value
            if isinstance(value, ast.Constant) and isinstance(
                value.value, (int, float)
            ):
                if keyword.arg == "minimum":
                    low = value.value
                elif keyword.arg == "maximum":
                    high = value.value
        if low is not None or high is not None:
            bounds[key] = (low, high)
    return bounds, kinds


def value_kind(key: str, default: str | None, kinds: dict[str, str]) -> str:
    known = kinds.get(key)
    if known is not None:
        return known
    value = (default or "").strip()
    if value.lower() in {"true", "false"}:
        return "boolean"
    if re.fullmatch(r"-?\d+\.\d+", value):
        return "float"
    if re.fullmatch(r"-?\d+", value):
        return "integer"
    return "text"


def label_for(key: str) -> str:
    override = LABEL_OVERRIDES.get(key)
    if override is not None:
        return override
    words = []
    for index, word in enumerate(key.split("_")):
        if word in _LABEL_WORDS:
            words.append(_LABEL_WORDS[word])
        elif index == 0:
            words.append(word.capitalize())
        else:
            words.append(word.lower())
    text = " ".join(words)
    return text[0].upper() + text[1:]


def _require_descriptions(entries: list[dict[str, Any]]) -> None:
    missing = [entry["key"] for entry in entries if not entry["description"]]
    if missing:
        raise ValueError(
            "These keys have no comment of their own in .env.example: "
            f"{', '.join(missing)}. Every key documents itself; nothing is "
            "inherited from a neighbouring key's block."
        )


def build_inventory() -> dict[str, Any]:
    """Render .env.example plus its registry metadata as one JSON document."""
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    from backend.app.settings_registry import SETTINGS_BY_KEY

    source = ENV_EXAMPLE_PATH.read_text(encoding="utf-8")
    entries = parse_env_example(source)
    _require_descriptions(entries)

    variables = []
    for entry in entries:
        definition = SETTINGS_BY_KEY.get(entry["key"])
        variable: dict[str, Any] = dict(entry)
        if definition is not None:
            variable.update(
                {
                    "label": definition.label,
                    "kind": definition.kind,
                    "scope": definition.scope,
                    "risk": definition.risk,
                    "secret": definition.secret,
                    "editable": definition.is_overridable,
                    "choices": list(definition.choices),
                    "minimum": definition.minimum,
                    "maximum": definition.maximum,
                    "requires_confirmation": definition.requires_confirmation,
                }
            )
        variables.append(variable)

    return {
        "source": ".env.example",
        "generated_by": "python scripts/export_env_inventory.py",
        "variable_count": len(variables),
        "sections": section_order(source),
        "variables": variables,
    }


def _wrap_literal(text: str, indent: int, width: int = 88) -> list[str]:
    pad = " " * indent
    pieces = textwrap.wrap(
        text,
        width=width - indent - 4,
        break_on_hyphens=False,
        break_long_words=False,
    ) or [""]
    if len(pieces) == 1:
        return [f"{pad}{pieces[0]!r},"]
    rendered = [
        f"{pad}{(piece if index == len(pieces) - 1 else piece + ' ')!r}"
        for index, piece in enumerate(pieces)
    ]
    rendered[-1] += ","
    return rendered


def build_registry_tables() -> str:
    """Render the SECTION_ORDER and _RAW source blocks for the registry module."""
    source = ENV_EXAMPLE_PATH.read_text(encoding="utf-8")
    entries = parse_env_example(source)
    _require_descriptions(entries)
    bounds, kinds = parse_config()

    lines = ["SECTION_ORDER: tuple[str, ...] = ("]
    for name in section_order(source):
        lines.append(f"    {name!r},")
    lines.append(")")
    lines.append("")
    lines.append("_RAW: tuple[_Row, ...] = (")
    for entry in entries:
        key = entry["key"]
        low, high = BOUND_OVERRIDES.get(key) or bounds.get(key, (None, None))
        lines.append("    (")
        lines.append(f"        {key!r},")
        lines.append(f"        {entry['section']!r},")
        lines.append(f"        {label_for(key)!r},")
        lines.extend(_wrap_literal(entry["description"], 8))
        lines.append(f"        {value_kind(key, entry['default'], kinds)!r},")
        lines.append(f"        {entry['default']!r},")
        lines.append(f"        {low!r},")
        lines.append(f"        {high!r},")
        lines.append("    ),")
    lines.append(")")
    return "\n".join(lines)


def _replace_block(lines: list[str], opener: str, replacement: str) -> list[str]:
    start = next(
        (index for index, line in enumerate(lines) if line.startswith(opener)), None
    )
    if start is None:
        raise ValueError(f"{REGISTRY_PATH.name} no longer declares {opener}")
    if lines[start].rstrip().endswith("()"):
        end = start
    else:
        end = next(
            index for index in range(start + 1, len(lines)) if lines[index] == ")"
        )
    return lines[:start] + replacement.split("\n") + lines[end + 1 :]


def export_env_inventory(target_path: Path = ENV_INVENTORY_PATH) -> str:
    """Write the deterministic inventory JSON and return its content."""
    content = json.dumps(build_inventory(), indent=2, ensure_ascii=False) + "\n"
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(content, encoding="utf-8", newline="\n")
    return content


def export_settings_registry(target_path: Path = REGISTRY_PATH) -> str:
    """Splice the generated tables into the registry, keeping its policy sets."""
    tables = build_registry_tables()
    section_block, raw_block = tables.split("\n\n", 1)
    lines = target_path.read_text(encoding="utf-8").split("\n")
    lines = _replace_block(lines, "SECTION_ORDER: tuple[str, ...] =", section_block)
    lines = _replace_block(lines, "_RAW: tuple[_Row, ...] =", raw_block)
    content = "\n".join(lines)
    target_path.write_text(content, encoding="utf-8", newline="\n")
    return content


if __name__ == "__main__":
    export_settings_registry()
    export_env_inventory()
    print(
        "Regenerated "
        f"{REGISTRY_PATH.relative_to(PROJECT_ROOT)} and "
        f"{ENV_INVENTORY_PATH.relative_to(PROJECT_ROOT)}"
    )
