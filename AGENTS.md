# AGENTS.md - Lumina

Operating instructions for coding agents in this repository.

## Working Rules

- Act as a senior engineer: precise, minimal-diff, and evidence-based. Do not add teaching commentary.
- Inspect `git status --short --branch`, the current diff, and the relevant executable config before editing. Task branches may lag `dev`.
- The checked-out repository overrides this file. If they disagree, state the discrepancy and follow the repository.
- Preserve unrelated dirty changes. Never reset, clean, or rewrite work you did not create.
- Verify commands before claiming success. Never invent files, endpoints, checks, or passing results.

## Current Architecture

- Python is pinned to 3.12. Backend code uses FastAPI, Pydantic 2, and SQLAlchemy 2 typed declarative models.
- `backend/app/config.py` owns application settings. `backend/app/database_config.py` reads the database-only subset for Alembic. `.env` is not loaded automatically; launchers must inject configuration. Do not add environment reads elsewhere. Settings are frozen and import-cached.
- `DEPLOYMENT_MODE=self_hosted` defaults to SQLite and local paths. Hosted staging requires PostgreSQL and is exercised by a pinned live CI service; hosted production is qualified only with `STORAGE_BACKEND=s3` plus PostgreSQL, and fails at startup otherwise.
- `backend/app/database.py` creates SQLite parent directories outside production and enables `PRAGMA foreign_keys=ON` for every connection. Do not remove the listener; SQLite cascades depend on it.
- `backend/app/models.py` defines the 21-table relational model. `DocumentChunk.course_id` and `DocumentPage.course_id` are intentional denormalization for course-scoped reads. `ChunkEmbedding` denormalizes `document_id`, `course_id`, and `chunk_index` the same way, held true by a composite foreign key into `document_chunks`.
- Keep DB `ondelete="CASCADE"` and ORM `cascade="all, delete-orphan", passive_deletes=True` together. `User -> Role` deliberately does not cascade.
- Root `routes/`, `schemas/`, `services/`, and `utils/` belong to the FastAPI layer. Do not move them under `backend/app/` without an explicit team decision.
- User, course, document, and processing services persist through SQLAlchemy sessions; do not reintroduce process-local stores.
- `main:app` includes auth, course, admin, user, profile knowledge, document, study guide, generated output, quiz, flashcard, AI tutor, course Q&A, conversation, and prompt generator routers.
- `services/credits.py` is the only module that may modify `users.credits`. Every balance change updates the balance and appends one `credit_transactions` row in the same transaction, so a metered balance always equals the sum of its deltas; the ledger is append-only and corrections are new rows. All six charge sites (study guide, quiz, flashcard, AI tutor, course Q&A, prompt generator) go through `CreditService.charge`/`refund`, and a refund carries a unique `refunds_transaction_id` so a charge can be reversed at most once. `GENERATION_CREDIT_COSTS` is the authoritative price table and is served to clients rather than hardcoded by them; a quiz containing open-ended questions costs 2 because it prepays the AI grading of every attempt, which is why quiz grading charges nothing and can never be skipped for want of credit. A null balance means unmetered, which is how administrators work and what `CREDIT_METERING_ENABLED=false` extends to a whole self-hosted deployment; exemption is never faked with a large number. The monthly grant is lazy rather than scheduled, evaluated when a balance is charged or read and made idempotent by a unique `(user_id, grant_period)`. Administrators move a balance through the single `POST /api/admin/users/{email}/credits`, which takes a signed `delta` and a mandatory `reason` of `admin_grant`, `support_compensation`, or `admin_adjustment`; there is no separate grant or adjust route, no set-balance operation, and a manual change is deliberately unbounded. Those two mechanisms are the only ways out of a zero balance, and a client must offer no other, because there is no purchase path. `GET /api/users/me/credits` is the balance a user interface reads: it alone evaluates the lazy grant, and it returns the policy that explains the balance, reporting nulls when the account is unmetered. A refusal is identified by the `X-Error-Code` header that every AI route now sends beside `detail`, not by the bare status. See `docs/credits.md`.
- `IMPLEMENTED_AI_PROVIDERS` in `backend/app/config.py` is the authoritative provider list, and a test asserts `get_text_generation_provider` constructs every name in it. `openai` and `claude` are recognized but unimplemented and fail at startup for both `AI_PROVIDER` and `AI_FALLBACK_PROVIDERS`, never at request time. `get_text_generation_provider` returns a `ReliableTextGenerationProvider` wrapping the primary and fallbacks; concrete providers return plain text or a JSON object and feature services own prompt construction, schema validation, and persistence. See `docs/ai_providers.md`.
- Course resources are owner-scoped. `utils/authorization.py` is the only course authorization boundary; every course-scoped endpoint must depend on `require_course_access`, `require_course_owner`, or `require_course_deletion` instead of trusting a client `course_id`. Unauthorized or missing courses return `404`, never `403`. Administrators may read any course but write only their own. See `docs/database.md`.
- Document upload validates bytes and writes generated, content-derived keys. Never derive storage keys from client filenames. `storage/base.py` defines the synchronous `Storage` contract plus the canonical portable key generator and key validator shared by both providers. `storage/local.py` stores under `UPLOAD_DIRECTORY`; `storage/s3.py` stores in an S3-compatible bucket via boto3 with an injectable client (tests fake it; the hosted Compose is the live qualification). `storage/dependencies.py` builds the provider from settings; the readiness probe exercises `check_ready()` on the configured provider.
- Alembic is the only runtime schema-management mechanism. The canonical chain is `97d9fd86a3ba -> b6d8f2a4c901 -> d2a7f0c91e35 -> c4e6a8f1b203 -> f7a3c9d2e541 -> a8c4e2f7b913 -> a4fd52f56b91 -> b7e2a9d1c3f4 -> e5c1a7b39d64 -> a1c5e7f9b203 -> c9b3d5e08f27 -> d3f8b21a6c40 -> e4a7b1c90d52 -> f4b18c7a2e60 -> 910e2719d549 -> 2a7c4e9f8b10 -> 7b3e1a9c4d28 -> b2f47c8d0915 -> c8e1f5a9b3d2 -> c8d4a1f39e72 -> f5a7c2d9e104 -> d7f3a2c48e15 -> b9c1d4e7f2a6 -> a3d9e5c17b48 -> e7c1d4a8b203`; add schema changes as descendants and keep one base/head unless an explicit migration design requires otherwise.
- Every AI prompt is a versioned JSON template under `app/prompts/`; no prompt text lives in Python. `services/prompt_loader.py` loads them and `schemas/prompt_template.py` renders them, rejecting an undeclared `{{PLACEHOLDER}}` at load time and an unsupplied one before the provider call. Both checks scan the template body, never the rendered output, because a placeholder appearing inside a variable's value must stay inert; that inertness is the anti-injection guarantee and services render user text and course material last to preserve it. Every template declares `EDUCATION_LEVEL`, `COURSE_TITLE`, `SUBJECT_AREA`, and `MATERIAL_KIND` ahead of its own variables, and `services/prompt_context.py` is the only place that resolves them: course value, then profile value, then a neutral `unspecified` that never stands in for undergraduate. `prompt_generator` is not course-scoped and deliberately gets a user-scoped context with neutral course fields. See `docs/prompt_templates.md`.
- `frontend/` is a React 19 + TypeScript + Vite application with its own npm lockfile and commands.
- Chunk embeddings and course-isolated semantic retrieval are implemented. `services/vector_store.py` owns durable vector storage behind one interface with two backends chosen by `VECTOR_BACKEND`: pgvector on PostgreSQL, ChromaDB at `CHROMA_PERSIST_DIRECTORY` on SQLite. `services/semantic_retrieval.py` ranks the chunks of one course against a query embedding and resolves them back to chunk text; the course scope is mandatory and never optional. Study guide, quiz, flashcard, AI tutor, and course Q&A read retrieved material through `services/retrieval_material.py`, which applies the `RETRIEVAL_MIN_SIMILARITY` floor above the ranking seam, spends the character budget in similarity order, and emits in corpus order. It never falls back to whole-corpus assembly: a retrieval failure fails the request. Ranking that returns nothing at all is reported as `MaterialNotIndexedError`, separately from a relevance miss, because a course with ready chunks and no vectors is fixed by `python -m workers.embedding_backfill` rather than by a broader topic. `services/retrieval_query.py` turns each request's topic focus or current student question into the query the feature ranks against. See `docs/vector_storage.md`.
- Embeddings are generated in the worker parent process and written inside `complete_job`'s transaction, after the chunk flush and before `ready`. A document must never reach `ready` without one current vector per current chunk. `services/embeddings.py` owns the `EmbeddingProvider` seam, which is separate from `TextGenerationProvider`; `IMPLEMENTED_EMBEDDING_PROVIDERS` is authoritative and unimplemented providers fail at startup. `services/document_embedding.py` owns the failure classification the job state machine consumes.
- `python -m workers.embedding_backfill` reconciles vectors for chunks that predate indexing. It is idempotent and safe to rerun; `--prune-orphans` removes vectors whose chunk is gone.
- Course deletion is unconditional, permanent, and owner-only; there is no soft delete and administrators cannot delete another owner's course. `CourseService.hard_delete_course` is the only deletion path: it tombstones and fences, then removes storage, then vectors, then the row. `courses.is_deleted` means purge pending and is not client-settable. `python -m workers.course_purge` finishes deletions a storage or vector-store failure left unfinished; it is idempotent, `--course-id` scopes it, and `--dry-run` reports without deleting. See `docs/database.md`.
- `services/course_material.py` is the whole-corpus path, now used by the profile-knowledge assembly helper. It bounds the assembled material to a caller-supplied character budget and reports truncation; the ordering and the budget are the contract its tests assert. It is deliberately dumb selection being replaced feature by feature by `services/retrieval_material.py`, so keep authorization, provider calls, validation, and persistence out of both. Neither module reads settings: the calling feature supplies every bound.
- Course Q&A and AI Tutor persist separate typed conversations through `services/conversation.py`. Generation continuation requires the conversation id, current user, course, and type to match; list/detail reads are course-scoped and follow the normal administrator read override. Existing pre-type rows are truthfully backfilled as `course_qa`. Only successful user/assistant pairs are stored, in chronological message order.
- Study guide responses report `retrieval_narrowed` (retrieval chose a subset of the course, the normal case) separately from `context_truncated` (the character budget dropped a chunk retrieval had already selected). Do not collapse them: before retrieval `context_truncated` meant `chunks_used < chunks_available`, which now holds on nearly every request.
- Quiz generation validates the provider's whole response, and checks it against the requested count, allowed question types, and difficulty, before writing anything; the quiz and its questions are then written in one transaction. `quiz_questions.correct_answer` is the authoritative answer document and `options`/`correct_option_index` are a mirror kept populated for the two option-based types. Attempt grading scores multiple choice and true/false by option index, short answers by normalized match against stored accepted variants, and open-ended answers through the provider; an answer the provider could not score is recorded ungraded rather than wrong and never fails the attempt. See `docs/database.md`.
- AI routes must not return exception text. `utils/ai_errors.py` owns the exception-to-response mapping; every public message is a constant and the stable category is logged, never sent.
- `generated_outputs.user_id` and `generated_outputs.model_used` record the requesting user and the model that actually produced the row. `generation_settings` and `generation_context` record the requested options and what retrieval actually produced, as versioned JSON documents written strictly through their Pydantic models and read back permissively so one bad row can never fail a history read. All four are nullable only because legacy rows have no truthful value; they are never backfilled, and every new study guide and quiz row must populate them. `GeneratedOutputService.record` is the single writer, so a feature cannot store attribution its own way.
- `GET /api/courses/{id}/generated-outputs` and `.../generated-outputs/{output_id}` are reads, so administrators may read another owner's history but still cannot generate into their course. An output is always looked up scoped to its parent course, and reading never calls an AI provider.
- The root `Dockerfile` and `docker-compose.yml` are the supported single-host self-hosted container path. They run one-shot migrations before separate API and worker services as UID/GID 10001 on a shared named volume. `docker-compose.hosted.yml` is the hosted topology: pinned pgvector and MinIO services, a one-shot `minio-init` bucket provisioner, and the same migrate/API/worker roles wired to S3 storage and pgvector. Hosted production config requires `STORAGE_BACKEND=s3`; MinIO itself is gated by the API and worker readiness probes because the MinIO image has no shell.
- AWS runtime database traffic goes through TLS-only RDS Proxy; the one-shot migrator uses a separate direct RDS URL so schema locks and DDL never traverse the runtime pool. Keep both Secrets Manager ARNs wired to the ECS execution role.
- Production application logs use `backend/app/observability.py`: one privacy-safe JSON event per line with request correlation, plus CloudWatch EMF worker metrics. Do not add raw prompts, uploaded content, credentials, query strings, or arbitrary exception text to logs. See `docs/observability.md`.
- AWS hosted API and worker tasks may scale horizontally. Worker scaling uses `OldestQueuedAgeSeconds`; PostgreSQL claims must retain `SKIP LOCKED`, short claim transactions, expiring leases, and claim-token fencing. Never qualify multi-host SQLite/local/Chroma. See `docs/deployment.md`.

