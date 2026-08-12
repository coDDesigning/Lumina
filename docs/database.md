# Database and document processing

Lumina uses SQLAlchemy models as the schema contract and Alembic as the only
runtime schema-management mechanism. The API never calls
`Base.metadata.create_all`; that helper is reserved for isolated tests.

## Apply migrations

Set `DEPLOYMENT_MODE` and `DATABASE_URL`, then run migrations before starting
the API or worker:

```bash
python -m alembic upgrade head
python -m alembic current --check-heads
python -m alembic check
```

The processing-job and processing-stage revisions are additive children of the
canonical SCRUM-30 revision. During upgrade they create one extraction job for
every existing document and migrate the public document states:

| Existing document | Backfilled job |
| --- | --- |
| `pending` | `uploaded` with a `queued` job |
| `processing` | `uploaded` with a `queued` job |
| `completed` with chunks | `ready` with a `succeeded` job |
| `completed` without chunks | `uploaded` with a `queued` job |
| `failed` | `failed` |

Documents that still need processing under an already deleted course are
backfilled as failed instead of being left permanently unclaimable.

Downgrading the revision removes processing jobs and chunk page metadata. It
does not restore a legacy `processing` state because the original worker lease
cannot be reconstructed safely.

## Run the API and worker

The API registers documents and enqueues extraction in one database
transaction. Extraction runs in a separate process:

```bash
uvicorn main:app --host 0.0.0.0 --port 8000
python -m workers.document_processor
```

Process at most one available job for smoke testing:

```bash
python -m workers.document_processor --once
```

Check database migrations, required role seeds, and writable document storage
without recovering or claiming a job:

```bash
python -m workers.document_processor --check
```

The worker performs this readiness check before entering its processing loop and
exits nonzero if a dependency is not ready. `--check` and `--once` are mutually
exclusive; unlike `--check`, `--once` may recover and process durable jobs.

The worker handles `SIGTERM` and `SIGINT` as drain requests. It stops claiming
new jobs and normally finishes and persists the active attempt before exiting.
The process uses one stable worker identity for its lifetime so lease logs can be
correlated across jobs. Database finalization failures leave the lease for safe
recovery, while unrecoverable child-process failures exit nonzero for supervisor
restart.

For normal operation, set the supervisor termination grace to at least
`PROCESSING_JOB_ATTEMPT_TIMEOUT_SECONDS + 45` seconds. With the defaults this is
345 seconds. This is a minimum baseline, not a deadline for uninterruptible
storage or operating-system I/O. A forced kill before draining completes leaves
the lease to expire; recovery is safe, but the interrupted attempt still counts
toward `PROCESSING_JOB_MAX_ATTEMPTS`.

The API and worker must use the same `STORAGE_BACKEND`, `STORAGE_NAMESPACE`,
and storage contents. With the current local provider, multiple hosts require a
shared mounted directory at `UPLOAD_DIRECTORY`.

## Durable state machine

Each document has one `extract_document` job.

```text
queued -> running -> succeeded
   ^         |
   |         +-> queued (retryable failure or expired lease)
   |         +-> failed (permanent failure or attempts exhausted)
   +------------- manual retry from failed
```

Document status is projected from the job:

| Job | Document |
| --- | --- |
| `queued` | `uploaded` |
| `running` | `processing` |
| `succeeded` | `ready` |
| `failed` | `failed` |

Claims are short database transactions. SQLite acquires `BEGIN IMMEDIATE` before
choosing work; PostgreSQL uses `FOR UPDATE OF processing_jobs SKIP LOCKED`.
Every running claim receives a unique token and expiring lease. Heartbeat,
completion, and failure transitions require the current token. A stale worker
therefore cannot overwrite a reclaimed attempt.

Workers recover expired leases periodically, not only at startup. Chunk
replacement, document completion, and job completion commit atomically. Course
deletion fences queued and running claims before storage cleanup.

## Processing API

Authenticated clients can inspect, retry, and delete course-scoped documents:

```text
GET  /api/courses/{course_id}/documents/{document_id}
POST /api/courses/{course_id}/documents/{document_id}/retry
DELETE /api/courses/{course_id}/documents/{document_id}
```

Retry returns `202` only for a failed job and resets its attempt history. Other
states return `409`. Course/document mismatches and deleted courses return
`404`. Responses expose job progress and safe error codes, but never claim
tokens, worker identities, storage keys, or lease internals.

Deletion returns `409` while the durable job is queued or running. Terminal
failed or ready documents are first tombstoned, then their source, chunks, and
processing job are removed. Storage or database failures retain the tombstone
so the same deletion request can safely resume cleanup. Matching uploads return
`409` while deletion is in progress.

## Limits and failure behavior

Worker behavior is configured through:

- `PROCESSING_JOB_LEASE_SECONDS`
- `PROCESSING_JOB_MAX_ATTEMPTS`
- `PROCESSING_JOB_POLL_SECONDS`
- `PROCESSING_JOB_ATTEMPT_TIMEOUT_SECONDS`
- `MAX_EXTRACTED_CHARACTERS`
- `MAX_DOCUMENT_CHUNKS`
- `OCR_LANGUAGE`
- `OCR_DPI`
- `OCR_MIN_TEXT_CHARACTERS`
- `DOCUMENT_CHUNK_SIZE_CHARACTERS`
- `DOCUMENT_CHUNK_OVERLAP_CHARACTERS`

The worker verifies the stored byte count and SHA-256 digest before extraction.
It also reuses upload page and byte limits, bounds extracted text and chunk
count, and stores only curated public errors. Extraction runs in a killable
subprocess with a hard per-attempt timeout. Text-poor PDF pages are recognized
with local Tesseract OCR. Any existing searchable text is retained alongside
recognized text. Install every language selected by `OCR_LANGUAGE`; the default
container installs English (`eng`). Image understanding is not enabled.
