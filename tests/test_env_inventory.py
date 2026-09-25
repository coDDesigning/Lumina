import json
import re

from scripts.export_env_inventory import (
    ENV_EXAMPLE_PATH,
    ENV_INVENTORY_PATH,
    build_inventory,
)

KEY_PATTERN = re.compile(r"^(#\s*)?([A-Z][A-Z0-9_]*)=(.*)$")


def _committed() -> dict:
    return json.loads(ENV_INVENTORY_PATH.read_text(encoding="utf-8"))


def _declared_values() -> dict[str, tuple[str, bool]]:
    declared: dict[str, tuple[str, bool]] = {}
    for line in ENV_EXAMPLE_PATH.read_text(encoding="utf-8").splitlines():
        match = KEY_PATTERN.match(line)
        if not match:
            continue
        commented, key, value = match.groups()
        value = value.strip()
        if not commented and "  #" in value:
            value = value.split("  #", 1)[0].strip()
        if key in declared and commented:
            continue
        declared[key] = (value, bool(commented))
    return declared


def test_the_committed_inventory_matches_env_example() -> None:
    assert _committed() == build_inventory(), (
        "docs/env.json is out of date with .env.example. Regenerate it with "
        "`python scripts/export_env_inventory.py` and commit the result."
    )


def test_the_inventory_is_written_deterministically() -> None:
    assert build_inventory() == build_inventory()


def test_every_declared_variable_appears_once() -> None:
    inventory = _committed()
    keys = [variable["key"] for variable in inventory["variables"]]

    assert len(keys) == len(set(keys))
    assert set(keys) == set(_declared_values())
    assert inventory["variable_count"] == len(keys)


def test_defaults_are_copied_from_env_example_verbatim() -> None:
    declared = _declared_values()

    for variable in _committed()["variables"]:
        value, commented = declared[variable["key"]]
        assert variable["default"] == (value or None), (
            f"{variable['key']} does not match its .env.example declaration."
        )
        assert variable["commented"] is commented


def test_the_inventory_never_holds_anything_env_example_does_not() -> None:
    source = ENV_EXAMPLE_PATH.read_text(encoding="utf-8")

    for variable in _committed()["variables"]:
        default = variable["default"]
        if default is None:
            continue
        assert default in source, (
            f"{variable['key']} carries a value that is not in .env.example. "
            "docs/env.json is generated from the committed template and must "
            "never contain anything from a real .env."
        )


def test_every_variable_carries_a_section_and_description() -> None:
    inventory = _committed()
    sections = set(inventory["sections"])

    for variable in inventory["variables"]:
        assert variable["section"] in sections
        assert variable["description"].strip(), f"{variable['key']} has no description."


def test_registry_metadata_is_attached_to_every_variable() -> None:
    for variable in _committed()["variables"]:
        for field in ("kind", "scope", "risk", "secret", "editable"):
            assert field in variable, f"{variable['key']} is missing {field}."


def test_agents_documents_the_regeneration_rule() -> None:
    agents = (ENV_EXAMPLE_PATH.parent / "AGENTS.md").read_text(encoding="utf-8")

    assert "scripts/export_env_inventory.py" in agents
    assert "docs/env.json" in agents


SECTION_PATTERN = re.compile(r"^#\s*──")


def _is_prose(line: str) -> bool:
    return (
        line.startswith("#")
        and not SECTION_PATTERN.match(line)
        and KEY_PATTERN.match(line) is None
    )


def test_every_key_documents_itself_in_env_example() -> None:
    lines = ENV_EXAMPLE_PATH.read_text(encoding="utf-8").splitlines()
    undocumented = []

    for index, line in enumerate(lines):
        match = KEY_PATTERN.match(line)
        if not match:
            continue
        commented, key, value = match.groups()
        if not commented and "  #" in value:
            continue
        previous = lines[index - 1] if index else ""
        if not _is_prose(previous):
            undocumented.append(key)

    assert not undocumented, (
        "These keys have no comment of their own directly above them in "
        f".env.example: {', '.join(undocumented)}. Every key documents itself; "
        "a description is never inherited from a neighbouring key's block, "
        "because that silently attaches the wrong prose."
    )


def test_no_two_keys_share_a_description() -> None:
    descriptions: dict[str, list[str]] = {}
    for variable in _committed()["variables"]:
        descriptions.setdefault(variable["description"], []).append(variable["key"])

    shared = {text: keys for text, keys in descriptions.items() if len(keys) > 1}

    assert not shared, (
        "These keys share one description, which means a comment block leaked "
        f"across keys: {[keys for keys in shared.values()]}"
    )


def test_the_registry_help_matches_the_inventory_description() -> None:
    from backend.app.settings_registry import SETTINGS_BY_KEY

    for variable in _committed()["variables"]:
        definition = SETTINGS_BY_KEY.get(variable["key"])
        if definition is None:
            continue
        assert definition.help == variable["description"], (
            f"{variable['key']} reads differently in the registry and in "
            "docs/env.json. Both are generated from .env.example, so one of them "
            "was not regenerated."
        )
