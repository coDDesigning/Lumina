from typing import Literal

from pydantic import BaseModel, Field


class AiModelInfo(BaseModel):
    id: str
    provider: str
    model: str
    display_name: str
    is_default: bool
    cost_hint: str = ""
    capabilities: list[str] = []
    description: str = ""
    is_local: bool = False
    supports_json: bool = True
    json_mode: bool = True
    context_window: int = 8192
    vision: bool = False


class ModelTestRequest(BaseModel):
    model_id: str | None = Field(default=None, max_length=200)


ModelTestErrorCode = Literal[
    "unreachable",
    "timeout",
    "model_not_found",
    "auth",
    "rate_limited",
    "bad_response",
    "unavailable",
]


class ModelTestResult(BaseModel):
    ok: bool
    model_id: str
    provider: str
    latency_ms: int | None = None
    error_code: ModelTestErrorCode | None = None
    message: str
    supports_vision: bool | None = None
    base_url: str | None = None
    base_url_fallback: bool | None = None