## Dependencies

- Direct dependencies live in `requirements.in` and `requirements-dev.in`; generated, hash-locked files are `requirements.txt` and `requirements-dev.txt`.
- Development and CI install `requirements-dev.txt`; it already includes runtime dependencies.
- Never edit generated locks by hand or replace them with `pip freeze`. Regenerate both with `uv==0.12.1` using the commands in `docs/dependencies.md`, run each compile twice, and require no second-run diff.
- `httpx2` is intentional for Starlette 1.3 `TestClient`; it is the Pydantic-maintained HTTPX successor, not a typo.

```bash
python -m venv .venv
source .venv/Scripts/activate
python -m pip install --require-hashes --only-binary=:all: -r requirements-dev.txt
```

## Verification

Mirror `.github/workflows/ci.yml`; it is the executable source of truth.

```bash
python -m pip check
python -m ruff check --no-cache .
python -m ruff format --check .
git ls-files '*.py' | xargs -r python -m py_compile
python -m pytest -q -p no:cacheprovider tests

cd frontend
npm ci --no-audit --no-fund
npm run lint
npm test
npm run build
```

- Run one test with `python -m pytest -q tests/test_document_upload.py::test_name`.
- Frontend lint is deliberately scoped to `src` and `vite.config.ts`; do not replace it with `eslint .`, which scans generated/dependency trees.
- CI also imports `backend.app.models`, builds `main.app` OpenAPI, qualifies the relational contract on PostgreSQL 17.8 with pgvector, and requires tests/builds to leave tracked files unchanged.

