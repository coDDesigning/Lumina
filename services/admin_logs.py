"""One capability-aware read boundary for operational and AI telemetry logs."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import sqlite3
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field, fields as dataclass_fields, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

from sqlalchemy import String, and_, cast, func, literal, or_, select
from sqlalchemy.orm import Session

from backend.app.config import Settings, settings
from backend.app.models import (
    AiUsageLog,
    GenerationJob,
    ProcessingJob,
    ProfileProcessingJob,
)
from backend.app.operational_events import sanitize_operational_payload
from schemas.admin_logs import (
    AdminLogEventDetail,
    AdminLogList,
    AdminLogRecord,
    AdminLogSummary,
    AdminLogTrace,
    LogErrorGroup,
    LogLevelCounts,
    LogQueryWindow,
    LogSourceHealth,
    LogTimeBucket,
)

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200
MAX_QUERY_DAYS = 30
MAX_EXPORT_RECORDS = 10_000
MAX_SOURCE_SCAN_RECORDS = 10_001
_LEVELS = ("INFO", "WARNING", "ERROR", "CRITICAL")

_COMMON_FILTERS = frozenset(
    {
        "levels",
        "sources",
        "services",
        "environments",
        "loggers",
        "events",
        "error_codes",
        "error_categories",
        "error_signatures",
        "exception_types",
        "operation_id",
        "request_id",
        "job_id",
        "job_types",
        "job_statuses",
        "attempt_numbers",
        "user_id",
        "course_id",
        "generation_types",
        "providers",
        "models",
        "success",
        "minimum_duration_ms",
        "maximum_duration_ms",
        "search",
    }
)
OPERATIONAL_FILTERS = _COMMON_FILTERS | frozenset(
    {
        "http_methods",
        "http_statuses",
        "http_status_classes",
        "failed_stages",
        "document_id",
    }
)
AI_FILTERS = _COMMON_FILTERS - {"exception_types"}


class InvalidLogQueryError(ValueError):
    code = "invalid_log_query"


class InvalidLogCursorError(ValueError):
    code = "invalid_log_cursor"


class UnsupportedLogFilterError(ValueError):
    code = "unsupported_log_filter"


class LogRecordNotFoundError(LookupError):
    code = "log_event_not_found"


class LogSourcesUnavailableError(RuntimeError):
    code = "log_sources_unavailable"


@dataclass(frozen=True, slots=True)
class LogFilters:
    start: datetime
    end: datetime
    levels: tuple[str, ...] = ()
    sources: tuple[str, ...] = ()
    services: tuple[str, ...] = ()
    environments: tuple[str, ...] = ()
    loggers: tuple[str, ...] = ()
    events: tuple[str, ...] = ()
    http_methods: tuple[str, ...] = ()
    http_statuses: tuple[int, ...] = ()
    http_status_classes: tuple[int, ...] = ()
    minimum_duration_ms: float | None = None
    maximum_duration_ms: float | None = None
    error_codes: tuple[str, ...] = ()
    error_categories: tuple[str, ...] = ()
    error_signatures: tuple[str, ...] = ()
    exception_types: tuple[str, ...] = ()
    failed_stages: tuple[str, ...] = ()
    job_types: tuple[str, ...] = ()
    job_statuses: tuple[str, ...] = ()
    attempt_numbers: tuple[int, ...] = ()
    request_id: str | None = None
    operation_id: str | None = None
    operation_ids: tuple[str, ...] = ()
    job_id: int | None = None
    user_id: int | None = None
    course_id: int | None = None
    document_id: str | None = None
    generation_types: tuple[str, ...] = ()
    providers: tuple[str, ...] = ()
    models: tuple[str, ...] = ()
    success: bool | None = None
    search: str | None = None
    before_timestamp: datetime | None = None
    before_id: str | None = None

    def active_names(self) -> set[str]:
        inactive = {
            "start",
            "end",
            "operation_ids",
            "before_timestamp",
            "before_id",
        }
        return {
            item.name
            for item in dataclass_fields(self)
            if (value := getattr(self, item.name)) is not None
            for name in (item.name,)
            if name not in inactive and value not in (None, (), "")
        }

    def fingerprint(self) -> str:
        serializable = {
            name: value.isoformat() if isinstance(value, datetime) else value
            for item in dataclass_fields(self)
            if item.name not in {"operation_ids", "before_timestamp", "before_id"}
            for name, value in ((item.name, getattr(self, item.name)),)
        }
        return hashlib.sha256(
            json.dumps(serializable, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()


@dataclass(slots=True)
class SourcePage:
    records: list[AdminLogRecord] = field(default_factory=list)
    health: list[LogSourceHealth] = field(default_factory=list)
    limited: bool = False


def normalize_window(
    start: datetime | None,
    end: datetime | None,
    *,
    now: datetime | None = None,
) -> tuple[datetime, datetime]:
    if start is not None and (start.tzinfo is None or start.utcoffset() is None):
        raise InvalidLogQueryError("The start time must include a UTC offset.")
    if end is not None and (end.tzinfo is None or end.utcoffset() is None):
        raise InvalidLogQueryError("The end time must include a UTC offset.")
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    normalized_end = (end or current).astimezone(timezone.utc)
    normalized_start = (start or normalized_end - timedelta(hours=1)).astimezone(
        timezone.utc
    )
    if normalized_start >= normalized_end:
        raise InvalidLogQueryError("The start time must be before the end time.")
    if normalized_end - normalized_start > timedelta(days=MAX_QUERY_DAYS):
        raise InvalidLogQueryError("The query window cannot exceed 30 days.")
    return normalized_start, normalized_end


def _record_from_payload(payload: dict[str, Any]) -> AdminLogRecord | None:
    try:
        return AdminLogRecord.model_validate(payload)
    except Exception:
        return None


def _source_health(
    source: str,
    *,
    status: str = "available",
    available_from: datetime | None = None,
    available_to: datetime | None = None,
    malformed: int = 0,
    dropped: int | None = None,
    limited: bool = False,
    detail: str | None = None,
    filters: Iterable[str],
    collected_levels: Sequence[str] = _LEVELS,
    ingestion_delay_seconds: float | None = None,
) -> LogSourceHealth:
    now = datetime.now(timezone.utc)
    effective_status = (
        "delayed"
        if status == "available"
        and ingestion_delay_seconds is not None
        and ingestion_delay_seconds > 60
        else status
    )
    return LogSourceHealth(
        source=source,
        status=effective_status,
        available_from=available_from,
        available_to=available_to,
        last_successful_fetch_at=now if status == "available" else None,
        ingestion_delay_seconds=ingestion_delay_seconds,
        malformed_records=malformed,
        dropped_records=dropped,
        limited=limited,
        detail=detail,
        supported_filters=sorted(filters),
        collected_levels=list(collected_levels),
    )


def _sql_conditions(filters: LogFilters, *, include_source: bool = True):
    conditions: list[str] = ["timestamp_ms >= ?", "timestamp_ms <= ?"]
    params: list[Any] = [
        int(filters.start.timestamp() * 1000),
        int(filters.end.timestamp() * 1000),
    ]

    def values(column: str, selected: Sequence[Any]) -> None:
        if selected:
            conditions.append(f"{column} IN ({','.join('?' for _ in selected)})")
            params.extend(selected)

    values("level", filters.levels)
    if include_source:
        values("source", filters.sources)
    values("service", filters.services)
    values("environment", filters.environments)
    values("logger", filters.loggers)
    values("event", filters.events)
    values("http_method", filters.http_methods)
    values("http_status", filters.http_statuses)
    values("error_code", filters.error_codes)
    values("error_category", filters.error_categories)
    values("error_signature", filters.error_signatures)
    values("exception_type", filters.exception_types)
    values("failed_stage", filters.failed_stages)
    values("job_type", filters.job_types)
    values("job_status", filters.job_statuses)
    values("attempt_number", filters.attempt_numbers)
    values("generation_type", filters.generation_types)
    values("provider", filters.providers)
    values("model", filters.models)
    if filters.http_status_classes:
        conditions.append(
            "("
            + " OR ".join(
                "http_status BETWEEN ? AND ?" for _ in filters.http_status_classes
            )
            + ")"
        )
        for status_class in filters.http_status_classes:
            params.extend((status_class * 100, status_class * 100 + 99))
    for column, value in (
        ("request_id", filters.request_id),
        ("operation_id", filters.operation_id),
        ("job_id", filters.job_id),
        ("user_id", filters.user_id),
        ("course_id", filters.course_id),
        ("document_id", filters.document_id),
    ):
        if value is not None:
            conditions.append(f"{column} = ?")
            params.append(value)
    if filters.operation_ids:
        placeholders = ",".join("?" for _ in filters.operation_ids)
        conditions.append(
            f"(operation_id IN ({placeholders}) OR "
            f"parent_operation_id IN ({placeholders}))"
        )
        params.extend(filters.operation_ids)
        params.extend(filters.operation_ids)
    if filters.minimum_duration_ms is not None:
        conditions.append("duration_ms >= ?")
        params.append(filters.minimum_duration_ms)
    if filters.maximum_duration_ms is not None:
        conditions.append("duration_ms <= ?")
        params.append(filters.maximum_duration_ms)
    if filters.success is not None:
        conditions.append("success = ?")
        params.append(int(filters.success))
    if filters.search:
        escaped = (
            filters.search.lower()
            .replace("\\", "\\\\")
            .replace("%", "\\%")
            .replace("_", "\\_")
        )
        conditions.append(
            "lower(description || ' ' || event || ' ' || coalesce(error_code, '') || "
            "' ' || coalesce(error_category, '') || ' ' || coalesce(exception_type, '') || "
            "' ' || service || ' ' || logger) LIKE ? ESCAPE '\\'"
        )
        params.append(f"%{escaped}%")
    if filters.before_timestamp is not None and filters.before_id is not None:
        before_ms = int(filters.before_timestamp.timestamp() * 1000)
        conditions.append(
            "(timestamp_ms < ? OR (timestamp_ms = ? AND "
            "(source || ':' || event_id) < ?))"
        )
        params.extend((before_ms, before_ms, filters.before_id))
    return conditions, params


class LocalOperationalSource:
    def __init__(self, path: str) -> None:
        self.path = Path(path)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            f"file:{self.path.as_posix()}?mode=ro",
            uri=True,
            timeout=1,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        connection.execute("PRAGMA busy_timeout=1000")
        return connection

    def fetch(self, filters: LogFilters, *, limit: int) -> SourcePage:
        requested = filters.sources or ("operational", "client_report")
        local_sources = tuple(
            source for source in requested if source in {"operational", "client_report"}
        )
        if not local_sources:
            return SourcePage()
        if not self.path.exists():
            return SourcePage(
                health=[
                    _source_health(
                        source,
                        status="unconfigured",
                        detail="No persisted operational history is available yet.",
                        filters=OPERATIONAL_FILTERS,
                    )
                    for source in local_sources
                ]
            )
        selected = replace(filters, sources=local_sources)
        conditions, params = _sql_conditions(selected)
        malformed = 0
        records: list[AdminLogRecord] = []
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    "SELECT payload_json FROM operational_events WHERE "
                    + " AND ".join(conditions)
                    + " ORDER BY timestamp_ms DESC, source DESC, event_id DESC LIMIT ?",
                    (*params, limit),
                ).fetchall()
                range_row = connection.execute(
                    "SELECT min(timestamp), max(timestamp) FROM operational_events"
                ).fetchone()
                dropped = connection.execute(
                    "SELECT coalesce(sum(dropped_records), 0) FROM operational_event_source_state"
                ).fetchone()[0]
        except (sqlite3.Error, OSError):
            return SourcePage(
                health=[
                    _source_health(
                        source,
                        status="unavailable",
                        detail="The local operational event store could not be read.",
                        filters=OPERATIONAL_FILTERS,
                    )
                    for source in local_sources
                ]
            )
        for row in rows:
            try:
                record = _record_from_payload(json.loads(row["payload_json"]))
            except (TypeError, ValueError, json.JSONDecodeError):
                record = None
            if record is None:
                malformed += 1
            else:
                records.append(record)
        earliest = _parse_db_datetime(range_row[0])
        latest = _parse_db_datetime(range_row[1])
        health = [
            _source_health(
                source,
                available_from=earliest,
                available_to=latest,
                malformed=malformed,
                dropped=int(dropped),
                limited=len(rows) >= limit,
                filters=OPERATIONAL_FILTERS,
                ingestion_delay_seconds=0,
            )
            for source in local_sources
        ]
        return SourcePage(records=records, health=health, limited=len(rows) >= limit)

    def get(self, event_id: str) -> AdminLogRecord | None:
        if not self.path.exists():
            return None
        try:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT payload_json FROM operational_events WHERE event_id = ?",
                    (event_id,),
                ).fetchone()
        except (sqlite3.Error, OSError):
            return None
        if row is None:
            return None
        try:
            return _record_from_payload(json.loads(row["payload_json"]))
        except (TypeError, ValueError, json.JSONDecodeError):
            return None


def _parse_db_datetime(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


class CloudWatchOperationalSource:
    def __init__(self, app_settings: Settings, *, client=None) -> None:
        self.settings = app_settings
        self._client = client

    def _logs_client(self):
        if self._client is None:
            import boto3

            self._client = boto3.client(
                "logs", region_name=self.settings.operational_log_cloudwatch_region
            )
        return self._client

    def fetch(self, filters: LogFilters, *, limit: int) -> SourcePage:
        requested = filters.sources or ("operational", "client_report")
        sources = tuple(
            source for source in requested if source in {"operational", "client_report"}
        )
        if not sources:
            return SourcePage()
        group = self.settings.operational_log_cloudwatch_group
        if group is None:
            return SourcePage(
                health=[
                    _source_health(
                        source,
                        status="unconfigured",
                        detail="The hosted application log group is not configured.",
                        filters=OPERATIONAL_FILTERS,
                    )
                    for source in sources
                ]
            )
        deadline = (
            time.monotonic() + self.settings.operational_log_query_timeout_seconds
        )
        raw_events: list[dict[str, Any]] = []
        token: str | None = None
        malformed = 0
        try:
            while len(raw_events) < MAX_SOURCE_SCAN_RECORDS:
                if time.monotonic() >= deadline:
                    break
                request: dict[str, Any] = {
                    "logGroupName": group,
                    "startTime": int(filters.start.timestamp() * 1000),
                    "endTime": int(filters.end.timestamp() * 1000),
                    "limit": min(10_000, MAX_SOURCE_SCAN_RECORDS - len(raw_events)),
                }
                if token:
                    request["nextToken"] = token
                response = self._logs_client().filter_log_events(**request)
                raw_events.extend(response.get("events", ()))
                next_token = response.get("nextToken")
                if not next_token or next_token == token:
                    break
                token = next_token
        except Exception as exc:
            name = type(exc).__name__
            response = getattr(exc, "response", None)
            error = response.get("Error", {}) if isinstance(response, dict) else {}
            error_code = error.get("Code") if isinstance(error, dict) else None
            detail = (
                "The application is not permitted to read its hosted log group."
                if name == "AccessDeniedException"
                or error_code
                in {"AccessDenied", "AccessDeniedException", "UnauthorizedOperation"}
                else "The hosted operational source could not be read."
            )
            return SourcePage(
                health=[
                    _source_health(
                        source,
                        status="unavailable",
                        detail=detail,
                        filters=OPERATIONAL_FILTERS,
                    )
                    for source in sources
                ]
            )

        records: list[AdminLogRecord] = []
        for event in raw_events:
            message = event.get("message")
            if not isinstance(message, str):
                malformed += 1
                continue
            try:
                candidate = json.loads(message)
            except json.JSONDecodeError:
                malformed += 1
                continue
            safe = sanitize_operational_payload(candidate)
            if safe is None:
                malformed += 1
                continue
            safe["id"] = f"{safe['source']}:{safe['timestamp_ms']}:{safe['event_id']}"
            record = _record_from_payload(safe)
            if record is None or not _record_matches(record, filters):
                malformed += record is None
                continue
            records.append(record)
        records.sort(key=lambda record: (record.timestamp, record.id), reverse=True)
        timed_out = time.monotonic() >= deadline
        limited = bool(token) or len(raw_events) >= MAX_SOURCE_SCAN_RECORDS or timed_out
        latest = max((record.timestamp for record in records), default=None)
        earliest = min((record.timestamp for record in records), default=None)
        detail = (
            "The hosted query timed out before every matching stream was read."
            if timed_out
            else None
        )
        return SourcePage(
            records=records[:limit],
            health=[
                _source_health(
                    source,
                    available_from=earliest,
                    available_to=latest,
                    malformed=malformed,
                    limited=limited,
                    detail=detail,
                    filters=OPERATIONAL_FILTERS,
                )
                for source in sources
            ],
            limited=limited,
        )

    def get(self, encoded: str) -> AdminLogRecord | None:
        parts = encoded.split(":", 1)
        if len(parts) != 2 or not parts[0].isdigit():
            return None
        timestamp_ms = int(parts[0])
        instant = datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc)
        filters = LogFilters(
            start=instant - timedelta(milliseconds=1),
            end=instant + timedelta(milliseconds=1),
        )
        page = self.fetch(filters, limit=MAX_SOURCE_SCAN_RECORDS)
        suffix = f":{encoded}"
        return next(
            (record for record in page.records if record.id.endswith(suffix)), None
        )


def _ai_signature_for_category(category: str | None) -> str:
    value = "\x1f".join(
        ("ai-telemetry", "ai_generation_failed", category or "", "", "")
    )
    return f"v1:{hashlib.sha256(value.encode()).hexdigest()[:24]}"


def _ai_signature(row: AiUsageLog) -> str | None:
    return None if row.success else _ai_signature_for_category(row.error_category)


class AiTelemetrySource:
    def __init__(self, db: Session, app_settings: Settings) -> None:
        self.db = db
        self.settings = app_settings

    def _statement(self, filters: LogFilters):
        statement = select(AiUsageLog).where(
            AiUsageLog.created_at >= filters.start,
            AiUsageLog.created_at <= filters.end,
        )
        clauses = []

        def values(column, selected: Sequence[Any]) -> None:
            if selected:
                clauses.append(column.in_(selected))

        if filters.levels:
            wants_success = "INFO" in filters.levels
            wants_failure = "WARNING" in filters.levels
            if wants_success != wants_failure:
                clauses.append(AiUsageLog.success.is_(wants_success))
            elif not wants_success:
                clauses.append(AiUsageLog.id < 0)
        if filters.sources and "ai_telemetry" not in filters.sources:
            clauses.append(AiUsageLog.id < 0)
        if filters.services and "ai-telemetry" not in filters.services:
            clauses.append(AiUsageLog.id < 0)
        if filters.environments and self.settings.app_env not in filters.environments:
            clauses.append(AiUsageLog.id < 0)
        if filters.loggers and "services.ai_usage_logger" not in filters.loggers:
            clauses.append(AiUsageLog.id < 0)
        if filters.events:
            success_events = "ai_generation_completed" in filters.events
            failed_events = "ai_generation_failed" in filters.events
            if success_events != failed_events:
                clauses.append(AiUsageLog.success.is_(success_events))
            elif not success_events:
                clauses.append(AiUsageLog.id < 0)
        values(AiUsageLog.error_category, filters.error_categories)
        if filters.error_signatures:
            categories = self.db.scalars(
                select(AiUsageLog.error_category)
                .where(AiUsageLog.error_category.is_not(None))
                .distinct()
            ).all()
            matching_categories = [
                category
                for category in categories
                if _ai_signature_for_category(category) in filters.error_signatures
            ]
            if matching_categories:
                values(AiUsageLog.error_category, matching_categories)
            else:
                clauses.append(AiUsageLog.id < 0)
        values(AiUsageLog.generation_type, filters.generation_types)
        values(AiUsageLog.provider, filters.providers)
        values(AiUsageLog.model, filters.models)
        values(AiUsageLog.job_type, filters.job_types)
        values(AiUsageLog.attempt_number, filters.attempt_numbers)
        if filters.error_codes:
            values(AiUsageLog.error_category, filters.error_codes)
        if filters.request_id:
            clauses.append(AiUsageLog.request_id == filters.request_id)
        if filters.operation_id:
            clauses.append(AiUsageLog.operation_id == filters.operation_id)
        if filters.operation_ids:
            values(AiUsageLog.operation_id, filters.operation_ids)
        if filters.job_id is not None:
            clauses.append(AiUsageLog.job_id == filters.job_id)
        if filters.user_id is not None:
            clauses.append(AiUsageLog.user_id == filters.user_id)
        if filters.course_id is not None:
            clauses.append(AiUsageLog.course_id == filters.course_id)
        if filters.success is not None:
            clauses.append(AiUsageLog.success.is_(filters.success))
        if filters.minimum_duration_ms is not None:
            clauses.append(AiUsageLog.latency_ms >= filters.minimum_duration_ms)
        if filters.maximum_duration_ms is not None:
            clauses.append(AiUsageLog.latency_ms <= filters.maximum_duration_ms)
        if filters.job_statuses:
            wants_success = "succeeded" in filters.job_statuses
            wants_failure = "failed" in filters.job_statuses
            if wants_success != wants_failure:
                clauses.append(AiUsageLog.success.is_(wants_success))
            elif not wants_success:
                clauses.append(AiUsageLog.id < 0)
        if filters.search:
            escaped = filters.search.replace("%", "\\%").replace("_", "\\_")
            clauses.append(
                or_(
                    AiUsageLog.generation_type.ilike(f"%{escaped}%", escape="\\"),
                    AiUsageLog.provider.ilike(f"%{escaped}%", escape="\\"),
                    AiUsageLog.model.ilike(f"%{escaped}%", escape="\\"),
                    AiUsageLog.error_category.ilike(f"%{escaped}%", escape="\\"),
                )
            )
        if filters.before_timestamp is not None and filters.before_id is not None:
            record_id = literal("ai_telemetry:") + cast(AiUsageLog.id, String)
            clauses.append(
                or_(
                    AiUsageLog.created_at < filters.before_timestamp,
                    and_(
                        AiUsageLog.created_at == filters.before_timestamp,
                        record_id < filters.before_id,
                    ),
                )
            )
        return statement.where(and_(*clauses)) if clauses else statement

    def fetch(self, filters: LogFilters, *, limit: int) -> SourcePage:
        requested = filters.sources or ("operational", "client_report", "ai_telemetry")
        if "ai_telemetry" not in requested:
            return SourcePage()
        rows = self.db.scalars(
            self._statement(filters)
            .order_by(AiUsageLog.created_at.desc(), AiUsageLog.id.desc())
            .limit(limit)
        ).all()
        records = [self._record(row) for row in rows]
        earliest, latest = self.db.execute(
            select(func.min(AiUsageLog.created_at), func.max(AiUsageLog.created_at))
        ).one()
        return SourcePage(
            records=records,
            health=[
                _source_health(
                    "ai_telemetry",
                    available_from=earliest,
                    available_to=latest,
                    limited=len(rows) >= limit,
                    filters=AI_FILTERS,
                    collected_levels=("INFO", "WARNING"),
                    ingestion_delay_seconds=0,
                )
            ],
            limited=len(rows) >= limit,
        )

    def get(self, record_id: int) -> AdminLogRecord | None:
        row = self.db.get(AiUsageLog, record_id)
        return self._record(row) if row is not None else None

    def _record(self, row: AiUsageLog) -> AdminLogRecord:
        failed = not row.success
        details = {}
        for name in (
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
            "estimated_cost_usd",
            "pricing_version",
        ):
            value = getattr(row, name)
            if value is not None:
                details[name] = value
        return AdminLogRecord(
            id=f"ai_telemetry:{row.id}",
            source="ai_telemetry",
            timestamp=row.created_at,
            level="WARNING" if failed else "INFO",
            service="ai-telemetry",
            environment=self.settings.app_env,
            logger="services.ai_usage_logger",
            event="ai_generation_failed" if failed else "ai_generation_completed",
            description=(
                "An AI generation attempt failed."
                if failed
                else "An AI generation attempt completed."
            ),
            error_code=row.error_category,
            error_category=row.error_category,
            error_signature=_ai_signature(row),
            duration_ms=row.latency_ms,
            request_id=row.request_id,
            operation_id=row.operation_id,
            job_id=row.job_id,
            job_type=row.job_type,
            job_status="failed" if failed else "succeeded",
            attempt_number=row.attempt_number,
            user_id=row.user_id,
            course_id=row.course_id,
            generation_type=row.generation_type,
            provider=row.provider,
            model=row.model,
            success=row.success,
            prompt_tokens=row.prompt_tokens,
            completion_tokens=row.completion_tokens,
            total_tokens=row.total_tokens,
            estimated_cost_usd=row.estimated_cost_usd,
            pricing_version=row.pricing_version,
            details=details,
        )


def _record_matches(record: AdminLogRecord, filters: LogFilters) -> bool:
    def selected(value: Any, choices: Sequence[Any]) -> bool:
        return not choices or value in choices

    if not (filters.start <= record.timestamp <= filters.end):
        return False
    checks = (
        selected(record.level, filters.levels),
        selected(record.source, filters.sources),
        selected(record.service, filters.services),
        selected(record.environment, filters.environments),
        selected(record.logger, filters.loggers),
        selected(record.event, filters.events),
        selected(record.http_method, filters.http_methods),
        selected(record.error_code, filters.error_codes),
        selected(record.error_category, filters.error_categories),
        selected(record.error_signature, filters.error_signatures),
        selected(record.exception_type, filters.exception_types),
        selected(record.failed_stage, filters.failed_stages),
        selected(record.job_type, filters.job_types),
        selected(record.job_status, filters.job_statuses),
        selected(record.attempt_number, filters.attempt_numbers),
        selected(record.generation_type, filters.generation_types),
        selected(record.provider, filters.providers),
        selected(record.model, filters.models),
    )
    if not all(checks):
        return False
    exact = (
        (filters.request_id, record.request_id),
        (filters.operation_id, record.operation_id),
        (filters.job_id, record.job_id),
        (filters.user_id, record.user_id),
        (filters.course_id, record.course_id),
        (filters.document_id, record.document_id),
        (filters.success, record.success),
    )
    if any(expected is not None and expected != actual for expected, actual in exact):
        return False
    if filters.operation_ids and not (
        record.operation_id in filters.operation_ids
        or record.parent_operation_id in filters.operation_ids
    ):
        return False
    if filters.before_timestamp is not None and filters.before_id is not None:
        if (record.timestamp, record.id) >= (
            filters.before_timestamp,
            filters.before_id,
        ):
            return False
    if filters.http_statuses and record.http_status not in filters.http_statuses:
        return False
    if filters.http_status_classes and (
        record.http_status is None
        or record.http_status // 100 not in filters.http_status_classes
    ):
        return False
    if filters.minimum_duration_ms is not None and (
        record.duration_ms is None or record.duration_ms < filters.minimum_duration_ms
    ):
        return False
    if filters.maximum_duration_ms is not None and (
        record.duration_ms is None or record.duration_ms > filters.maximum_duration_ms
    ):
        return False
    if filters.search:
        haystack = " ".join(
            value
            for value in (
                record.description,
                record.event,
                record.error_code,
                record.error_category,
                record.exception_type,
                record.service,
                record.logger,
            )
            if value
        ).lower()
        if filters.search.lower() not in haystack:
            return False
    return True


class LogReadService:
    def __init__(
        self,
        db: Session,
        *,
        app_settings: Settings = settings,
        cloudwatch_client=None,
    ) -> None:
        self.db = db
        self.settings = app_settings
        self.operational = (
            CloudWatchOperationalSource(app_settings, client=cloudwatch_client)
            if app_settings.is_hosted
            else LocalOperationalSource(app_settings.operational_log_path)
        )
        self.ai = AiTelemetrySource(db, app_settings)

    def _validate_capabilities(self, filters: LogFilters) -> None:
        selected = set(filters.sources)
        active = filters.active_names()
        if selected == {"ai_telemetry"}:
            unsupported = active - AI_FILTERS
            if unsupported:
                raise UnsupportedLogFilterError(
                    "AI telemetry does not support: " + ", ".join(sorted(unsupported))
                )

    def _pages(self, filters: LogFilters, *, limit: int) -> list[SourcePage]:
        self._validate_capabilities(filters)
        pages = [self.operational.fetch(filters, limit=limit)]
        if not (filters.active_names() - AI_FILTERS):
            pages.append(self.ai.fetch(filters, limit=limit))
        elif not filters.sources or "ai_telemetry" in filters.sources:
            pages.append(
                SourcePage(
                    health=[
                        _source_health(
                            "ai_telemetry",
                            status="unavailable",
                            detail="Excluded because this source cannot apply every active filter.",
                            filters=AI_FILTERS,
                            collected_levels=("INFO", "WARNING"),
                        )
                    ]
                )
            )
        return pages

    def list(
        self,
        filters: LogFilters,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        cursor: str | None = None,
    ) -> AdminLogList:
        if not 1 <= limit <= MAX_PAGE_SIZE:
            raise InvalidLogQueryError("Page size must be between 1 and 200.")
        after: tuple[datetime, str] | None = None
        if cursor:
            after = self._decode_cursor(cursor, filters)
        paged_filters = (
            replace(filters, before_timestamp=after[0], before_id=after[1])
            if after is not None
            else filters
        )
        pages = self._pages(paged_filters, limit=limit + 1)
        records = [record for page in pages for record in page.records]
        if after is not None:
            records = [
                record for record in records if (record.timestamp, record.id) < after
            ]
        records.sort(key=lambda record: (record.timestamp, record.id), reverse=True)
        visible = records[:limit]
        has_more = len(records) > limit or any(page.limited for page in pages)
        health = [item for page in pages for item in page.health]
        if (
            not visible
            and health
            and all(item.status in {"unavailable", "unconfigured"} for item in health)
        ):
            if all(item.status == "unavailable" for item in health):
                raise LogSourcesUnavailableError(
                    "Every selected log source is unavailable."
                )
        next_cursor = (
            self._encode_cursor(filters, visible[-1]) if has_more and visible else None
        )
        partial = any(item.status != "available" for item in health) or any(
            page.limited for page in pages
        )
        return AdminLogList(
            records=visible,
            next_cursor=next_cursor,
            query_window=LogQueryWindow(start=filters.start, end=filters.end),
            last_updated_at=datetime.now(timezone.utc),
            source_health=health,
            partial=partial,
            limited=any(page.limited for page in pages),
            omitted_records=sum(
                max((item.malformed_records for item in page.health), default=0)
                for page in pages
            ),
        )

    def summary(self, filters: LogFilters) -> AdminLogSummary:
        pages = self._pages(filters, limit=MAX_SOURCE_SCAN_RECORDS)
        health = [item for page in pages for item in page.health]
        limited = any(page.limited for page in pages)
        records = [record for page in pages for record in page.records]
        counts = LogLevelCounts()
        groups: list[LogErrorGroup] = []
        distribution: list[LogTimeBucket] = []
        if not limited and all(item.status == "available" for item in health):
            failed = [
                record
                for record in records
                if record.level in {"WARNING", "ERROR", "CRITICAL"}
            ]
            counts = LogLevelCounts(
                events=len(records),
                warnings=sum(record.level == "WARNING" for record in records),
                errors=sum(record.level in {"ERROR", "CRITICAL"} for record in records),
                distinct_failed_operations=len(
                    {record.operation_id for record in failed if record.operation_id}
                ),
            )
            groups = self._groups(failed)
            distribution = self._distribution(records, filters)
        return AdminLogSummary(
            counts=counts,
            error_groups=groups,
            distribution=distribution,
            query_window=LogQueryWindow(start=filters.start, end=filters.end),
            last_updated_at=datetime.now(timezone.utc),
            source_health=health,
            partial=limited or any(item.status != "available" for item in health),
            limited=limited,
        )

    def detail(self, event_id: str) -> AdminLogEventDetail:
        record: AdminLogRecord | None = None
        if event_id.startswith("ai_telemetry:"):
            value = event_id.removeprefix("ai_telemetry:")
            record = self.ai.get(int(value)) if value.isdigit() else None
        elif event_id.startswith(("operational:", "client_report:")):
            encoded = event_id.split(":", 1)[1]
            record = self.operational.get(encoded)
        if record is None:
            raise LogRecordNotFoundError("The log event was not found or has expired.")
        related = {}
        if record.operation_id:
            related["operation_id"] = record.operation_id
        if record.job_id is not None and record.job_type:
            related["job"] = f"{record.job_type}:{record.job_id}"
        if record.error_signature:
            related["error_signature"] = record.error_signature
        return AdminLogEventDetail(record=record, related_filter=related)

    def trace(self, event_id: str) -> AdminLogTrace:
        anchor = self.detail(event_id).record
        operation_ids = {
            value
            for value in (anchor.operation_id, anchor.parent_operation_id)
            if value
        }
        if anchor.job_id is not None and anchor.job_type:
            operation_ids.update(self._job_operations(anchor.job_type, anchor.job_id))
        if not operation_ids:
            return AdminLogTrace(
                anchor_id=anchor.id,
                records=[anchor],
                correlation_status="none",
                message="No correlation information",
                source_health=[],
            )
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=MAX_QUERY_DAYS)
        filters = LogFilters(
            start=start,
            end=end,
            operation_ids=tuple(sorted(operation_ids)),
        )
        pages = self._pages(filters, limit=MAX_SOURCE_SCAN_RECORDS)
        records = [record for page in pages for record in page.records]
        discovered = {record.operation_id for record in records if record.operation_id}
        if not discovered.issubset(operation_ids):
            operation_ids.update(discovered)
            filters = replace(filters, operation_ids=tuple(sorted(operation_ids)))
            pages = self._pages(filters, limit=MAX_SOURCE_SCAN_RECORDS)
            records = [record for page in pages for record in page.records]
        if all(record.id != anchor.id for record in records):
            records.append(anchor)
        records.sort(key=lambda record: (record.timestamp, record.id))
        health = [item for page in pages for item in page.health]
        return AdminLogTrace(
            anchor_id=anchor.id,
            operation_id=anchor.operation_id,
            records=records,
            correlation_status="correlated",
            source_health=health,
            partial=any(page.limited for page in pages),
        )

    def export(
        self, filters: LogFilters
    ) -> tuple[list[AdminLogRecord], bool, list[LogSourceHealth]]:
        pages = self._pages(filters, limit=MAX_EXPORT_RECORDS + 1)
        records = [record for page in pages for record in page.records]
        health = [item for page in pages for item in page.health]
        if (
            not records
            and health
            and all(item.status == "unavailable" for item in health)
        ):
            raise LogSourcesUnavailableError(
                "Every selected log source is unavailable."
            )
        records.sort(key=lambda record: (record.timestamp, record.id), reverse=True)
        truncated = len(records) > MAX_EXPORT_RECORDS or any(
            page.limited for page in pages
        )
        return (
            records[:MAX_EXPORT_RECORDS],
            truncated,
            health,
        )

    def _job_operations(self, job_type: str, job_id: int) -> set[str]:
        values: set[str] = set()
        if job_type == "course_document_processing":
            row = self.db.get(ProcessingJob, job_id)
            if row:
                values.add(f"processing_job:course:{row.id}")
                if row.parent_operation_id:
                    values.add(row.parent_operation_id)
        elif job_type == "profile_document_processing":
            row = self.db.get(ProfileProcessingJob, job_id)
            if row:
                values.add(f"processing_job:profile:{row.id}")
                if row.parent_operation_id:
                    values.add(row.parent_operation_id)
        else:
            row = self.db.get(GenerationJob, job_id)
            if row and row.job_type == job_type:
                chain = [row]
                if row.retry_of_job_id:
                    original = self.db.get(GenerationJob, row.retry_of_job_id)
                    if original:
                        chain.append(original)
                retry = self.db.scalar(
                    select(GenerationJob).where(GenerationJob.retry_of_job_id == row.id)
                )
                if retry:
                    chain.append(retry)
                for item in chain:
                    values.add(f"generation_job:{item.job_type}:{item.id}")
                    if item.parent_operation_id:
                        values.add(item.parent_operation_id)
        return values

    @staticmethod
    def _groups(records: list[AdminLogRecord]) -> list[LogErrorGroup]:
        grouped: dict[str, list[AdminLogRecord]] = defaultdict(list)
        for record in records:
            if record.error_signature:
                grouped[record.error_signature].append(record)
        result = []
        for signature, occurrences in grouped.items():
            ordered = sorted(occurrences, key=lambda record: record.timestamp)
            first = ordered[0]
            operation_ids = {
                record.operation_id for record in occurrences if record.operation_id
            }
            result.append(
                LogErrorGroup(
                    signature=signature,
                    service=first.service,
                    event=first.event,
                    error_code=first.error_code or first.error_category,
                    exception_type=first.exception_type,
                    source_location=first.source_location,
                    first_occurrence=ordered[0].timestamp,
                    last_occurrence=ordered[-1].timestamp,
                    event_count=len(occurrences),
                    distinct_operations=len(operation_ids) if operation_ids else None,
                )
            )
        result.sort(
            key=lambda group: (group.event_count, group.last_occurrence), reverse=True
        )
        return result[:10]

    @staticmethod
    def _distribution(
        records: list[AdminLogRecord], filters: LogFilters
    ) -> list[LogTimeBucket]:
        duration = filters.end - filters.start
        seconds = (
            300
            if duration <= timedelta(hours=1)
            else 3600
            if duration <= timedelta(days=1)
            else 86_400
        )
        buckets: dict[int, Counter[str]] = defaultdict(Counter)
        for record in records:
            epoch = int(record.timestamp.timestamp())
            bucket = epoch - epoch % seconds
            buckets[bucket][record.level] += 1
        return [
            LogTimeBucket(
                start=datetime.fromtimestamp(bucket, tz=timezone.utc),
                events=sum(counts.values()),
                warnings=counts["WARNING"],
                errors=counts["ERROR"] + counts["CRITICAL"],
            )
            for bucket, counts in sorted(buckets.items())
        ]

    def _encode_cursor(self, filters: LogFilters, record: AdminLogRecord) -> str:
        payload = json.dumps(
            {
                "v": 1,
                "q": filters.fingerprint(),
                "t": record.timestamp.isoformat(),
                "i": record.id,
            },
            separators=(",", ":"),
        ).encode()
        signature = hmac.new(
            self.settings.jwt_secret_key.encode(), payload, hashlib.sha256
        ).digest()
        return base64.urlsafe_b64encode(payload + signature).decode().rstrip("=")

    def _decode_cursor(self, cursor: str, filters: LogFilters) -> tuple[datetime, str]:
        try:
            padded = cursor + "=" * (-len(cursor) % 4)
            decoded = base64.urlsafe_b64decode(padded)
            payload, signature = decoded[:-32], decoded[-32:]
            expected = hmac.new(
                self.settings.jwt_secret_key.encode(), payload, hashlib.sha256
            ).digest()
            if not hmac.compare_digest(signature, expected):
                raise ValueError
            value = json.loads(payload)
            if value.get("v") != 1 or value.get("q") != filters.fingerprint():
                raise ValueError
            timestamp = datetime.fromisoformat(value["t"])
            record_id = value["i"]
            if timestamp.tzinfo is None or not isinstance(record_id, str):
                raise ValueError
            return timestamp.astimezone(timezone.utc), record_id
        except Exception as exc:
            raise InvalidLogCursorError(
                "The cursor does not belong to this fixed filter window."
            ) from exc
