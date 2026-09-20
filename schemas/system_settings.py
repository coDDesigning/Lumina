from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class SystemSettingRow(BaseModel):
    key: str
    section: str
    label: str
    help: str
    kind: Literal["boolean", "integer", "float", "enum", "text", "json", "list"]
    scope: Literal["overridable", "container_managed", "compose_managed"]
    risk: Literal["low", "medium", "high"]
    secret: bool
    choices: list[str] = Field(default_factory=list)
    minimum: float | None = None
    maximum: float | None = None
    requires_confirmation: bool = False
    editable: bool
    source: Literal["override", "environment", "default", "missing"]
    has_override: bool
    configured: bool
    value: str | None = None
    default: str | None = None


class RestartStatus(BaseModel):
    request_id: str
    state: Literal["queued", "draining", "restarting", "ready", "failed", "rolled_back"]
    target_revision: int
    requested_at: str
    updated_at: str
    drain_deadline: str | None = None
    detail: str | None = None
    actor_id: int | None = None
    changed_keys: list[str] = Field(default_factory=list)


class SystemSettingsInventory(BaseModel):
    sections: list[str]
    settings: list[SystemSettingRow]
    active_revision: int
    saved_revision: int
    pending_restart: bool
    pending_keys: list[str]
    override_count: int
    saved_at: str | None = None
    supervised_restart: bool
    self_hosted: bool = True
    restart: RestartStatus | None = None
    rolled_back_from: int | None = None


class SystemSettingsUpdate(BaseModel):
    expected_revision: int
    values: dict[str, str | None] = Field(default_factory=dict)
    reset: list[str] = Field(default_factory=list)


class SystemSettingsReset(BaseModel):
    expected_revision: int
    confirm: bool = False


class RestartRequestBody(BaseModel):
    expected_revision: int


class RestartAccepted(BaseModel):
    request_id: str
    target_revision: int
    state: str
    in_flight: dict[str, int] = Field(default_factory=dict)
