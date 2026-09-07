# Observability

Lumina emits one JSON object per application log line. ECS transports stdout
and stderr to `/ecs/<project>-<environment>` in CloudWatch Logs; the application
does not write local log files. Terraform retains the group for 30 days and
enables ECS Container Insights, RDS PostgreSQL logs, a CloudWatch dashboard,
an SNS alarm topic, and production alarms.

## Log contract

Every line contains:

- UTC `timestamp`, `level`, `service`, `environment`, and `logger`;
- a stable `event` identifier and redacted `message`;
- `request_id` when processing HTTP work; and
- bounded operational fields such as `http_method`, `http_path`,
  `http_status`, `duration_ms`, `job_id`, `worker_id`, `error_code`, and
  `exception_type`; and
- `stack` on records logged at ERROR with an exception: project-relative frames
  (`services/quiz.py:524 in generate`) taken from the innermost cause, capped at
  twelve. Never a rendered traceback, a source line, or an exception message.

The API accepts a safe `X-Request-ID` (1-64 letters, digits, dots, dashes, or
underscores), generates one otherwise, and returns it on the response. Query
strings, request bodies, uploaded content, prompts, credentials, and raw
exception text are not structured fields. Model output is described but never
quoted: a failed generation reports its size, digest, sanitised top-level key
names and the fields validation rejected, and the response text itself appears
only when an operator turns on `AI_LOG_RAW_RESPONSE_ON_FAILURE`. See
[Diagnosing an unusable AI response](#diagnosing-an-unusable-ai-response). Known
token/password/API-key forms are redacted from messages. Uvicorn access logging is disabled because
the middleware already records one correlated request event.

AI usage rows remain privacy-safe product telemetry in PostgreSQL/SQLite; they
are not operational logs. A telemetry write uses a nested transaction so a
failed best-effort flush cannot poison the caller's transaction.

## Worker and service metrics

Lumina emits CloudWatch Embedded Metric Format events under `Lumina/Worker` and `Lumina/AI`:

### Document worker (`Lumina/Worker`, `Service=worker`, `Environment`)

| Metric | Unit | Statistic | Description / Dimensions |
| --- | --- | --- | --- |
| `QueuedJobs`, `RunningJobs`, `FailedJobs` | Count | Maximum | Queue gauges from periodic recovery snapshot |
| `OldestQueuedAgeSeconds` | Seconds | Maximum | Oldest queued job age in seconds |
| `RecoveredJobs`, `JobsRetried`, `JobsFailed`, `JobsSucceeded` | Count | Sum | Job lifecycle outcome event counters |
| `StageFailed`, `StageRetried` | Count | Sum | Per-stage failures, dimensioned by `Stage` |
| `ProcessingDurationMs` | Milliseconds | p95/Average | End-to-end extraction and embedding duration |

### AI provider health (`Lumina/AI`, `Service=api`, `Environment`)

| Metric | Unit | Statistic | Description / Dimensions |
| --- | --- | --- | --- |
| `ProviderCalls` | Count | Sum | Total AI generation calls, dimensioned by `Provider` |
| `ProviderLatencyMs` | Milliseconds | p95/Average | Provider response latency in milliseconds |
| `ProviderErrors` | Count | Sum | Failed AI generation calls, dimensioned by `Provider` and `ErrorCategory` |

### Course purge and maintenance (`Lumina/Worker`, `Service=course_purge`, `Environment`)

| Metric | Unit | Statistic | Description / Dimensions |
| --- | --- | --- | --- |
| `CoursesExamined`, `CoursesPurged`, `CoursesFailed` | Count | Sum | Course tombstone purge execution counts |
| `AgedTombstones` | Count | Maximum | Number of tombstones exceeding the purge threshold |
| `OldestTombstoneAgeSeconds` | Seconds | Maximum | Oldest unpurged course tombstone age in seconds |

### Document purge (`Lumina/Worker`, `Service=document_purge`, `Environment`)

| Metric | Unit | Statistic | Description / Dimensions |
| --- | --- | --- | --- |
| `DocumentsExamined`, `DocumentsPurged`, `DocumentsFailed` | Count | Sum | Document tombstone purge execution counts |
| `AgedDocumentTombstones` | Count | Maximum | Number of document tombstones exceeding the purge threshold |
| `OldestDocumentTombstoneAgeSeconds` | Seconds | Maximum | Oldest unpurged document tombstone age in seconds |

Each worker reports the same queue snapshot, so dashboards and alarms use
`Maximum`, never `Sum`, for queue gauges. Outcome metrics are event counters and
use `Sum`. AWS Application Auto Scaling uses `OldestQueuedAgeSeconds` with
`Maximum` to add/remove worker tasks; CPU is deliberately not the worker signal
because extraction and provider calls are frequently I/O-bound.

## Alarms

The Terraform `observability` module sends both ALARM and OK transitions to
`<prefix>-alarms`. Set `alarm_email` to create an email subscription, then
confirm it through SNS. The baseline alarms cover:

- ALB target 5xx, response latency, and unhealthy targets;
- API CPU and missing worker tasks;
- RDS CPU, free memory, and free storage;
- RDS Proxy session pinning;
- oldest queued-job age;
- permanently failed document jobs; and
- aged course tombstones (`AgedTombstones >= 1`).

Thresholds are conservative starting values. Change them from observed
production baselines and record the reason in review; do not disable missing
worker/queue telemetry alarms to hide an outage.

## Alarm exercise

Before production launch and after alarm changes, run a staging exercise:

1. Confirm the SNS subscription and dashboard are visible.
2. Send enough controlled failing requests to cross the ALB 5xx threshold.
3. Pause the staging worker long enough to cross the queue-age threshold.
4. Confirm each alarm transitions `OK -> ALARM` and reaches the recipient.
5. Restore the worker and successful traffic; confirm `ALARM -> OK`.
6. Record timestamps, alarm names, and observed recovery time without request or
   study content.

Readiness remains a dependency probe, not a substitute for metrics. Liveness is
used only to recycle an unresponsive process.

## Correlation ID lifecycle and query workflows

Correlation IDs trace execution end-to-end from the initial HTTP request through background job processing and subprocess extraction:

1. **API Ingress:** When an API request arrives, the `observe_request` middleware binds `_REQUEST_ID` (preserving a valid `X-Request-ID` header or generating a UUID4 hex string) and returns `X-Request-ID` in the response headers.
2. **Job Enqueue:** When an extraction job is enqueued (`services.processing_jobs.enqueue_document_job`), the active `request_id` is durably stored in `processing_jobs.correlation_id`.
3. **Worker Claim:** When a worker claims the job (`claim_next_job`), `ClaimedJob.correlation_id` is bound to the worker's logging context (`bind_request_id`).
4. **Subprocess Isolation:** The `spawn` extraction subprocess re-applies `configure_logging(service="worker", ...)` as its first action and then binds the `correlation_id` passed across the `multiprocessing` boundary, so OCR, image-understanding, and chunking logs are the same one-JSON-object-per-line records — traceback-free, redacted, carrying `request_id` — as the parent. Without that call the child would fall back to `logging.lastResort` and emit raw tracebacks with no correlation.
5. **Maintenance Logging:** In-process reconciliation (`workers.document_processor._maintenance_cycle`: course purge, embedding backfill, AI-usage retention cleanup) logs through the worker's own `configure_logging`. Standalone maintenance scripts (`workers.course_purge`, `workers.embedding_backfill`, `workers.ai_usage_cleanup`, `workers.self_hosted_backup`) format logs using `configure_logging(service="maintenance", ...)`.

### Querying an End-to-End Trace

#### CloudWatch Logs Insights (Hosted ECS):
```sql
fields @timestamp, service, event, message, http_status, duration_ms, error_code
| filter request_id = "<CORRELATION_ID>" or job_id = <JOB_ID>
| sort @timestamp asc
```

#### Pivoting from an operator alert to its runbook:
Alerts carry the scope and the remediation pointer as structured fields, so an
alert can be taken straight to the procedure that resolves it:
```sql
fields @timestamp, event, error_code, failed_stage, course_id, document_id, runbook
| filter event = "permanent_document_failure" or event = "aged_tombstone_detected"
| sort @timestamp desc
```
`course_id`, `document_id`, `owner_id`, `user_id`, `failed_stage` and `runbook` are
part of the emitted JSON because they are on the `_ALLOWED_FIELDS` allowlist in
`backend/app/observability.py`. A field set through `extra=` but absent from that
tuple is dropped by `JsonFormatter` and never reaches CloudWatch, so adding a new
structured field means adding it there and to the allowlist pin in
`tests/test_privacy_telemetry.py`. The pin asserts the whole tuple, so growing it
is a reviewed privacy decision rather than a side effect.

#### Diagnosing an unusable AI response:
A generation that the provider answered but the schema rejected emits one
`ai_generation_failed` line from `services/ai_usage_logger.py`:
```sql
fields @timestamp, generation_type, provider, model, error_category,
       ai_response_type, ai_response_bytes, ai_response_keys, ai_validation_errors
| filter event = "ai_generation_failed"
| sort @timestamp desc
```
`ai_response_keys` is usually the fastest read: `["error","message"]` means the
provider reported a fault in a 200, `["data"]` means it wrapped the payload, and
a schema-shaped key list means the model drifted inside a field --
`ai_validation_errors` then names which one. `ai_response_sha256` distinguishes
one repeated broken response, which is a prompt bug, from a different one each
time, which is model instability. Key names and pydantic locations are model
output, so `utils/ai_diagnostics.py` emits them only when they look like schema
field names and masks anything else as `*`.

One failure can produce three correlated lines: this one, an `ERROR` from
`utils/ai_errors.py` carrying `stack`, and the middleware's request line. Join
them on `request_id`.

`AI_LOG_RAW_RESPONSE_ON_FAILURE=true` adds `ai_response_excerpt`, a truncated and
redacted copy of the response itself. It is study content: turn it on to
diagnose, and off again afterwards.

#### Local / Self-Hosted (Docker Compose):
```bash
docker compose logs lumina lumina-worker | grep '<CORRELATION_ID>' | jq .
```

## Operational Runbooks

For remediation procedures during operational incidents, see:
* [Stuck Document Processing](runbooks/stuck_document.md)
* [Stranded Tombstone Course Purge](runbooks/stranded_tombstone.md)
* [AI Provider Outage & Degradation](runbooks/provider_outage.md)
* [Self-Hosted Backup & Restore](self-hosted-backup.md)