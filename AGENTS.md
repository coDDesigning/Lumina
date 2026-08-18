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
- `DEPLOYMENT_MODE=self_hosted` defaults to SQLite and local paths. Hosted staging requires PostgreSQL and is exercised by a pinned live CI service; hosted production remains blocked because shared storage, pgvector, and S3 adapters are not implemented.
- `backend/app/database.py` creates SQLite parent directories outside production and enables `PRAGMA foreign_keys=ON` for every connection. Do not remove the listener; SQLite cascades depend on it.
- `backend/app/models.py` defines the 15-table relational model. `DocumentChunk.course_id` and `DocumentPage.course_id` are intentional denormalization for course-scoped reads.
- Keep DB `ondelete="CASCADE"` and ORM `cascade="all, delete-orphan", passive_deletes=True` together. `User -> Role` deliberately does not cascade.
- Root `routes/`, `schemas/`, `services/`, and `utils/` belong to the FastAPI layer. Do not move them under `backend/app/` without an explicit team decision.
- User, course, document, and processing services persist through SQLAlchemy sessions; do not reintroduce process-local stores.
- `main:app` includes auth, course, admin, user, document, study guide, quiz, flashcard, and prompt generator routers.
- `IMPLEMENTED_AI_PROVIDERS` in `backend/app/config.py` is the authoritative provider list, and a test asserts `get_text_generation_provider` constructs every name in it. `openai` and `claude` are recognized but unimplemented and fail at startup for both `AI_PROVIDER` and `AI_FALLBACK_PROVIDERS`, never at request time. `get_text_generation_provider` returns a `ReliableTextGenerationProvider` wrapping the primary and fallbacks; concrete providers return plain text or a JSON object and feature services own prompt construction, schema validation, and persistence. See `docs/ai_providers.md`.
- Course resources are owner-scoped. `utils/authorization.py` is the only course authorization boundary; every course-scoped endpoint must depend on `require_course_access`, `require_course_owner`, or `require_course_deletion` instead of trusting a client `course_id`. Unauthorized or missing courses return `404`, never `403`. Administrators may read any course but write only their own. See `docs/database.md`.
- Document upload validates bytes and writes generated, content-derived paths. Never derive storage paths from client filenames.
- Alembic is the only runtime schema-management mechanism. The canonical chain is `97d9fd86a3ba -> b6d8f2a4c901 -> d2a7f0c91e35 -> c4e6a8f1b203 -> f7a3c9d2e541 -> a8c4e2f7b913 -> a4fd52f56b91 -> b7e2a9d1c3f4 -> e5c1a7b39d64`; add schema changes as descendants and keep one base/head unless an explicit migration design requires otherwise.
- `frontend/` is a React 19 + TypeScript + Vite application with its own npm lockfile and commands.
- Vector retrieval is not implemented. Do not document or call planned vector components as available until dependencies, durable indexing, deletion, and retrieval contracts land with tests.
- The root `Dockerfile` and `docker-compose.yml` are the supported single-host self-hosted container path. They run one-shot migrations before separate API and worker services as UID/GID 10001 on a shared named volume. `docker-compose.hosted.yml` remains experimental and unsupported for production.

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
npm run build
```

- Run one test with `python -m pytest -q tests/test_document_upload.py::test_name`.
- Frontend lint is deliberately scoped to `src` and `vite.config.ts`; do not replace it with `eslint .`, which scans generated/dependency trees.
- CI also imports `backend.app.models`, builds `main.app` OpenAPI, qualifies the relational contract on PostgreSQL 17.6, and requires tests/builds to leave tracked files unchanged.

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

- Current `dev` job names are `Branch and PR policy`, `Repository quality`, `Backend quality and tests`, `PostgreSQL quality`, `Container quality`, and `Frontend quality and build`.
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
