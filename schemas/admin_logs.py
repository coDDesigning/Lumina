from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


LogLevel = Literal["INFO", "WARNING", "ERROR", "CRITICAL"]
LogSourceType = Literal["operational", "ai_telemetry", "client_report"]
SourceStatus = Literal["available", "delayed", "unconfigured", "unavailable"]
ClientRouteTemplate = Literal[
    "/",
    "/account",
    "/account/ai",
    "/account/api-keys",
    "/account/appearance",
    "/account/background",
    "/account/security",
    "/activity",
    "/admin",
    "/admin/logs",
    "/courses/{course_id}",
    "/courses/{course_id}/exam-mode",
    "/courses/{course_id}/exam-mode/plans/{plan_id}",
    "/courses/{course_id}/exam-mode/plans/{plan_id}/compare/{other_plan_id}",
    "/courses/{course_id}/exam-mode/plans/{plan_id}/topics/{topic_key}",
    "/courses/{course_id}/guides/{output_id}",
    "/courses/{course_id}/practice/{quiz_id}",
    "/courses/{course_id}/practice/{quiz_id}/attempts/{attempt_id}",
    "/courses/{course_id}/practice/{quiz_id}/sessions/{session_id}",
    "/courses/{course_id}/progress",
    "/courses/{course_id}/reverse-quiz",
    "/courses/{course_id}/settings",
    "/dashboard",
    "/forgot-password",
    "/login",
    "/register",
    "/reset-password",
    "/unmatched",
    "/verify-email",
    "/workspaces/{course_id}/{legacy_path}",
]
ClientErrorClass = Literal[
    "AbortError",
    "AggregateError",
    "APIError",
    "ChunkLoadError",
    "DOMException",
    "Error",
    "EvalError",
    "MalformedResponseError",
    "NetworkError",
    "NotAllowedError",
    "QuotaExceededError",
    "RangeError",
    "ReferenceError",
    "RuntimeError",
    "SecurityError",
    "SyntaxError",
    "TypeError",
    "URIError",
]


class LogQueryWindow(BaseModel):
    start: datetime
    end: datetime
    timezone: Literal["UTC"] = "UTC"
    maximum_days: int = 30


class LogSourceHealth(BaseModel):
    source: LogSourceType
    status: SourceStatus
    available_from: datetime | None = None
    available_to: datetime | None = None
    last_successful_fetch_at: datetime | None = None
    ingestion_delay_seconds: float | None = None
    malformed_records: int = 0
    dropped_records: int | None = None
    limited: bool = False
    detail: str | None = None
    supported_filters: list[str] = Field(default_factory=list)
    collected_levels: list[LogLevel] = Field(default_factory=list)


class AdminLogRecord(BaseModel):
    id: str
    source: LogSourceType
    timestamp: datetime
    level: LogLevel
    service: str
    environment: str
    logger: str
    event: str
    description: str
    error_code: str | None = None
    error_category: str | None = None
    exception_type: str | None = None
    exception_chain: list[str] = Field(default_factory=list)
    source_location: str | None = None
    error_signature: str | None = None
    http_method: str | None = None
    http_path: str | None = None
    http_status: int | None = None
    duration_ms: float | None = None
    request_id: str | None = None
    related_request_id: str | None = None
    operation_id: str | None = None
    parent_operation_id: str | None = None
    job_id: int | None = None
    job_type: str | None = None
    job_status: str | None = None
    attempt_number: int | None = None
    stage: str | None = None
    failed_stage: str | None = None
    user_id: int | None = None
    course_id: int | None = None
    document_id: str | None = None
    generation_type: str | None = None
    provider: str | None = None
    model: str | None = None
    success: bool | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    estimated_cost_usd: float | None = None
    pricing_version: str | None = None
    runbook: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class AdminLogList(BaseModel):
    records: list[AdminLogRecord]
    next_cursor: str | None = None
    query_window: LogQueryWindow
    last_updated_at: datetime
    source_health: list[LogSourceHealth]
    partial: bool = False
    limited: bool = False
    omitted_records: int = 0


class LogLevelCounts(BaseModel):
    events: int | None = None
    warnings: int | None = None
    errors: int | None = None
    distinct_failed_operations: int | None = None


class LogErrorGroup(BaseModel):
    signature: str
    service: str
    event: str
    error_code: str | None = None
    exception_type: str | None = None
    source_location: str | None = None
    first_occurrence: datetime
    last_occurrence: datetime
    event_count: int
    distinct_operations: int | None = None


class LogTimeBucket(BaseModel):
    start: datetime
    events: int
    warnings: int
    errors: int


class AdminLogSummary(BaseModel):
    counts: LogLevelCounts
    error_groups: list[LogErrorGroup]
    distribution: list[LogTimeBucket]
    query_window: LogQueryWindow
    last_updated_at: datetime
    source_health: list[LogSourceHealth]
    partial: bool = False
    limited: bool = False


class AdminLogEventDetail(BaseModel):
    record: AdminLogRecord
    related_filter: dict[str, str] = Field(default_factory=dict)


class AdminLogTrace(BaseModel):
    anchor_id: str | None = None
    operation_id: str | None = None
    records: list[AdminLogRecord]
    correlation_status: Literal["correlated", "none"]
    message: str | None = None
    source_health: list[LogSourceHealth]
    partial: bool = False


class ClientErrorReport(BaseModel):
    route_template: ClientRouteTemplate
    application_version: str = Field(
        min_length=1, max_length=80, pattern=r"^[A-Za-z0-9._-]+$"
    )
    error_class: ClientErrorClass
    api_request_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9._-]+$",
    )
    fingerprint: str = Field(min_length=16, max_length=64, pattern=r"^[a-f0-9]+$")


class ClientErrorAccepted(BaseModel):
    accepted: bool = True
