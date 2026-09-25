from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Mapping, Sequence

from backend.app import settings_overrides
from backend.app.settings_registry import (
    SECTION_ORDER,
    SETTINGS,
    SETTINGS_BY_KEY,
    SettingDefinition,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
VALIDATION_TIMEOUT_SECONDS = 90

SOURCE_OVERRIDE = "override"
SOURCE_ENVIRONMENT = "environment"
SOURCE_DEFAULT = "default"
SOURCE_MISSING = "missing"

_VALIDATION_SCRIPT = "from backend.app.config import load_settings; load_settings()"
_ERROR_PATTERN = re.compile(r"(?:ValueError|TypeError):\s*(?P<message>.+)", re.DOTALL)
_KEY_PATTERN = re.compile(r"\b([A-Z][A-Z0-9_]{2,})\b")


@dataclass(frozen=True, slots=True)
class FieldError:
    key: str | None
    message: str


class CandidateRejected(Exception):
    def __init__(self, errors: Sequence[FieldError]) -> None:
        super().__init__("The configuration candidate was rejected.")
        self.errors = tuple(errors)


@dataclass(frozen=True, slots=True)
class SettingRow:
    definition: SettingDefinition
    source: str
    has_override: bool
    value: str | None
    configured: bool
    editable: bool = True

    def as_payload(self) -> dict[str, object]:
        definition = self.definition
        payload: dict[str, object] = {
            "key": definition.key,
            "section": definition.section,
            "label": definition.label,
            "help": definition.help,
            "kind": definition.kind,
            "scope": definition.scope,
            "risk": definition.risk,
            "secret": definition.secret,
            "choices": list(definition.choices),
            "minimum": definition.minimum,
            "maximum": definition.maximum,
            "requires_confirmation": definition.requires_confirmation,
            "editable": definition.is_overridable and self.editable,
            "source": self.source,
            "has_override": self.has_override,
            "configured": self.configured,
        }
        if definition.secret:
            payload["value"] = None
            payload["default"] = None
        else:
            payload["value"] = self.value
            payload["default"] = definition.example
        return payload


def _baseline() -> Mapping[str, str]:
    return settings_overrides.baseline_environment()


def _resolve(
    definition: SettingDefinition,
    overrides: Mapping[str, str],
    baseline: Mapping[str, str],
) -> SettingRow:
    key = definition.key
    has_override = key in overrides
    if has_override:
        return SettingRow(
            definition=definition,
            source=SOURCE_OVERRIDE,
            has_override=True,
            value=overrides[key],
            configured=bool(overrides[key]),
        )
    if key in baseline and baseline[key] != "":
        return SettingRow(
            definition=definition,
            source=SOURCE_ENVIRONMENT,
            has_override=False,
            value=baseline[key],
            configured=True,
        )
    if definition.example is not None:
        return SettingRow(
            definition=definition,
            source=SOURCE_DEFAULT,
            has_override=False,
            value=definition.example,
            configured=True,
        )
    return SettingRow(
        definition=definition,
        source=SOURCE_MISSING,
        has_override=False,
        value=None,
        configured=False,
    )


def build_rows() -> tuple[SettingRow, ...]:
    overrides = settings_overrides.load_overrides().values
    baseline = _baseline()
    editable = _self_hosted()
    return tuple(
        replace(_resolve(definition, overrides, baseline), editable=editable)
        for definition in SETTINGS
    )


def build_inventory() -> dict[str, object]:
    saved = settings_overrides.load_overrides()
    rows = build_rows()
    active = settings_overrides.active_revision()
    request = settings_overrides.read_restart_request()
    return {
        "sections": list(SECTION_ORDER),
        "settings": [row.as_payload() for row in rows],
        "active_revision": active,
        "saved_revision": saved.revision,
        "pending_restart": saved.revision != active,
        "pending_keys": sorted(_pending_keys(saved.values)),
        "override_count": len(saved.values),
        "saved_at": saved.updated_at,
        "supervised_restart": _supervised(),
        "self_hosted": _self_hosted(),
        "restart": request.as_payload() if request is not None else None,
        "rolled_back_from": settings_overrides.rolled_back_from(),
    }


def _supervised() -> bool:
    from backend.app.config import settings

    return settings.supervised_restart


def _self_hosted() -> bool:
    from backend.app.config import settings

    return settings.is_self_hosted


def _pending_keys(saved_values: Mapping[str, str]) -> set[str]:
    applied = dict(_applied_override_values())
    pending = set()
    for key, value in saved_values.items():
        if applied.get(key) != value:
            pending.add(key)
    for key in applied:
        if key not in saved_values:
            pending.add(key)
    return pending


def _applied_override_values() -> Mapping[str, str]:
    applied_keys = settings_overrides.applied_keys()
    return {key: os.environ[key] for key in applied_keys if key in os.environ}


def candidate_environment(
    updates: Mapping[str, str], removals: Sequence[str] = ()
) -> dict[str, str]:
    environment = dict(os.environ)
    baseline = _baseline()
    saved = settings_overrides.load_overrides().values

    for key in SETTINGS_BY_KEY:
        if key in baseline:
            environment[key] = baseline[key]
        else:
            environment.pop(key, None)

    for key, value in saved.items():
        environment[key] = value
    for key in removals:
        if key in baseline:
            environment[key] = baseline[key]
        else:
            environment.pop(key, None)
    for key, value in updates.items():
        environment[key] = value

    environment[settings_overrides.APPLY_VARIABLE] = "0"
    return environment


def _attribute(message: str, touched: Sequence[str]) -> list[FieldError]:
    named = [key for key in _KEY_PATTERN.findall(message) if key in SETTINGS_BY_KEY]
    relevant = [key for key in named if key in touched] or named
    if not relevant:
        return [FieldError(key=None, message=message)]
    return [FieldError(key=key, message=message) for key in dict.fromkeys(relevant)]


def validate_candidate(
    updates: Mapping[str, str], removals: Sequence[str] = ()
) -> None:
    environment = candidate_environment(updates, removals)
    touched = list(updates) + list(removals)
    try:
        completed = subprocess.run(
            [sys.executable, "-c", _VALIDATION_SCRIPT],
            cwd=str(PROJECT_ROOT),
            env=environment,
            capture_output=True,
            text=True,
            timeout=VALIDATION_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise CandidateRejected(
            [
                FieldError(
                    key=None,
                    message="Validating the new configuration timed out.",
                )
            ]
        ) from exc
    except OSError as exc:
        raise CandidateRejected(
            [FieldError(key=None, message=f"Could not validate: {exc}")]
        ) from exc

    if completed.returncode == 0:
        return

    stderr = (completed.stderr or "").strip()
    match = None
    for candidate in _ERROR_PATTERN.finditer(stderr):
        match = candidate
    message = match.group("message").strip() if match else stderr.splitlines()[-1:]
    if isinstance(message, list):
        message = message[0] if message else "The new configuration is not valid."
    message = " ".join(message.split())
    raise CandidateRejected(_attribute(message, touched))


def normalize_updates(
    updates: Mapping[str, str | None],
) -> tuple[dict[str, str], tuple[str, ...]]:
    values: dict[str, str] = {}
    unchanged_secrets: list[str] = []
    for key, raw in updates.items():
        definition = SETTINGS_BY_KEY.get(key)
        if definition is None:
            values[key] = "" if raw is None else str(raw)
            continue
        if raw is None:
            unchanged_secrets.append(key)
            continue
        text = str(raw)
        if definition.secret and text == "":
            unchanged_secrets.append(key)
            continue
        values[key] = text
    return values, tuple(unchanged_secrets)
