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
- `backend/app/config.py` is the only application module allowed to read environment variables. Settings are frozen and import-cached; `.env` is not loaded automatically.
- `DEPLOYMENT_MODE=self_hosted` defaults to SQLite and local paths. Hosted mode requires `DATABASE_URL`; pgvector/S3 hosted adapters are not implemented.
- `backend/app/database.py` creates SQLite parent directories and enables `PRAGMA foreign_keys=ON` for every connection. Do not remove the listener; SQLite cascades depend on it.
- `backend/app/models.py` defines the 11-table relational model. `DocumentChunk.course_id` is intentional denormalization for course-scoped reads.
- Keep DB `ondelete="CASCADE"` and ORM `cascade="all, delete-orphan", passive_deletes=True` together. `User -> Role` deliberately does not cascade.
- Root `routes/`, `schemas/`, `services/`, and `utils/` belong to the FastAPI layer. Do not move them under `backend/app/` without an explicit team decision.
- SQL models are not yet the API persistence layer: `services/user.py` and `services/course.py` still use process-local lists.
- `main:app` includes auth/course/admin/user routers only. `routes/document.py` owns a separate `FastAPI` app; its `/upload-doc` endpoint is tested directly and is not exposed by `main:app`.
- Document upload validates bytes and writes generated, content-derived paths. Never derive storage paths from client filenames.
- Alembic is installed but no migration environment exists on current `dev`. `Base.metadata.create_all` appears only in the cascade smoke script; once Alembic lands, schema changes must use migrations exclusively.
- `frontend/` is a React 19 + TypeScript + Vite application with its own npm lockfile and commands.
- `vector_store.py` and `scripts/search_smoke.py` do not exist on current `dev`; do not document or call planned components as implemented.

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
- CI also imports `backend.app.models`, builds OpenAPI for both `main.app` and `routes.document.app`, and requires tests/builds to leave tracked files unchanged.
- `scripts/cascade_smoke.py` mutates the configured database and is not a routine CI check. If needed, use `python -m scripts.cascade_smoke` from the repo root against a disposable `DATABASE_URL`, never a retained database.

## Git And PRs

- Branch flow is `main <- dev <- task branch`. Never commit or push directly to `main` or `dev`.
- From a clean worktree, start a task with `git switch dev && git pull --ff-only origin dev && git switch -c <type>/SCRUM-XX-description`.
- Branches must match `^(feature|fix|chore|docs)/SCRUM-[0-9]+-[a-z0-9][a-z0-9._-]*$`.
- Update a task branch with `git fetch origin && git merge origin/dev`; never rebase or force-push.
- Use `type(scope): message` commits with type `feat`, `fix`, `docs`, `chore`, `test`, `refactor`, `ci`, or `style`. Keep formatting-only changes in a separate `style:` commit.
- Stage intended paths explicitly. Do not use `git add .` except for an audited formatting-only sweep.
- PRs target `dev`; title them `SCRUM-XX: Description` and follow `.github/pull_request_template.md` exactly.
- PR `How to test` must list exact commands and expected results. Prefer `gh pr create --base dev ...` and `gh pr checks --watch`.
- Squash-merge with message `SCRUM-XX: description`, then delete the task branch.

## CI Contract

- Current `dev` job names are `Branch and PR policy`, `Repository quality`, `Backend quality and tests`, and `Frontend quality and build`.
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
