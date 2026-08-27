# Database and document processing

Lumina uses SQLAlchemy models as the schema contract and Alembic as the only
runtime schema-management mechanism. The API never calls
`Base.metadata.create_all`; that helper is reserved for isolated tests.

## Apply migrations

Set `DEPLOYMENT_MODE` and `DATABASE_URL`, then run migrations before starting
the API or worker:

Lumina does not load `.env` files itself. Export variables in the process
environment or use a launcher such as Docker Compose that injects them.

```bash
python -m alembic upgrade head
python -m alembic current --check-heads
python -m alembic check
```

CI also exercises both supported dialects through a disposable migration
lifecycle: fresh upgrade, head and drift checks, one-revision downgrade and
re-upgrade, full downgrade to base, and final re-upgrade. Production migrator
containers run `upgrade head`, `current --check-heads`, and `check`; a service
never starts after a partial migration or schema drift.

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

Finish course deletions that a storage or vector-store failure left unfinished.
It is idempotent, so rerunning it is always safe:

```bash
python -m workers.course_purge
python -m workers.course_purge --dry-run
python -m workers.course_purge --course-id 42
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
unsupported for self-hosted mode because SQLite/local storage/Chroma are not a
qualified multi-writer topology. AWS hosted mode uses PostgreSQL, RDS Proxy,
S3, and pgvector and supports multiple API and worker tasks.

File-backed SQLite connections use WAL mode, `synchronous=FULL`, a five-second
busy timeout, a 1,000-page automatic checkpoint, and a 64 MiB retained-WAL cap
after successful checkpoints. Long-running readers can delay checkpoints and
exceed that cap. Do not copy `lumina.db` or its `-wal` file directly. The
supported online SQLite snapshot and coordinated upload/Chroma procedure is in
[`self-hosted-backup.md`](self-hosted-backup.md).

## PostgreSQL qualification

CI runs the relational database contract against PostgreSQL 17.8 from an
immutable `pgvector/pgvector` image digest; the pgvector extension is required
because the schema declares a `vector` column and an HNSW index. The live job
verifies the complete Alembic upgrade/downgrade/re-upgrade cycle, schema drift,
role seeds, readiness, UUID and timezone round trips, unloaded database cascades
across all 23 tables, pgvector provisioning and cosine ranking, and
`SKIP LOCKED` worker claims. Tests marked `database_contract` run unchanged
against copies of an Alembic-migrated SQLite database and the disposable
PostgreSQL `lumina_ci` database. The PostgreSQL fixture refuses any other
database name, truncates only model tables between cases, and preserves the
Alembic revision.

This qualification covers the hosted production database path. Hosted
production additionally requires S3-compatible document storage
(`STORAGE_BACKEND=s3`); see `docs/deployment.md` for the hosted topology.

AWS API and worker tasks use TLS-only RDS Proxy. Each process has an explicit
SQLAlchemy pool budget (`DATABASE_POOL_SIZE`, `DATABASE_MAX_OVERFLOW`, and
`DATABASE_POOL_RECYCLE_SECONDS`); RDS Proxy then limits database-side
connections to a configured share of the instance. The migrator bypasses the
proxy with a separate direct URL so DDL and schema locks never contend with
runtime pooling. RDS parameters log queries slower than one second, bound idle
transactions, and set conservative work-memory/autovacuum defaults; tune them
only from observed production plans and memory pressure.

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

The AWS worker service scales on `OldestQueuedAgeSeconds`. Every claim commits
before extraction starts, so workers never hold queue locks during OCR,
embedding, or storage calls. A 120-second task stop timeout allows graceful
completion; if scale-in kills a longer attempt, lease recovery and claim-token
fencing make the retry safe. RDS Proxy and the per-process SQLAlchemy pool
budget must be sized before increasing replica maxima.

Workers recover expired leases periodically, not only at startup. Chunk
replacement, embedding storage, document completion, and job completion commit
atomically. Course deletion fences queued and running claims before storage
cleanup.

Processing stages advance `validating -> extracting_text -> running_ocr ->
understanding_images -> cleaning_text -> chunking -> generating_embeddings`. The
last stage runs in the worker parent process and stores one `chunk_embeddings`
row (or one Chroma record) per chunk in the same transaction that publishes the
chunks, so a document reaches `ready` only when its vectors exist. An embedding
failure leaves the previous chunks in place and either requeues the job or fails
it permanently, according to the classified error code. See
`docs/vector_storage.md`.

## Course ownership and authorization

Every course resource is owner-scoped. `Course.owner_id` is not merely data: it
is the authorization rule, and `utils/authorization.py` is the single boundary
that enforces it. Any new course-scoped endpoint must depend on it rather than
trust the `course_id` a client sends.

The module exposes three modes. `require_course_access` authorizes reads,
`require_course_owner` authorizes writes, and `require_course_deletion`
authorizes deletion of a course that may already be tombstoned, which keeps
deletion retryable after a storage or vector-store failure. Each loads a course with the policy
applied inside the query and returns the authorized course, so an endpoint never
repeats the lookup or reuses the untrusted identifier.

Ownership comes from the verified token. `POST /api/courses/` ignores any owner
supplied in the request body.

| Caller | List | Read course and documents | Write course, documents, generation | Delete course |
| --- | --- | --- | --- | --- |
| Unauthenticated | `401` | `401` | `401` | `401` |
| Owner | own courses | own courses | own courses | own courses |
| Other user | not listed | `404` | `404` | `404` |
| Administrator | all courses | any course | own courses only, else `404` | own courses only, else `404` |

The administrator override is deliberately read-only. Administrators may inspect
any course for support and administration, but may not modify, delete, upload
to, retry, or generate material against a course they do not own. Deleting a
course therefore needs no administrator involvement and admits none.

### Course workspace fields

A course workspace carries `title`, `description`, `semester`, `exam_date`,
`topics`, `syllabus`, `subject_area`, `education_level`, `is_archived`, `created_at` and
`updated_at`. `syllabus` is nullable free
text for the course outline; it is distinct from `topics`, which the study
features consume. `updated_at` is
maintained by the ORM through `onupdate`, so any course modification advances it
while `created_at` stays fixed.

`exam_date` is a nullable `DATE`. The database validates it and `ORDER BY
exam_date` is chronological, so course listing sorts by exam date with undated
courses last rather than by insertion order. The API accepts and returns an ISO
`YYYY-MM-DD` string; an empty string is read as absent so clearing the field in
a form does not fail validation.

Migration `e2b7c94f1a03` converted the former `String(20)` column. **A legacy
value converts only if it matches `^\d{4}-\d{2}-\d{2}$` and parses as a real
date.** The regular expression is checked first on purpose: CPython 3.11 widened
`date.fromisoformat` to accept forms such as `20260904`, and the conversion rule
must not depend on which interpreter runs the migration. Every other value --
the empty string, a bare year such as `2026`, a partial date such as `2026-09`,
an impossible date such as `2026-02-30`, and free text -- became NULL and was
logged as `course id=<id> exam_date=<original> discarded: not an ISO date`.
**The remediation for a discarded value is to re-enter that date from the
migration log**; the migration deliberately did not coerce `2026` into
`2026-01-01`, because that shows a student an exam date they never entered.

`topics` is not a column. Topic labels are rows in `course_topics`
(`course_id`, `position`, `name`), so a topic containing a comma keeps its
boundaries and a course-scoped topic lookup is an ordinary join rather than a
substring match. `position` preserves the order the student wrote and is not
unique, because the course form replaces the whole set. Uniqueness is
`(course_id, name)` rather than a functional index on `lower(name)` so the
constraint is identical on SQLite and PostgreSQL; case-insensitive
de-duplication happens in the service layer, keeping the first casing. A course
holds at most 50 topics of at most 100 characters each. Deleting a course
cascades its topics.

The API carries `topics` as a JSON array of strings on read and write, and the
frontend edits it with the `TagInput` primitive. Neither side splits or joins on
a comma. Migration `f3c8d05a2b16` performed that split one final time when it
backfilled the legacy column.

`is_archived` is a boolean flag indicating whether the course is archived by its owner.
Archiving is non-destructive and reversible: archived courses remain fully accessible for reads,
settings updates, restoration, and permanent deletion, but are hidden from the primary active course
list. Unlike permanent deletion, archiving preserves all associated documents, embeddings, quizzes,
attempts, and progress.

`education_level` is a `String(20)` constrained by a CHECK to `high_school`,
`undergraduate`, `graduate`, `professional_other`, or `unspecified`, and it
defaults to `unspecified` at the database level so a course written before the
column existed reads back as neutral rather than as a guess. `users` carries the
same column as a fallback, and `uploaded_documents.material_kind` records what
kind of material a document is; `mixed` is rejected there because it is only ever
a resolved aggregate across several documents. These three columns exist to feed
the shared prompt variables described in `docs/prompt_templates.md`; nothing else
reads them.

`subject_area` is nullable and is never inferred from the title.

`owner_id` is immutable. It is absent from both the create and update payloads,
so neither an owner nor an administrator can transfer a workspace through the
API.

### Course conversations

Course Q&A and AI Tutor persist successful exchanges in the shared
`conversations` and `conversation_messages` tables. `conversation_type` is
mandatory and checked as either `course_qa` or `ai_tutor`, so one feature cannot
continue the other feature's thread. Rows written before Tutor persistence were
introduced are backfilled as `course_qa` by the migration that adds the type.

Generation continuation scopes a conversation identifier to the current user,
authorized course, and expected type in one query. Missing, cross-course,
cross-user, and cross-type identifiers all return `404 Conversation not found`.
Only a successful provider response appends the user and assistant pair; a
retrieval or generation failure leaves the thread unchanged.

The read-only history API lists course conversations newest-first and returns
one detail with chronological messages:

```text
GET /api/courses/{course_id}/conversations
GET /api/courses/{course_id}/conversations/{conversation_id}
```

These endpoints use `require_course_access`, so owners read their own course
history and administrators retain the standard support read override. The
detail lookup includes the parent course in the same query, making an identifier
from another course indistinguishable from a missing conversation.

Unauthorized access never discloses existence. A nonexistent course, a course
whose purge has not finished, and another owner's course all return `404` with the same
`Course not found` body, so course identifiers cannot be enumerated. Documents
are additionally scoped to their authorized course, so a document identifier
from one course cannot be reached through another.

### Course identifier strategy

Course resources use sequential integer identifiers (`courses.id` auto-increment
primary keys). This is an explicit, accepted design decision:

- **Security boundary is authorization, not ID obscurity**: Access control is
  strictly enforced by `utils/authorization.py` on every course-scoped request.
  Possession or guessing of a course integer ID confers no access.
- **Uniform 404 responses prevent enumeration**: Missing, deleted, or unowned
  courses all return an identical `404 Course not found` response, preventing
  attackers from determining whether an ID exists or belongs to another user.
- **Opaque IDs not required**: Because ownership validation occurs inside the
  database query before any course-scoped logic executes, replacing sequential
  integers with UUIDs is unnecessary and not planned.

## Processing API

Authenticated owners can inspect, retry, and delete course-scoped documents:

```text
GET  /api/courses/{course_id}/documents/{document_id}
POST /api/courses/{course_id}/documents/{document_id}/retry
DELETE /api/courses/{course_id}/documents/{document_id}
```

Retry returns `202` only for a failed job and resets its attempt history. Other
states return `409`. Course/document mismatches, deleted courses, and courses
the caller does not own return `404`. Authorization runs before the endpoint
body, so a denied request creates no document row, storage object, or processing
job, and removes nothing. Responses expose job progress and safe error codes, but never
tokens, worker identities, storage keys, or lease internals.

Deletion returns `409` while the durable job is queued or running. Terminal
failed or ready documents are first tombstoned, then their source, chunks,
embeddings, and processing job are removed. Vectors are removed while the
document is still tombstoned, so a failure there is retryable and never leaves
deleted content searchable. Storage or database failures retain the tombstone
so the same deletion request can safely resume cleanup. Matching uploads return
`409` while deletion is in progress.

## Document upload validation contract

Validation of uploaded documents is intentionally partitioned into synchronous
request-time admission and asynchronous worker-time deep validation:

### Synchronous request-time validation

The API endpoint (`POST /api/courses/{course_id}/documents`) performs fast,
lightweight, non-parsing checks (`services/document_validation.py`) to safely
admit files without CPU-expensive inspection:

| Condition | Status | Error Code | Description |
| --- | --- | --- | --- |
| Unsupported extension | `415` | `UPLOAD_UNSUPPORTED_FILE_TYPE` | Not `.pdf`, `.txt`, `.md`, or `.markdown` |
| Invalid / long filename | `422` | `UPLOAD_INVALID_FILE_NAME` | Name > 255 chars or contains NUL bytes |
| File exceeds limit | `413` | `UPLOAD_FILE_TOO_LARGE` | Stream exceeds `MAX_UPLOAD_SIZE_BYTES` |
| Empty file | `422` | `UPLOAD_EMPTY_FILE` | 0-byte upload |
| Missing file part | `422` | `UPLOAD_DOCUMENT_REQUIRED` | Multipart body missing document field |
| Malformed multipart | `400` | `UPLOAD_INVALID_MULTIPART` | Invalid multipart payload |
| Storage quota reached | `409` | `UPLOAD_COURSE_DOCUMENT_LIMIT` | Course storage limit reached |
| Deletion in progress | `409` | `UPLOAD_DOCUMENT_DELETION_IN_PROGRESS` | Matching document being deleted |
| Registration failure | `500` | `UPLOAD_FAILED` | Internal registration or hash error |

### Asynchronous worker-time deep validation

Once admitted and enqueued, deep document inspection runs inside the worker
process (`services/document_pipeline.py`). Content-level errors are recorded on
the `processing_jobs` row and surfaced via document status polling as `failed`:

| Condition | Failed Stage | Error Code | Description |
| --- | --- | --- | --- |
| Corrupted / invalid PDF | `validating` | `CORRUPTED_PDF` | PDF syntax error or broken stream |
| Encrypted PDF | `validating` | `PASSWORD_PROTECTED_PDF` | Password-protected PDF |
| PDF complexity exceeded | `validating` | `DOCUMENT_TOO_COMPLEX` | Page count, pixel, or stream limit exceeded |
| Corrupted / binary text | `validating` | `CORRUPTED_TEXT` | Non-decodable binary data in text file |
| Text size limit exceeded | `extracting_text` | `EXTRACTED_TEXT_LIMIT_EXCEEDED` | Total extracted text exceeds limit |
| Chunk count exceeded | `chunking` | `DOCUMENT_CHUNK_LIMIT_EXCEEDED` | Generated chunks exceed limit |
| Empty extracted content | `cleaning_text` | `NO_PROCESSABLE_TEXT` | No readable text found |

This architectural separation keeps the API admission path fast and bounded,
preventing slow or maliciously crafted documents from consuming API worker
threads or causing denial-of-service.

## Course deletion

Deleting a course is unconditional and permanent. `DELETE /api/courses/{course_id}`
takes no mode parameter and there is no soft delete: the course row, its
documents and their stored files, pages, visuals, chunks, vectors, generated
outputs, quizzes, questions, attempts, progress, processing jobs, and AI usage
logs are all removed. Profile knowledge belongs to the student rather than the
course and is never touched, and neither is the account itself.

Only the owner may do it. An administrator may read any course but gets the same
`404` as any other non-owner when deleting one they do not own.

The order is deliberate and each step is idempotent:

1. Tombstone the course and fence its queued and running jobs, in one committed
   transaction, so no worker can write new artifacts underneath the deletion.
2. Delete every stored file.
3. Delete every vector.
4. Delete the course row, which cascades the relational graph.

`courses.is_deleted` is that tombstone and means one thing: purge pending. It is
internal state, not a trash bin — `CourseUpdate` does not carry it, so no client
can set it. A tombstoned course is already invisible to reads, and its owner can
still delete it, which is what makes the operation resumable.

A storage or vector-store failure stops before the row is deleted and answers
`500`. The tombstone and the metadata naming the remaining objects both survive,
so the same request can safely resume. Repeating a step that already succeeded is
harmless: a missing file deletes cleanly and an empty vector set is a no-op.
Deleting vectors before the row, rather than after, is what keeps a failure from
leaving deleted material semantically searchable.

`python -m workers.course_purge` finishes deletions nobody retried, including
courses soft-deleted under the older behavior. It reruns this same path against
every tombstoned course, so it is idempotent, and a course it cannot finish is
logged and left tombstoned rather than blocking the rest.

## Quizzes

A quiz belongs to one course, carries the attribution of the generation that
produced it, and owns an ordered list of questions.

`quizzes` records `user_id`, `model_used`, `generation_settings`, and
`generation_context` the same way `generated_outputs` does for study guides:
the requesting user, the model that actually produced the row, the options that
were asked for, and what retrieval actually returned. The two JSON documents are
written strictly through their Pydantic models and read back permissively, so
one bad row can never fail a list or detail read. `user_id` is `ON DELETE SET
NULL` because a quiz outlives the account that generated it; the course cascade
still removes it with its course.

Generation also writes a `generated_outputs` row of `output_type` `quiz`, so a
course's generation history is one list regardless of feature. Across all generation
features (`study_guide`, `quiz`, `flashcards`), `GeneratedOutputService.record` is the
enforced canonical single writer for `generated_outputs` rows. Feature services and routes
delegate persistence to this entrypoint and never instantiate `GeneratedOutput` models directly.


### Question types

`quiz_questions.question_type` is explicit rather than inferred from whether
`options` happens to be present. The four MVP types are `multiple_choice`,
`true_false`, `short_answer`, and `open_ended`. Each question also carries its
own `difficulty`, `topic`, and `explanation`.

`correct_answer` is the authoritative answer, stored as a JSON document
discriminated by `type`:

| Question type | Stored `correct_answer` | `options` | `correct_option_index` |
| --- | --- | --- | --- |
| `multiple_choice` | `{"type": "multiple_choice", "option_index": 1}` | four choices | mirrored |
| `true_false` | `{"type": "true_false", "value": true}` | `["True", "False"]` | mirrored |
| `short_answer` | `{"type": "short_answer", "text": "...", "accepted_answers": [...]}` | `NULL` | `NULL` |
| `open_ended` | `{"type": "open_ended", "reference_answer": "..."}` | `NULL` | `NULL` |

`options` and `correct_option_index` stay populated for the two option-based
types. They are a mirror, not a second source of truth: grading and the frontend
may read either, and revision `c8d4a1f39e72` backfilled the document for every
question that predates it. Both columns are nullable because a short-answer or
open-ended question genuinely has neither.

`UNIQUE(quiz_id, question_index)` is what makes question order a property of the
data rather than of insertion order. Reads sort by `question_index` with the
identifier as a tie-breaker, so a quiz presents its questions the way the model
generated them no matter how the rows were written.

### Generation is atomic

The provider's whole response is validated, and checked against the settings
that asked for it, before any row is written. Question count, allowed question
types, and difficulty are all enforced after generation rather than trusted to
prompt compliance. The quiz and every one of its questions are then written in a
single transaction, so a malformed response or a failed insert leaves no quiz
and no partial questions behind.

### Attempts and grading

`quiz_attempt_answers` records `selected_option_index` for option-based
questions and `answer_text` for written ones, plus `is_correct`, `score`, and
`feedback`.

Multiple-choice and true/false grade by option index. Short answers are matched
against the stored accepted variants after normalizing case, punctuation, and
whitespace. Open-ended answers are scored by the text-generation provider
against the stored reference answer, which costs one credit per attempt that
contains at least one of them.

`is_correct` and `score` are both nullable, and are null together, for one
reason: an open-ended answer the grader could not score. Such an answer is
recorded ungraded rather than wrong, is excluded from the attempt score's
denominator, and is skipped by topic mastery. Losing a student's written work
because a grading model timed out would be a much worse outcome than an unscored
answer, so a grading failure never fails the attempt.

## Credit ledger

`credit_transactions` makes every credit balance change attributable. For any
account with a non-null balance, that balance equals the sum of the account's
deltas; an account with a null balance is unmetered and owns no rows.

The table carries two constraints that enforce behavior rather than describe
shape. `UNIQUE (user_id, grant_period)` is the idempotency key for the lazy
monthly grant, so an account receives at most one grant per calendar month
however many requests race. `UNIQUE (refunds_transaction_id)` makes a charge
refundable at most once, so a failure handler that runs twice cannot mint
credit. Both rely on NULL comparing as distinct, which SQLite and PostgreSQL
agree on, so the many rows carrying neither value never collide.

`user_id` cascades on delete, because a deleted account's ledger has no subject.
`actor_user_id` uses `SET NULL` instead: deleting an administrator must not erase
the record of grants they made, so `actor_label` keeps their email as a snapshot.

The ledger is append-only. `services/credits.py` offers no update or delete path,
and corrections are recorded as new, opposing transactions.

Full policy, reasons, and the migration backfill rule are in `docs/credits.md`.

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

## Profile knowledge and student context

`profile_knowledge` stores structured student-owned knowledge topics and details
(`topic`, `detail`, `created_at`, `updated_at`) tied directly to `user_id`.

### Lifecycle and deletion semantics

Profile knowledge is user-scoped rather than course-scoped:
- **Course deletion**: Deleting a course removes only course-bound documents,
  chunks, embeddings, and generated outputs. It leaves all `profile_knowledge` rows intact.
- **User deletion**: Deleting a user cascades and permanently removes all associated
  profile knowledge records (`ondelete="CASCADE"`).
- **Cross-user privacy**: Profile knowledge entries are strictly isolated to the owning
  user. Reading, updating, or deleting another user's knowledge item returns `404 Not Found`
  to prevent identifier enumeration.

### Retrieval priority rules

When assembling context for course-scoped AI features:
1. **Consent is explicit and off by default**: Profile knowledge is read only when the
   request opts in through `use_profile_knowledge`. When the opt-in is absent or false, no
   `profile_knowledge` query is issued at all.
2. **Course material is primary and authoritative**: Extracted document chunks for the
   target course are loaded first up to the configured per-feature character budget. If no
   ready course material is available, generation fails with `NoReadyCourseMaterialError`.
   Profile knowledge is never a substitute for missing course material.
3. **Profile knowledge is supplementary**: Eligible profile knowledge entries for the
   authenticated user are loaded up to their own separate character budget and appended as
   supplementary student background context. An entry carrying neither a topic nor a detail
   is not eligible and is counted in neither `items_available` nor `items_used`. The course
   and profile budgets are independent, so profile knowledge can never displace course
   material; a profile that exceeds its budget is truncated within that budget alone.
4. **Precedence under conflict**: If course material and profile knowledge contain
   conflicting statements, course material is authoritative.
5. **Isolation**: A user's profile knowledge is never exposed to or included in another
   user's generation context.
6. **Recorded use**: The persisted `generation_context` document reports what actually
   reached the provider (`profile_knowledge_used`, `profile_knowledge_items_used`,
   `profile_knowledge_characters_used`, `profile_knowledge_truncated`). The request's
   intent is recorded separately as `use_profile_knowledge` in `generation_settings`, so an
   opted-in request by a user holding no entries records requested-but-unused.

### Scope and document upload deferral decision

- **MVP Scope**: In Lumina MVP, profile knowledge operates exclusively through structured
  text entries (`topic` and `detail`) created and managed by the student. These provide
  deterministic, low-overhead background context for prompt personalization across courses.
- **Post-MVP Deferral**: Full profile-level document and file upload (requiring user-scoped
  file storage, per-user document extraction/OCR pipelines, user-isolated chunking, and
  cross-course personal semantic vector search) is **explicitly deferred to post-MVP**.
- **UI and API Boundaries**: Course-scoped documents (`POST /api/courses/{course_id}/documents`)
  remain the sole file ingestion pipeline in MVP. The user profile UI and API endpoints
  do not provide or imply document upload capabilities for user profiles.

