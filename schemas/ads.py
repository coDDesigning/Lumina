"""Pydantic schemas for optional hosted advertising configuration and telemetry."""

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

AdPlacement = Literal["sidebar", "footer", "dashboard", "landing"]
AdStatus = Literal["rendered", "blocked", "no_fill", "error"]


class AdConfigResponse(BaseModel):
    """Public discovery payload for client ad initialization."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool
    provider: str | None = None
    publisher_id: str | None = None


class AdTelemetryRequest(BaseModel):
    """Privacy-safe aggregate impression and status telemetry request."""

    model_config = ConfigDict(extra="forbid")

    placement: AdPlacement = Field(..., description="Abstract UI slot identifier.")
    provider: str = Field(..., max_length=64, description="Ad provider network name.")
    status: AdStatus = Field(..., description="Client render or failure status.")


class AdTelemetryResponse(BaseModel):
    """Confirmation of recorded telemetry."""

    model_config = ConfigDict(extra="forbid")

    recorded: bool = True