## Git And PRs

- Branch flow is `main <- dev <- task branch`. Never commit or push directly to `main` or `dev`.
- From a clean worktree, start a task with `git switch dev && git pull --ff-only origin dev && git switch -c <type>/SCRUM-XX-description`.
- Branches must match `^(feature|fix|chore|docs)/SCRUM-[0-9]+-[a-z0-9][a-z0-9._-]*$`.
- Update a task branch with `git fetch origin && git merge origin/dev`; never rebase or force-push.
- Use `type(scope): message` commits with type `feat`, `fix`, `docs`, `chore`, `test`, `refactor`, `ci`, or `style`. Keep formatting-only changes in a separate `style:` commit.
- A commit message is the subject line and nothing else. Do not add a body, description, rationale, or trailers, and never add `Co-Authored-By`, `Generated with`, or any other tool or agent attribution to commits or PRs. Explanation belongs in the PR description, not in git history.
- Stage intended paths explicitly. Do not use `git add .` except for an audited formatting-only sweep.
- PRs target `dev`; title them `SCRUM-XX: Description` and follow `.github/pull_request_template.md` exactly.
- PR `How to test` must list exact commands and expected results. Prefer `gh pr create --base dev ...` and `gh pr checks --watch`.
- Squash-merge with message `SCRUM-XX: description`, then delete the task branch.

## CI Contract

- Current `dev` job names are `Branch and PR policy`, `Repository quality`, `Backend quality and tests`, `Migration governance`, `PostgreSQL quality`, `Container quality`, and `Frontend quality and build`.
- Job names are referenced by dormant rulesets. Flag the corresponding ruleset update before renaming one.
- CI must verify, never modify: no `--fix`, generated changes, or auto-commits. Keep `permissions: contents: read`.
- Third-party Actions must remain pinned to immutable 40-character commit SHAs.
- Do not add checks for components absent from the checked-out repository.

## Security And Safety

- Never write or log secrets. Commit placeholders only in `.env.example`; add every new configuration variable there in the same PR.
- Passwords persist only as `password_hash`. Logs may identify filenames but must not print uploaded contents or credentials.
- Future AWS authentication must use GitHub OIDC/IAM roles, not stored long-lived keys.
- Ask before destructive operations. The sole pre-approved reset is `rm -rf data`, which removes only ignored runtime state.
- Use Windows Git Bash-compatible commands; the venv activation path is `.venv/Scripts/activate` (`.venv/bin/activate` on Linux).
