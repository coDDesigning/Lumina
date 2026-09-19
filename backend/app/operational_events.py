"""Durable, content-free operational events for self-hosted deployments."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

from backend.app.observability import redact

_SAFE_TOKEN = re.compile(r"[A-Za-z0-9._:/-]{1,200}")
_SAFE_LOGGER = re.compile(r"[A-Za-z0-9_.-]{1,200}")
_SAFE_STACK = re.compile(r"[\w./<>-]+:\d+ in [A-Za-z0-9_<>.]+")
_SAFE_SCHEMA_NAME = re.compile(r"(?:[a-z][a-z0-9_]{0,39}|\*)")
_SAFE_VALIDATION_ERROR = re.compile(
    r"(?:\*|[a-z][a-z0-9_]{0,39}|\d+)(?:\.(?:\*|[a-z][a-z0-9_]{0,39}|\d+))*: [a-z][a-z0-9_.]{0,79}"
)
_LEVELS = {"INFO", "WARNING", "ERROR", "CRITICAL"}
_STATUSES = {"queued", "running", "succeeded", "failed", "timed_out"}
_MAX_DETAILS_ITEMS = 40
_MAX_MESSAGE = 1000
_MAX_EXCEPTION_MESSAGE = 500
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_PROJECT_LOGGER_ROOTS = {
    "__main__",
    "backend",
    "lumina",
    "main",
    "routes",
    "schemas",
    "services",
    "tasks",
    "utils",
    "workers",
}

EVENT_DESCRIPTIONS = {
    "account_purge_failed": "An account could not be purged, so its deletion tombstone was kept for a retry.",
    "account_purge_finished": "An account purge pass finished.",
    "account_purge_pass_failed": "An account purge pass failed unexpectedly.",
    "ad_telemetry": "An advertisement slot reported an impression or a click.",
    "admin_credits_changed": "An administrator changed an account credit balance.",
    "admin_role_changed": "An administrator changed an account role.",
    "admin_user_ban_changed": "An administrator changed an account ban state.",
    "aged_account_tombstone_detected": "A deleted account outlived the purge that should have erased it.",
    "aged_tombstone_detected": "A deleted record outlived the purge that should have removed it.",
    "ai_generation_failed": "An AI generation attempt failed.",
    "ai_metrics_emit_failed": "AI provider health metrics could not be emitted.",
    "ai_route_failed": "An AI route returned a server error for a generation.",
    "ai_route_refused": "An AI route refused a generation request.",
    "ai_usage_cleanup_completed": "An AI usage retention cleanup finished.",
    "ai_usage_cost_estimate_failed": "An AI usage cost estimate could not be calculated.",
    "ai_usage_owner_missing": "AI usage telemetry was skipped because no account was attached to the generation.",
    "ai_usage_write_failed": "AI usage telemetry could not be persisted.",
    "ai_vendors_available": "The configured AI vendor fallback chain was reported at startup.",
    "byok_key_decrypt_failed": "A stored provider API key could not be decrypted, so the default key was used.",
    "client_error_reported": "The browser reported an unhandled interface error.",
    "conversation_citations_unreadable": "A stored conversation message had citations that could not be read.",
    "course_cleanup_failed": "A course could not be fully erased, so its metadata was kept for a retry.",
    "course_purge_document_failed": "A deleted document could not be purged, so its tombstone was kept for a retry.",
    "course_purge_failed": "A course could not be purged, so its deletion tombstone was kept for a retry.",
    "course_purge_finished": "A course purge pass finished.",
    "course_purge_pass_failed": "A course purge pass failed unexpectedly.",
    "course_purge_profile_document_failed": "A deleted profile document could not be purged, so its tombstone was kept for a retry.",
    "course_purge_worker_started": "The purge worker started.",
    "course_purge_worker_stopped": "The purge worker stopped.",
    "credit_grant_already_recorded": "A periodic credit grant for this period already existed.",
    "credit_refund_already_settled": "A generation refund had already been settled.",
    "document_chunking_retried": "Splitting a document into chunks failed once and was retried.",
    "document_commit_outcome_unknown": "Whether a document registration was saved could not be determined.",
    "document_commit_recovered": "A document was found saved after an uncertain registration commit.",
    "document_delete_failed": "A course document could not be deleted.",
    "document_lock_release_failed": "Generation locks on documents could not be released and will expire on their own.",
    "document_pipeline_callback_failed": "A document processing progress callback failed.",
    "document_pipeline_stage_failed": "A document processing stage failed unexpectedly.",
    "document_purge_finished": "A document purge pass finished.",
    "document_purge_pass_failed": "A document purge pass failed unexpectedly.",
    "document_registration_failed": "An uploaded document could not be registered.",
    "document_registration_rollback_failed": "A failed document registration could not be rolled back.",
    "document_storage_provider_mismatch": "A document belongs to a storage backend this deployment is not using.",
    "document_upload_cleanup_failed": "The stored file of an unregistered upload could not be removed.",
    "document_upload_hash_failed": "An uploaded document could not be hashed.",
    "document_worker_iteration_failed": "A document worker failed while claiming or running a job.",
    "document_worker_recycle_required": "A document worker hit an unrecoverable error and exited to be restarted.",
    "document_worker_shutdown_requested": "A document worker was asked to shut down and stopped claiming jobs.",
    "document_worker_slots_started": "A document worker started its concurrent job slots.",
    "document_worker_started": "A document worker started.",
    "document_worker_stopped": "A document worker stopped.",
    "email_delivery_failed": "An outbound email could not be delivered.",
    "email_verified": "An account proved ownership of its email address.",
    "embedding_backfill_completed": "An embedding backfill run finished.",
    "embedding_backfill_worker_started": "The embedding backfill worker started.",
    "embedding_backfill_worker_stopped": "The embedding backfill worker stopped.",
    "exam_artifact_rejected": "A generated exam artifact was refused because it did not match what was requested.",
    "exam_question_extraction_failed": "Questions could not be extracted from a past exam paper.",
    "exam_question_extraction_provider_unavailable": "No AI provider was available to extract questions from a past exam paper.",
    "exam_question_extraction_status_write_failed": "The extraction status of a past exam paper could not be saved.",
    "exam_question_extraction_truncated": "A past exam paper was only partly read because it exceeded the extraction budget.",
    "exam_topic_unlock_race_refunded": "A concurrent exam topic unlock was detected and its duplicate charge was refunded.",
    "exam_topic_unlock_release_failed": "An exam topic unlock could not be undone after the work it paid for failed.",
    "generation_attempt_timeout": "A generation attempt exceeded its time limit.",
    "generation_failed_after_lease_lost": "A generation job failed after its lease had already been released.",
    "generation_heartbeat_failed": "A generation worker could not renew the lease on its job.",
    "generation_heartbeat_recovered": "A generation worker renewed its job lease again after failed attempts.",
    "generation_job_claimed": "A generation job was claimed by a worker.",
    "generation_job_completed": "A generation job completed successfully.",
    "generation_job_enqueued": "A generation job was accepted for processing.",
    "generation_job_failed": "A generation job attempt failed.",
    "generation_job_retried": "A generation job was queued for another attempt.",
    "generation_job_type_unknown": "A generation job had a type this worker cannot run, so it was failed.",
    "generation_jobs_recovered": "Expired generation jobs were returned to the queue.",
    "generation_lease_lost": "A generation job lost its lease to another worker.",
    "generation_locks_released": "Expired document generation locks were released.",
    "generation_recovery_failed": "Recovering expired generation jobs failed.",
    "generation_worker_iteration_failed": "A generation worker failed unexpectedly while claiming or running a job.",
    "generation_worker_shutdown_requested": "A generation worker stopped claiming jobs because shutdown was requested.",
    "generation_worker_started": "A generation worker started.",
    "generation_worker_stopped": "A generation worker stopped.",
    "http_authorization_denied": "An HTTP request was denied by authorization.",
    "http_not_found": "An HTTP request asked for something that does not exist.",
    "http_request_completed": "An HTTP request completed.",
    "http_request_failed": "An HTTP request failed unexpectedly.",
    "http_request_rate_limited": "An HTTP request was rejected by a rate limit.",
    "http_request_slow": "An HTTP request exceeded the slow-request threshold.",
    "http_validation_rejected": "An HTTP request was rejected by validation or a business rule.",
    "image_transcode_failed": "An uploaded image could not be converted to a PDF.",
    "image_understanding_disabled": "The configured model cannot read images, so visual analysis is switched off.",
    "image_understanding_usage_owner_missing": "Visual analysis usage could not be attributed to an account.",
    "image_understanding_usage_persist_failed": "Visual analysis usage telemetry could not be persisted.",
    "image_understanding_usage_report_failed": "Visual analysis usage could not be reported.",
    "image_upload_ocr_skipped": "Text recognition was skipped for an uploaded image.",
    "library_log": "A third-party library wrote a log line.",
    "maintenance_task_failed": "A periodic maintenance task failed.",
    "password_reset_email_undelivered": "A password reset email could not be delivered.",
    "pdf_page_pool_unavailable": "Parallel PDF page processing was unavailable, so pages were processed one at a time.",
    "pdf_text_extraction_failed": "Text could not be extracted from a PDF.",
    "pdf_validation_failed": "An uploaded PDF could not be validated.",
    "permanent_document_failure": "Document processing failed permanently.",
    "permanent_generation_failure": "Generation failed permanently.",
    "processing_claim_lost": "A document processing job lost its claim before it could complete.",
    "processing_failure_record_failed": "A document processing failure could not be recorded on its job.",
    "processing_heartbeat_failed": "A document processing job could not renew its lease.",
    "processing_heartbeat_recovered": "A document processing job renewed its lease again after failed attempts.",
    "processing_heartbeat_stop_timeout": "A document processing lease heartbeat did not stop before the job was finalized.",
    "processing_job_claimed": "A document processing job was claimed by a worker.",
    "processing_job_completed": "A document processing job completed successfully.",
    "processing_job_enqueued": "A document was accepted for background processing.",
    "processing_job_failed": "A document processing attempt failed.",
    "processing_job_finalize_failed": "A document processing job could not be finalized.",
    "processing_job_finalize_timeout": "Finalizing a document job exceeded its database time limit.",
    "processing_job_retried": "A document processing job was queued for another attempt.",
    "processing_jobs_recovered": "Expired document processing jobs were returned to the queue.",
    "processing_pages_persist_failed": "The extracted pages of a document could not be saved.",
    "processing_stage_started": "A document processing stage started.",
    "processing_subprocess_reap_failed": "A document extraction subprocess could not be stopped, so the worker exited.",
    "processing_vector_persist_failed": "Document vectors could not be saved while finalizing a processing job.",
    "profile_document_delete_failed": "A profile document could not be deleted.",
    "profile_document_registration_failed": "An uploaded profile document could not be registered.",
    "profile_document_upload_cleanup_failed": "The stored file of an unregistered profile document could not be removed.",
    "profile_knowledge_retrieval_failed": "Relevant passages could not be retrieved from a user's profile documents.",
    "provider_attempt": "An AI provider attempt completed.",
    "provider_exhausted": "An AI provider exhausted its retry budget.",
    "provider_failed": "An AI provider attempt failed.",
    "quiz_answer_document_unreadable": "A stored quiz question had an answer document that could not be read.",
    "quiz_citations_unreadable": "A stored quiz question had citations that could not be read.",
    "quiz_grading_provider_unavailable": "No AI provider was available to grade open-ended quiz answers.",
    "quiz_session_submit_retried": "A timed quiz submission lost a write race and was retried.",
    "rate_limit_rejected": "A request was rejected by an application rate limit.",
    "readiness_check_failed": "The API readiness probe found a required dependency unavailable.",
    "self_hosted_backup_completed": "A self-hosted backup completed.",
    "self_hosted_restore_completed": "A self-hosted restore completed.",
    "self_hosted_restore_object_unavailable": "A restored document object could not be read back.",
    "self_hosted_restore_requires_embedding_backfill": "A restored deployment still needs its embeddings rebuilt.",
    "self_hosted_restore_verification_failed": "Restore verification could not complete.",
    "stored_document_delete_failed": "A stored document could not be deleted from its backend.",
    "stored_document_deleted": "A stored document was deleted from its backend.",
    "stored_json_unreadable": "A stored JSON value could not be read back and was skipped.",
    "text_document_decode_failed": "An uploaded text document could not be decoded.",
    "token_iat_unreadable": "A token was rejected because its issued-at timestamp could not be read.",
    "unprotected_admin_bootstrap_granted": "An account became an administrator through unprotected bootstrap.",
    "unprotected_admin_bootstrap_warning": "The deployment is running with unprotected administrator bootstrap.",
    "vector_store_cache_clear_failed": "The vector store's cached system could not be cleared.",
    "vector_store_reopened": "The vector store was reopened after a failed operation.",
    "verification_email_undelivered": "A verification email could not be delivered.",
    "visual_analysis_failed": "A figure in a document could not be described.",
    "visual_analysis_temporarily_unavailable": "A figure in a document could not be described because the visual service was temporarily unavailable.",
    "visual_description_enqueue_failed": "A document's visual description job could not be queued.",
    "visual_description_sweep": "A sweep enqueued outstanding visual descriptions.",
    "visual_detection_degraded": "Table or drawing detection failed on some pages of a document.",
    "visual_provider_failed": "The visual description provider failed unexpectedly.",
    "web_root_missing": "The configured interface build directory does not exist.",
    "worker_readiness_check_failed": "A worker found a required dependency unavailable during its readiness check.",
    "worker_readiness_check_succeeded": "A worker readiness check passed.",
}

RUNBOOKS = {
    "permanent_document_failure": "/docs/database#document-processing",
    "permanent_generation_failure": "/docs/ai_providers",
    "processing_job_finalize_timeout": "/docs/database#document-processing",
    "self_hosted_restore_verification_failed": "/docs/runbooks/hosted-backup-restore.md",
}

_DETAIL_FIELDS = {
    "action",
    "ai_response_bytes",
    "ai_response_keys",
    "ai_response_sha256",
    "ai_response_type",
    "ai_validation_errors",
    "application_version",
    "auth_state",
    "client_fingerprint",
    "error_class",
    "item_count",
    "lock_holder",
    "maintenance_task",
    "page_number",
    "profile_document_id",
    "rate_limit_control",
    "rate_limit_feature",
    "reason",
    "related_request_id",
    "response_bytes",
    "retry_after_seconds",
    "visual_index",
    "worker_id",
}


def _safe_string(value: object, *, logger: bool = False) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    pattern = _SAFE_LOGGER if logger else _SAFE_TOKEN
    return candidate if pattern.fullmatch(candidate) else None


def _safe_identifier(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value >= 0:
        return value
    return None


def _safe_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if 0 <= number <= 86_400_000 else None


def _safe_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _safe_stack(payload: Mapping[str, Any]) -> list[str]:
    stack = payload.get("stack")
    if not isinstance(stack, list):
        return []
    return [
        item for item in stack if isinstance(item, str) and _SAFE_STACK.fullmatch(item)
    ]


def _safe_text(value: object, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    text = _CONTROL_CHARACTERS.sub(" ", redact(value)).strip()
    return text[:limit] or None


def _is_project_logger(name: str) -> bool:
    return name.split(".", 1)[0] in _PROJECT_LOGGER_ROOTS


def _signature(payload: Mapping[str, Any], source_location: str | None) -> str | None:
    if payload.get("level") not in {"WARNING", "ERROR", "CRITICAL"}:
        return None
    parts = (
        payload.get("service"),
        payload.get("event"),
        payload.get("error_code") or payload.get("error_category"),
        payload.get("exception_type"),
        source_location,
    )
    digest = hashlib.sha256(
        "\x1f".join(str(value or "") for value in parts).encode("utf-8")
    ).hexdigest()[:24]
    return f"v1:{digest}"


def sanitize_operational_payload(
    payload: Mapping[str, Any],
    *,
    source: str | None = None,
) -> dict[str, Any] | None:
    """Validate an external/logging payload and discard all unreviewed content."""
    timestamp = _safe_timestamp(payload.get("timestamp"))
    level = payload.get("level")
    service = _safe_string(payload.get("service"))
    environment = _safe_string(payload.get("environment"))
    logger = _safe_string(payload.get("logger"), logger=True)
    event = _safe_string(payload.get("event"))
    event_id = _safe_string(payload.get("event_id"))
    if (
        timestamp is None
        or level not in _LEVELS
        or service is None
        or environment is None
        or logger is None
        or event is None
        or event_id is None
        or event == "cloudwatch_emf"
    ):
        return None
    if event == "application_log" and not _is_project_logger(logger):
        event = "library_log"
    message = _safe_text(payload.get("message"), _MAX_MESSAGE)

    source_type = source or (
        "client_report" if event == "client_error_reported" else "operational"
    )
    if source_type not in {"operational", "client_report"}:
        return None

    safe: dict[str, Any] = {
        "id": f"{source_type}:{event_id}",
        "event_id": event_id,
        "source": source_type,
        "timestamp": timestamp.isoformat(),
        "timestamp_ms": int(timestamp.timestamp() * 1000),
        "level": level,
        "service": service,
        "environment": environment,
        "logger": logger,
        "event": event,
        "description": EVENT_DESCRIPTIONS.get(event) or message or "Application event.",
    }
    if message is not None:
        safe["message"] = message
    exception_message = _safe_text(
        payload.get("exception_message"), _MAX_EXCEPTION_MESSAGE
    )
    if exception_message is not None:
        safe["exception_message"] = exception_message
    for field in (
        "error_code",
        "error_category",
        "error_class",
        "exception_type",
        "failed_stage",
        "generation_type",
        "http_method",
        "job_type",
        "model",
        "operation_id",
        "parent_operation_id",
        "pricing_version",
        "provider",
        "request_id",
        "related_request_id",
        "stage",
    ):
        value = _safe_string(payload.get(field))
        if value is not None:
            safe[field] = value

    job_status = _safe_string(payload.get("job_status"))
    if job_status in _STATUSES:
        safe["job_status"] = job_status

    path = payload.get("http_path")
    if (
        isinstance(path, str)
        and len(path) <= 200
        and path.startswith("/")
        and "?" not in path
        and "#" not in path
        and not any(ord(character) < 32 for character in path)
    ):
        safe["http_path"] = path

    document_id = _safe_string(payload.get("document_id"))
    if document_id is not None:
        safe["document_id"] = document_id

    for field in (
        "attempt_number",
        "completion_tokens",
        "course_id",
        "http_status",
        "job_id",
        "prompt_tokens",
        "total_tokens",
        "user_id",
    ):
        value = _safe_identifier(payload.get(field))
        if value is not None:
            safe[field] = value

    duration = _safe_number(payload.get("duration_ms"))
    if duration is not None:
        safe["duration_ms"] = duration
    estimated_cost = payload.get("estimated_cost_usd")
    if isinstance(estimated_cost, (int, float)) and not isinstance(
        estimated_cost, bool
    ):
        if 0 <= float(estimated_cost) <= 1_000_000:
            safe["estimated_cost_usd"] = float(estimated_cost)
    if isinstance(payload.get("success"), bool):
        safe["success"] = payload["success"]

    chain = payload.get("exception_chain")
    if isinstance(chain, list):
        safe_chain = [_safe_string(item) for item in chain[:10]]
        safe["exception_chain"] = [item for item in safe_chain if item is not None]

    stack = _safe_stack(payload)
    location = stack[-1] if stack else None
    if stack:
        safe["stack"] = stack
    if location is not None:
        safe["source_location"] = location
    signature = _signature(safe, location)
    if signature is not None:
        safe["error_signature"] = signature
    runbook = RUNBOOKS.get(event)
    if runbook is not None:
        safe["runbook"] = runbook

    details: dict[str, Any] = {}
    owner_id = _safe_identifier(payload.get("owner_id"))
    if owner_id is not None:
        details["target_user_id"] = owner_id
    for field in _DETAIL_FIELDS:
        value = payload.get(field)
        if field == "ai_response_keys" and isinstance(value, list):
            details[field] = [
                item
                if isinstance(item, str) and _SAFE_SCHEMA_NAME.fullmatch(item)
                else "*"
                for item in value[:20]
            ]
        elif field == "ai_validation_errors" and isinstance(value, list):
            details[field] = [
                item
                if isinstance(item, str) and _SAFE_VALIDATION_ERROR.fullmatch(item)
                else "*"
                for item in value[:20]
            ]
        elif isinstance(value, str):
            safe_value = _safe_string(value)
            if safe_value is not None:
                details[field] = safe_value
        elif isinstance(value, bool):
            details[field] = value
        elif (safe_number := _safe_number(value)) is not None:
            details[field] = safe_number
        if len(details) >= _MAX_DETAILS_ITEMS:
            break
    safe["details"] = details
    return safe


class OperationalEventHandler(logging.Handler):
    """A best-effort SQLite sink independent from application transactions."""

    _lumina_operational_handler = True

    def __init__(
        self,
        path: str,
        *,
        service: str,
        environment: str,
        retention_days: int,
        max_records: int,
    ) -> None:
        super().__init__(logging.INFO)
        from backend.app.observability import JsonFormatter

        self.path = Path(path)
        self.retention_days = retention_days
        self.max_records = max_records
        self.source_key = f"{service}:{environment}"
        self._pending_dropped = 0
        self._last_cleanup = 0.0
        self.setFormatter(JsonFormatter(service=service, environment=environment))
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self._connect() as connection:
                self._initialize(connection)
        except Exception:
            self._pending_dropped += 1

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=0.1)
        connection.execute("PRAGMA busy_timeout=100")
        return connection

    @staticmethod
    def _initialize(connection: sqlite3.Connection) -> None:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS operational_events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL UNIQUE,
                timestamp TEXT NOT NULL,
                timestamp_ms INTEGER NOT NULL,
                level TEXT NOT NULL,
                source TEXT NOT NULL,
                service TEXT NOT NULL,
                environment TEXT NOT NULL,
                logger TEXT NOT NULL,
                event TEXT NOT NULL,
                description TEXT NOT NULL,
                error_code TEXT,
                error_category TEXT,
                exception_type TEXT,
                error_signature TEXT,
                source_location TEXT,
                operation_id TEXT,
                parent_operation_id TEXT,
                request_id TEXT,
                job_id INTEGER,
                job_type TEXT,
                job_status TEXT,
                attempt_number INTEGER,
                stage TEXT,
                failed_stage TEXT,
                user_id INTEGER,
                course_id INTEGER,
                document_id TEXT,
                http_method TEXT,
                http_path TEXT,
                http_status INTEGER,
                duration_ms REAL,
                generation_type TEXT,
                provider TEXT,
                model TEXT,
                success INTEGER,
                payload_json TEXT NOT NULL,
                ingested_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS ix_operational_events_time
                ON operational_events(timestamp_ms DESC, sequence DESC);
            CREATE INDEX IF NOT EXISTS ix_operational_events_level_time
                ON operational_events(level, timestamp_ms DESC);
            CREATE INDEX IF NOT EXISTS ix_operational_events_service_time
                ON operational_events(service, timestamp_ms DESC);
            CREATE INDEX IF NOT EXISTS ix_operational_events_event_time
                ON operational_events(event, timestamp_ms DESC);
            CREATE INDEX IF NOT EXISTS ix_operational_events_error_time
                ON operational_events(error_code, timestamp_ms DESC);
            CREATE INDEX IF NOT EXISTS ix_operational_events_operation_time
                ON operational_events(operation_id, timestamp_ms);
            CREATE INDEX IF NOT EXISTS ix_operational_events_request_time
                ON operational_events(request_id, timestamp_ms);
            CREATE INDEX IF NOT EXISTS ix_operational_events_job_time
                ON operational_events(job_type, job_id, timestamp_ms);
            CREATE TABLE IF NOT EXISTS operational_event_source_state (
                source_key TEXT PRIMARY KEY,
                dropped_records INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            );
            """
        )
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(operational_events)")
        }
        if "stage" not in columns:
            connection.execute("ALTER TABLE operational_events ADD COLUMN stage TEXT")

    def emit(self, record: logging.LogRecord) -> None:
        if getattr(record, "event", None) == "cloudwatch_emf":
            return
        try:
            rendered = self.format(record)
            payload = sanitize_operational_payload(json.loads(rendered))
            if payload is None:
                self._pending_dropped += 1
                return
            now = datetime.now(timezone.utc).isoformat()
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                columns = (
                    "event_id",
                    "timestamp",
                    "timestamp_ms",
                    "level",
                    "source",
                    "service",
                    "environment",
                    "logger",
                    "event",
                    "description",
                    "error_code",
                    "error_category",
                    "exception_type",
                    "error_signature",
                    "source_location",
                    "operation_id",
                    "parent_operation_id",
                    "request_id",
                    "job_id",
                    "job_type",
                    "job_status",
                    "attempt_number",
                    "stage",
                    "failed_stage",
                    "user_id",
                    "course_id",
                    "document_id",
                    "http_method",
                    "http_path",
                    "http_status",
                    "duration_ms",
                    "generation_type",
                    "provider",
                    "model",
                    "success",
                    "payload_json",
                    "ingested_at",
                )
                values = [payload.get(column) for column in columns[:-2]]
                values.extend((json.dumps(payload, separators=(",", ":")), now))
                connection.execute(
                    f"INSERT OR IGNORE INTO operational_events ({','.join(columns)}) "
                    f"VALUES ({','.join('?' for _ in columns)})",
                    values,
                )
                if self._pending_dropped:
                    connection.execute(
                        """
                        INSERT INTO operational_event_source_state
                            (source_key, dropped_records, updated_at)
                        VALUES (?, ?, ?)
                        ON CONFLICT(source_key) DO UPDATE SET
                            dropped_records = dropped_records + excluded.dropped_records,
                            updated_at = excluded.updated_at
                        """,
                        (self.source_key, self._pending_dropped, now),
                    )
                    self._pending_dropped = 0
                if time.monotonic() - self._last_cleanup >= 600:
                    self._cleanup(connection)
                    self._last_cleanup = time.monotonic()
                connection.commit()
        except Exception:
            self._pending_dropped += 1

    def _cleanup(self, connection: sqlite3.Connection) -> None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=self.retention_days)
        connection.execute(
            "DELETE FROM operational_events WHERE sequence IN "
            "(SELECT sequence FROM operational_events WHERE timestamp_ms < ? "
            "ORDER BY sequence LIMIT 1000)",
            (int(cutoff.timestamp() * 1000),),
        )
        count = connection.execute(
            "SELECT count(*) FROM operational_events"
        ).fetchone()[0]
        excess = max(0, count - self.max_records)
        if excess:
            connection.execute(
                "DELETE FROM operational_events WHERE sequence IN "
                "(SELECT sequence FROM operational_events ORDER BY sequence LIMIT ?)",
                (min(excess, 1000),),
            )
