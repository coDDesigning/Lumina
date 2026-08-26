# Operational Runbook: Stuck Document Processing

## Overview

A document processing job transitions through `queued -> running -> succeeded` (or terminal `failed`). A job is considered "stuck" when:
1. It remains in `running` state past its lease expiration (`lease_expires_at < CURRENT_TIMESTAMP`) because a worker crashed or was killed without finalizing.
2. It has exceeded its maximum attempts (`attempt_count >= max_attempts`) and is marked `failed`.
3. It repeatedly times out during OCR or heavy PDF extraction exceeding `PROCESSING_JOB_ATTEMPT_TIMEOUT_SECONDS`.

---

## 1. Diagnosis

### Symptoms & Alerts
* CloudWatch alarm: `OldestQueuedAgeSeconds > 300` or `QueuedJobs` increasing without corresponding `JobsSucceeded`.
* API document list (`GET /api/courses/{id}/documents`) shows `status="processing"` or `status="failed"`.
* Document processing error codes in `processing_jobs.last_error_code` (e.g. `PROCESSING_TIMEOUT`, `EXTRACTION_FAILED`, `EXTRACTION_PERSISTENCE_FAILED`).

### Diagnostic Queries

#### CloudWatch Logs Insights (Hosted):
```sql
fields @timestamp, request_id, job_id, worker_id, event, message, error_code
| filter ispresent(job_id) or ispresent(request_id)
| filter level = "ERROR" or event in ["job_failed", "job_retry"]
| sort @timestamp desc
| limit 50
```

#### SQL Diagnostics (Hosted RDS PostgreSQL / Self-Hosted SQLite):
```sql
-- Identify running jobs with expired leases
SELECT id, document_id, course_id, attempt_count, max_attempts,
       lease_owner, lease_expires_at, correlation_id, processing_stage
FROM processing_jobs
WHERE status = 'running'
  AND lease_expires_at < CURRENT_TIMESTAMP;

-- Identify terminally failed jobs
SELECT id, document_id, course_id, attempt_count, max_attempts,
       last_error_code, last_error_message, failed_stage, correlation_id
FROM processing_jobs
WHERE status = 'failed'
ORDER BY updated_at DESC
LIMIT 20;
```

---

## 2. Safe Remediation

### Self-Hosted Environment

1. **Check worker container status:**
   ```bash
   docker compose ps worker
   docker compose logs --tail=100 worker
   ```

2. **Recover lease automatically:**
   The worker loop periodically runs `recover_expired_jobs()`. If the worker is running, expired leases are automatically returned to `queued` state (or marked `failed` if `attempt_count >= max_attempts`).

3. **Restart stalled worker container:**
   ```bash
   docker compose restart worker
   ```

4. **Re-process one-off job manually:**
   ```bash
   docker compose run --rm worker python -m workers.document_processor --once
   ```

5. **Re-index document embeddings if chunks are stored but missing vectors:**
   ```bash
   docker compose run --rm worker python -m workers.embedding_backfill --document-id <DOCUMENT_UUID>
   ```
   *Note*: The background worker automatically scans and backfills missing vectors every `EMBEDDING_BACKFILL_INTERVAL_SECONDS` (default: 3600 seconds / 1 hour).

### Hosted Environment (AWS ECS / RDS / S3)

1. **Verify ECS Worker Service Health:**
   ```bash
   aws ecs describe-services \
     --cluster <CLUSTER_NAME> \
     --services <WORKER_SERVICE_NAME> \
     --query 'services[0].{runningCount:runningCount,desiredCount:desiredCount}'
   ```

2. **Check Worker Task Logs by Correlation ID / Job ID:**
   ```bash
   aws logs filter-log-events \
     --log-group-name /ecs/<NAME_PREFIX> \
     --filter-pattern '{ $.job_id = <JOB_ID> }'
   ```

3. **Force a New Worker Deployment (if tasks are hung on unkillable zombies):**
   ```bash
   aws ecs update-service \
     --cluster <CLUSTER_NAME> \
     --service <WORKER_SERVICE_NAME> \
     --force-new-deployment
   ```

4. **Run On-Demand Embedding Backfill for a Stuck Course/Document:**
   ```bash
   aws ecs run-task \
     --cluster <CLUSTER_NAME> \
     --task-definition <WORKER_TASK_DEF> \
     --overrides '{"containerOverrides": [{"name": "worker", "command": ["python", "-m", "workers.embedding_backfill", "--document-id", "<DOCUMENT_UUID>"]}]}'
   ```

---

## 3. Validation

1. **Verify Document State in Database:**
   ```sql
   SELECT id, status, processing_error FROM uploaded_documents WHERE id = '<DOCUMENT_UUID>';
   ```
   Expected: `status = 'ready'` and `processing_error IS NULL`.

2. **Verify Chunk Vectors:**
   ```sql
   SELECT COUNT(*) FROM document_chunks WHERE document_id = '<DOCUMENT_UUID>';
   SELECT COUNT(*) FROM chunk_embeddings WHERE document_id = '<DOCUMENT_UUID>';
   ```
   Expected: Chunk count equals embedding count.

3. **Verify API Read:**
   Call `GET /api/courses/{course_id}/documents/{document_id}` and ensure it returns status `ready`.

---

## 4. Escalation Criteria

* **Corrupted or Malformed PDF:** If a specific document crashes PyMuPDF consistently with `WorkerProcessFatalError` across retries, notify the user that the uploaded file format is corrupted or unsupported.
* **Disk/Storage Full:** If `UPLOAD_DIRECTORY` or local `/tmp` is at 100% capacity, extraction fails immediately. Escalate to infrastructure on-call to expand storage volumes.
* **Vector Store Unreachable:** If PostgreSQL pgvector or local Chroma rejects connections (`VectorStoreError`), escalate to Database on-call.
