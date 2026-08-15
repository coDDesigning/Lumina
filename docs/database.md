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

Exactly one deployment-owned process may apply migrations. API and worker
entrypoints must never migrate or stamp the database. In the supported
self-hosted container topology, the one-shot `migrate` service completes before
Compose starts either runtime role; see [`deployment.md`](deployment.md).

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
uvicorn main:app --host 0.0.0.0 --port 8000 --limit-concurrency 100 --timeout-graceful-shutdown 330
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

The API and worker must use the same `DATABASE_URL`, `STORAGE_BACKEND`,
`STORAGE_NAMESPACE`, and storage contents. The supported self-hosted Compose
topology runs on one host and shares one named volume. Multiple hosts remain
unsupported because no qualified durable shared storage topology exists.

## PostgreSQL qualification

CI runs the relational database contract against PostgreSQL 17.6 from an
immutable official image digest. The live job verifies the complete Alembic
upgrade/downgrade/re-upgrade cycle, schema drift, role seeds, readiness, UUID and
timezone round trips, loaded ORM/database cascades, and `SKIP LOCKED` worker
claims.

This qualification does not enable hosted production. Hosted staging still uses
local document storage, and production remains blocked until API and worker
processes have qualified durable shared storage and deployment topology.

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
subprocess with a hard per-attempt timeout.

PDF, TXT, and Markdown files first produce a common raw extraction result. PDF
content is retained per physical page with one-based page numbers; TXT and
Markdown produce one content unit with a null page number. Decoded Markdown
source is retained before cleaning so headings, code fences, indentation, and
hard line breaks remain available to later stages.

The worker claim-fences an atomic raw checkpoint in `document_pages` before OCR,
visual analysis, cleaning, or chunking continues. Successful completion then
atomically replaces that checkpoint with enriched pages and final chunks.
`raw_text` and `raw_extraction_method` retain SCRUM-36 provenance while
`extraction_method` and `ocr_status` identify the primary native, decoded, or
OCR source. After cleaning, `text` is the canonical page-level representation:
clean primary text followed by labeled successful visual descriptions. A
later-stage failure leaves the raw checkpoint available for diagnosis and
retry.

Chunking operates on the ordered cleaned document stream. The configured size
is a character target rather than a hard maximum: paragraph, line, and word
boundaries can shorten a base range, while overlap can extend a stored chunk up
to the target plus the configured overlap. Newly processed PDF chunks store
inclusive starting and ending page numbers; TXT and Markdown chunks keep both
page fields null. Legacy chunks without source-page metadata retain null ranges
until they are reprocessed.

PDF pages are OCR candidates when they have less searchable text than
`OCR_MIN_TEXT_CHARACTERS` and contain a meaningful embedded image, detected
table, or vector-drawing region. Blank pages and repeated small decorative
images are skipped. Candidate pages are recognized individually with local
Tesseract while native text from other pages remains authoritative. Install
every language selected by `OCR_LANGUAGE`; the default container installs
English (`eng`). An OCR engine error fails the attempt; a successful OCR call
that finds no text is recorded as `no_text`, allowing configured visual analysis
to recover semantic content before the final no-processable-text check.

Meaningful images, tables, and drawing clusters are retained as ordered
`document_visuals` rows with page-relative point coordinates, coarse type,
source, status, and an optional description. Selection is bounded per page and
document, repeated small images are filtered, and regions are rendered and
released one at a time. Cleaning normalizes Unicode and format-specific
whitespace, conservatively repairs PDF wrapping, and removes high-confidence
repeated PDF edge content without crossing page boundaries. Successful visual
descriptions remain structured and are also appended to `document_pages.text`
with labels such as `[Diagram]`; repeated or exact duplicate descriptions are
omitted from merged text without deleting their provenance rows. The production
visual provider is currently disabled;
disabled analysis is explicitly recorded as `not_configured`. The provider
contract and deterministic test providers qualify non-fatal per-visual failures
and retryable temporary service failures without selecting a production AI
backend.
