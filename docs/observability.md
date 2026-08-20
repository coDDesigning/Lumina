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

## Worker metrics

The worker emits CloudWatch Embedded Metric Format events under
`Lumina/Worker`, dimensioned by `Service=worker` and `Environment`:

| Metric | Unit | Statistic |
| --- | --- | --- |
| `QueuedJobs`, `RunningJobs`, `FailedJobs` | Count | Maximum |
| `OldestQueuedAgeSeconds` | Seconds | Maximum |
| `RecoveredJobs`, `JobsRetried`, `JobsFailed`, `JobsSucceeded` | Count | Sum |
| `ProcessingDurationMs` | Milliseconds | p95/Average |

Each worker reports the same queue snapshot, so dashboards and alarms use
`Maximum`, never `Sum`, for queue gauges. Outcome metrics are event counters and
use `Sum`.

## Alarms

The Terraform `observability` module sends both ALARM and OK transitions to
`<prefix>-alarms`. Set `alarm_email` to create an email subscription, then
confirm it through SNS. The baseline alarms cover:

- ALB target 5xx, response latency, and unhealthy targets;
- API CPU and missing worker tasks;
- RDS CPU, free memory, and free storage;
- RDS Proxy session pinning;
- oldest queued-job age; and
- permanently failed document jobs.

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
