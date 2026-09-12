# Observability

Lumina emits one JSON object per application log line. Hosted ECS transports
stdout and stderr to `/ecs/<project>-<environment>` in CloudWatch Logs; the
application does not write local log files there. Self-hosted deployments can
also index the same sanitized events in a bounded SQLite store for investigation
through the application. Terraform retains the hosted group for 30 days and
enables ECS Container Insights, RDS PostgreSQL logs, a CloudWatch dashboard, an
SNS alarm topic, and production alarms.

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
it carries neither correlation nor sanitisation; the middleware records the
request events described under [Which requests are logged](#which-requests-are-logged).

### Which requests are logged

A request event is recorded when it says something an operator could act on:

- every response at 5xx, whatever the path;
- every rejection: 429, 401/403, 404, and every other 4xx;
- every request slower than the two-second threshold; and
- every successful mutation (`POST`, `PUT`, `PATCH`, `DELETE`).

A successful read is not logged, and neither is the interface itself: the shell,
static assets, `/health/live`, `/health/ready` and `/ads.txt` are silent unless
they answer 5xx, which is always logged.

This is a correctness property of the local store rather than a preference.
`OperationalEventHandler._cleanup` deletes at most a thousand rows per
ten-minute pass, so an instance logging one row per request, per polled document
status, and per asset of every page load outruns its own retention: the window
`/admin/logs` can show shrinks until it no longer covers the incident being
investigated. The events that survive are the ones that carry a finding.

`http_path` is the matched route template, never a concrete URL, and it carries
four sentinels for the paths routing never claimed: `/spa` for a shell load,
`/static` for a file in the build output, `/api/unmatched` for an unknown API
path, and `/unmatched` for anything else. `auth_state` says whether the caller
was `authenticated`, `rejected` (credentials were offered and refused), or
`anonymous`. `user_id` is bound only on success, so `user_id` present means
authenticated.

Every rejection carries a machine-readable `X-Error-Code`, which the middleware
records as `error_code` and the interface renders as specific copy. The code is
set in the exception constructor (`utils/exceptions.py`), so a refusal raised
anywhere is typed by default; the log falls back to a per-status code so
`error_code` is never null.

Taking a reported 4xx to the account and route that produced it:
```sql
fields @timestamp, event, http_path, http_status, error_code, user_id, request_id
| filter http_status >= 400 and user_id = 4711
| sort @timestamp desc
```

AI usage rows remain privacy-safe product telemetry in PostgreSQL/SQLite; they
are not operational logs. A telemetry write uses a nested transaction so a
failed best-effort flush cannot poison the caller's transaction.

## Admin investigation center

Administrators can investigate the sanitized records at `/admin/logs`. The
page and every endpoint behind it require the administrator role. It supports
bounded UTC ranges, cursor pagination, source capability and health reporting,
aggregate error groups, operation timelines, shareable URL filters, and CSV or
JSONL exports. An unavailable source is reported as unavailable; it is never
silently presented as an empty result. Exports contain at most 10,000 records
and carry explicit truncation and partial-result metadata.

The read model combines three source names without merging their storage:

- `operational` contains server and worker events;
- `client_report` contains sanitized browser failures emitted into the
  operational stream; and
- `ai_telemetry` projects privacy-safe `ai_usage_logs` rows.

Hosted API tasks can read only the fixed
`OPERATIONAL_LOG_CLOUDWATCH_GROUP` configured by Terraform. The browser cannot
select a group and never receives AWS credentials. Self-hosted deployments use
`OPERATIONAL_LOG_PATH`; Compose mounts `/data/operational-logs.db` on the shared
application volume. `OPERATIONAL_LOG_RETENTION_DAYS` and
`OPERATIONAL_LOG_MAX_RECORDS` bound the local index. Cleanup is incremental and
best effort so logging cannot fail application work.

The admin API is:

- `GET /api/admin/logs` for records and source health;
- `GET /api/admin/logs/summary` for counts, distribution, and error groups;
- `GET /api/admin/logs/events/{event_id}` for one sanitized record;
- `GET /api/admin/logs/trace?event_id=...` for its operation lineage; and
- `GET /api/admin/logs/export?format=jsonl|csv` for bounded exports.

Queries are limited to 30 days. A page defaults to 50 records and cannot exceed
200. An unsupported filter on an AI-only query is rejected. In a combined query,
a source that cannot apply every active filter is explicitly excluded and makes
the result partial; the filter is never silently ignored.

Authenticated browsers send unhandled failures to `POST /api/client-errors`.
The payload contains only a fixed route template, build version, error class,
optional related API request ID, and a derived fingerprint. It does not contain
the exception message, stack, query string, dynamic route values, component
props, browser storage, or study content. The client deduplicates bursts and
the server applies a per-user fingerprint rate limit before logging. Hosted
releases compile the commit release ID into `VITE_APP_VERSION`; local builds use
`development`.

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

`request_id` identifies an HTTP exchange. `operation_id` identifies one logical
execution, while `parent_operation_id` links durable background work back to the
operation that created it. The admin timeline follows both persisted job links
and emitted parent links rather than assuming a caller-supplied request ID is
globally unique.

Correlation IDs trace execution end-to-end from the initial HTTP request through background job processing and subprocess extraction:

1. **API Ingress:** When an API request arrives, the `observe_request` middleware binds `_REQUEST_ID` (preserving a valid `X-Request-ID` header or generating a UUID4 hex string), creates a server-owned `api:<uuid>` operation, and returns `X-Request-ID` in the response headers.
2. **Job Enqueue:** When a job is enqueued, the active request ID and API operation are stored as durable correlation and parent-operation metadata.
3. **Worker Claim:** When a worker claims the job, it binds a stable job operation such as `generation_job:<type>:<id>` together with the stored parent operation and request correlation.
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
`course_id`, `document_id`, `owner_id`, `user_id`, `auth_state`, `response_bytes`,
`failed_stage` and `runbook` are part of the emitted JSON because they are on the `_ALLOWED_FIELDS` allowlist in
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
