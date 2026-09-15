from datetime import date

from pydantic import BaseModel, ConfigDict


class LegalConfigResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool
    terms_version: str | None = None
    privacy_version: str | None = None
    effective_date: date | None = None
