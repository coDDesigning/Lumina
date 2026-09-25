import re
from pathlib import Path

import pytest

from backend.app import settings_registry
from backend.app.settings_registry import (
    SCOPE_COMPOSE_MANAGED,
    SCOPE_CONTAINER_MANAGED,
    SCOPE_OVERRIDABLE,
    SETTINGS,
    SETTINGS_BY_KEY,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_EXAMPLE = PROJECT_ROOT / ".env.example"
COMPOSE_FILE = PROJECT_ROOT / "docker-compose.yml"

KEY_PATTERN = re.compile(r"^(?:#\s*)?([A-Z][A-Z0-9_]*)=")
SECTION_PATTERN = re.compile(r"^#\s*──\s*(.+?)\s*─+\s*$")


def _env_example_keys() -> set[str]:
    keys = set()
    for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        match = KEY_PATTERN.match(line)
        if match:
            keys.add(match.group(1))
    return keys


def _env_example_sections() -> list[str]:
    sections = []
    for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        match = SECTION_PATTERN.match(line)
        if match:
            sections.append(match.group(1).strip())
    return sections


def test_every_env_example_key_has_registry_metadata() -> None:
    missing = sorted(_env_example_keys() - set(SETTINGS_BY_KEY))

    assert not missing, (
        "These keys are declared in .env.example but carry no registry metadata, "
        "so the administrator interface cannot render them: "
        f"{', '.join(missing)}. Add them to backend/app/settings_registry.py."
    )


def test_every_registry_key_is_declared_in_env_example() -> None:
    stale = sorted(set(SETTINGS_BY_KEY) - _env_example_keys())

    assert not stale, (
        "These keys carry registry metadata but no longer exist in .env.example: "
        f"{', '.join(stale)}. Remove them from backend/app/settings_registry.py."
    )


def test_registry_preserves_env_example_ordering() -> None:
    declared_order = []
    seen = set()
    for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        match = KEY_PATTERN.match(line)
        if match and match.group(1) not in seen:
            seen.add(match.group(1))
            declared_order.append(match.group(1))

    assert [setting.key for setting in SETTINGS] == declared_order


def test_registry_sections_match_env_example_sections() -> None:
    assert list(settings_registry.SECTION_ORDER) == _env_example_sections()


def test_every_setting_belongs_to_a_known_section() -> None:
    known = set(settings_registry.SECTION_ORDER)

    for setting in SETTINGS:
        assert setting.section in known, f"{setting.key} names an unknown section."


def test_every_setting_carries_a_label_and_help() -> None:
    for setting in SETTINGS:
        assert setting.label.strip(), f"{setting.key} has no label."
        assert len(setting.help.strip()) >= 20, (
            f"{setting.key} has no usable help text."
        )


def test_scopes_are_exhaustive_and_disjoint() -> None:
    scopes = {
        SCOPE_OVERRIDABLE,
        SCOPE_CONTAINER_MANAGED,
        SCOPE_COMPOSE_MANAGED,
    }

    for setting in SETTINGS:
        assert setting.scope in scopes

    overlap = (
        settings_registry.CONTAINER_MANAGED_KEYS
        & settings_registry.COMPOSE_MANAGED_KEYS
    )
    assert not overlap, (
        f"A key cannot be both container and compose managed: {sorted(overlap)}"
    )


def test_container_managed_keys_are_pinned_by_compose() -> None:
    compose = COMPOSE_FILE.read_text(encoding="utf-8")
    environment_block = compose.split("environment:", 1)[1].split("user:", 1)[0]
    pinned = {
        line.split(":", 1)[0].strip()
        for line in environment_block.splitlines()
        if ":" in line and not line.strip().startswith("#")
    }

    image_declared = {"LUMINA_WEB_ROOT", "LUMINA_SUPERVISED_RESTART"}

    for key in settings_registry.CONTAINER_MANAGED_KEYS - image_declared:
        assert key in pinned, (
            f"{key} is registered as container managed but docker-compose.yml no "
            "longer pins it under environment:."
        )


def test_compose_managed_keys_are_interpolated_by_compose() -> None:
    compose = COMPOSE_FILE.read_text(encoding="utf-8")
    interpolated = set(re.findall(r"\$\{([A-Z][A-Z0-9_]*)", compose))
    hosted_only = {
        "POSTGRES_CPU_LIMIT",
        "POSTGRES_MEMORY_LIMIT",
        "MINIO_CPU_LIMIT",
        "MINIO_MEMORY_LIMIT",
    }
    implicit = {"COMPOSE_PROJECT_NAME"}

    unexplained = (
        settings_registry.COMPOSE_MANAGED_KEYS - interpolated - hosted_only - implicit
    )

    assert not unexplained, (
        "These keys are registered as compose managed but nothing in "
        f"docker-compose.yml interpolates them: {sorted(unexplained)}"
    )


def test_no_secret_exposes_an_example_value() -> None:
    for setting in SETTINGS:
        if setting.secret:
            assert setting.example is None, (
                f"{setting.key} is a secret and must not carry an example value."
            )


def test_secrets_are_high_risk() -> None:
    for setting in SETTINGS:
        if setting.secret:
            assert setting.risk == "high", (
                f"{setting.key} is a secret but not high risk."
            )


def test_enum_settings_declare_choices_and_a_valid_example() -> None:
    for setting in SETTINGS:
        if setting.kind != "enum":
            assert not setting.choices, (
                f"{setting.key} declares choices but is not an enum."
            )
            continue
        assert setting.choices, f"{setting.key} is an enum with no choices."
        if setting.example is not None:
            assert setting.example in setting.choices, (
                f"{setting.key} advertises an example outside its own choices."
            )


def test_numeric_bounds_are_ordered_and_contain_the_example() -> None:
    for setting in SETTINGS:
        if setting.minimum is not None and setting.maximum is not None:
            assert setting.minimum <= setting.maximum, (
                f"{setting.key} declares an inverted range."
            )
        if setting.example is None or setting.kind not in {"integer", "float"}:
            continue
        value = float(setting.example)
        if setting.minimum is not None:
            assert value >= setting.minimum, (
                f"{setting.key} advertises an example below its own minimum."
            )
        if setting.maximum is not None:
            assert value <= setting.maximum, (
                f"{setting.key} advertises an example above its own maximum."
            )


def test_boolean_examples_parse_as_booleans() -> None:
    for setting in SETTINGS:
        if setting.kind == "boolean" and setting.example is not None:
            assert setting.example.lower() in {"true", "false"}, (
                f"{setting.key} is a boolean whose example does not parse."
            )


@pytest.mark.parametrize(
    "key",
    [
        "DATABASE_URL",
        "UPLOAD_DIRECTORY",
        "CHROMA_PERSIST_DIRECTORY",
        "DEPLOYMENT_MODE",
        "STORAGE_BACKEND",
        "SYSTEM_SETTINGS_DIRECTORY",
    ],
)
def test_boot_critical_keys_are_never_overridable(key: str) -> None:
    setting = SETTINGS_BY_KEY[key]

    assert setting.scope == SCOPE_CONTAINER_MANAGED
    assert not setting.is_overridable
    assert not settings_registry.is_overridable(key)


@pytest.mark.parametrize("key", ["LUMINA_PORT", "LUMINA_IMAGE", "COMPOSE_PROJECT_NAME"])
def test_compose_level_keys_are_never_overridable(key: str) -> None:
    setting = SETTINGS_BY_KEY[key]

    assert setting.scope == SCOPE_COMPOSE_MANAGED
    assert not setting.is_overridable


def test_overridable_keys_exclude_every_managed_key() -> None:
    managed = (
        settings_registry.CONTAINER_MANAGED_KEYS
        | settings_registry.COMPOSE_MANAGED_KEYS
    )

    assert not (settings_registry.OVERRIDABLE_KEYS & managed)


def test_get_returns_none_for_an_unsupported_key() -> None:
    assert settings_registry.get("TOTALLY_MADE_UP_KEY") is None
    assert not settings_registry.is_overridable("TOTALLY_MADE_UP_KEY")


def test_registry_keys_are_unique() -> None:
    keys = [setting.key for setting in SETTINGS]

    assert len(keys) == len(set(keys))
