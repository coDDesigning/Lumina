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
  `exception_type`.

The API accepts a safe `X-Request-ID` (1-64 letters, digits, dots, dashes, or
underscores), generates one otherwise, and returns it on the response. Query
strings, request bodies, uploaded content, prompts, model output, credentials,
and raw exception text are not structured fields. Known token/password/API-key
forms are redacted from messages. Uvicorn access logging is disabled because
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
4. **Subprocess Isolation:** The extraction subprocess inherits `correlation_id` explicitly across the `multiprocessing` boundary, ensuring OCR, image understanding, and chunking logs preserve the triggering request ID.
5. **Maintenance Logging:** Maintenance scripts (`workers.course_purge`, `workers.embedding_backfill`, `workers.self_hosted_backup`) format logs using `configure_logging(service="maintenance", ...)`.

### Querying an End-to-End Trace

#### CloudWatch Logs Insights (Hosted ECS):
```sql
fields @timestamp, service, event, message, http_status, duration_ms, error_code
| filter request_id = "<CORRELATION_ID>" or job_id = <JOB_ID>
| sort @timestamp asc
```

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