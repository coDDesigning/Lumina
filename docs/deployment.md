# Deployment readiness

Lumina's API and durable document processor support production only in
`self_hosted` mode with SQLite and local filesystem document storage. The root
`Dockerfile` and `docker-compose.yml` are the supported single-host container
path for that mode. The separate `docker-compose.hosted.yml` remains an
experimental development artifact and must not be used for production.

The relational contract is tested against PostgreSQL 17.6 in CI, but hosted and
PostgreSQL production remain blocked until durable shared storage and deployment
topology are qualified. Vector retrieval is not implemented or
production-qualified.

## Container architecture

The production image:

- pins the Python 3.12 Debian base by immutable OCI index digest;
- installs only hash-locked binary dependencies from `requirements.txt`;
- selectively copies runtime files instead of the repository;
- runs as unprivileged UID/GID `10001:10001`;
- keeps application files root-owned and non-writable; and
- uses an inert entrypoint that never applies migrations.

Compose runs three roles from that image:

| Service | Responsibility | Expected state |
| --- | --- | --- |
| `migrate` | Apply `alembic upgrade head` once before runtime roles start | Exited with code 0 |
| `api` | Serve HTTP and readiness probes | Running and healthy |
| `worker` | Claim and process durable document jobs | Running and healthy |

All roles run without Linux capabilities, with `no-new-privileges`, a read-only
root filesystem, and a bounded temporary filesystem. Only `/data` is
persistently writable. The API binds to host loopback by default; put a trusted
reverse proxy in front of it instead of publishing the application port
directly.

## First deployment

Use Docker Engine with Docker Compose v2.20 or newer on one x86-64 or ARM64 Linux
host. Create a host-owner-only configuration file:

```bash
install -m 0600 .env.example .env
```

Replace at least `JWT_SECRET_KEY`, `BOOTSTRAP_ADMIN_EMAIL`, and
`BOOTSTRAP_ADMIN_TOKEN` with production values. Keep `COMPOSE_PROJECT_NAME`
stable across release directories because it identifies the persistent volume.
Prefer URL-safe generated secrets; single-quote any Compose `.env` value that
contains `$` to prevent interpolation. Never commit `.env`. Then run:

```bash
set -euo pipefail
docker compose build --pull
docker compose up --detach --wait --wait-timeout 180
docker compose ps --all
curl --fail http://127.0.0.1:8000/health/ready
```

Migration failure prevents dependent runtime roles from starting. A readiness
failure makes `docker compose up --wait` return nonzero, but leaves containers
available for inspection. Keep ingress closed until the command succeeds and
`migrate` is exited with code 0 while both `api` and `worker` are healthy.
Register `BOOTSTRAP_ADMIN_EMAIL` with the configured token in the
`X-Bootstrap-Token` header over a trusted route before opening public ingress.

## Transition from the experimental stack

The previous experimental Compose file is not an in-place upgrade. It stored
state in the `./data` bind mount and named its API service `lumina-backend`; the
supported topology uses a project-scoped named volume and an `api` service.

If that experimental stack contains state, stop it with
its original Compose file, verify no old container is still writing, take a
verified copy of `./data`, and import that state into the new volume with
ownership `10001:10001` before the first migration. The supported stack
deliberately does not auto-import legacy state. Starting it before the import
initializes an empty database.

## Deploy an update

Drain the worker and stop both runtime roles before changing the schema. The
default API and worker stop grace is 345 seconds. The worker value matches the
default processing timeout plus 45 seconds.

```bash
set -euo pipefail
docker compose build --pull
docker compose stop api worker
docker compose run --rm --no-deps --entrypoint sh migrate -c 'test -s /data/lumina.db'
docker compose run --rm --no-deps migrate
docker compose up --detach --no-deps --wait --wait-timeout 180 api worker
curl --fail http://127.0.0.1:8000/health/ready
```

Do not place migration commands in the API or worker startup path and do not run
multiple migrators concurrently. If migration fails, keep the runtime roles
stopped and investigate before starting either role.

## Persistent state

The project-scoped `lumina-data` named volume contains all supported durable
state:

```text
/data/lumina.db
/data/uploads/
/data/chroma/
```

The volume is shared by the migrator, API, and worker and survives
`docker compose down`. Compose derives its engine-level name from the project.
`COMPOSE_PROJECT_NAME` is required. Give each intentional stack a unique value
and never change it for an existing stack; changing it selects a different data
set. Verify the intended volume before migration and startup. Never use
`docker compose down --volumes` unless permanent deletion of the database and
uploaded documents is intended.

This topology is single-host. Do not scale the API or worker across hosts and do
not move SQLite or uploads to an unqualified network filesystem. Automated
backup, restore, and rollback qualification are separate release requirements.

## Production configuration

Compose fixes the following safety-critical values:

| Variable | Container value |
| --- | --- |
| `APP_ENV` | `production` |
| `APP_DEBUG` | `false` |
| `DEPLOYMENT_MODE` | `self_hosted` |
| `DATABASE_URL` | `sqlite:////data/lumina.db` |
| `UPLOAD_DIRECTORY` | `/data/uploads` |
| `CHROMA_PERSIST_DIRECTORY` | `/data/chroma` |
| `STORAGE_BACKEND` | `local` |

API authentication values, storage namespace, upload limits, validation limits,
and worker limits are interpolated from `.env`. `JWT_SECRET_KEY` must contain at
least 32 characters. `BOOTSTRAP_ADMIN_TOKEN` must contain at least 32 visible
ASCII characters. Treat `STORAGE_NAMESPACE` as immutable after the volume holds
documents; changing it strands jobs created for the prior provider identity.

The temporary filesystem must hold two spooled copies of each concurrent
maximum-size chunked upload. Keep `LUMINA_TMPFS_SIZE_BYTES` at least
`2 * MAX_CONCURRENT_DOCUMENT_VALIDATIONS * (MAX_UPLOAD_SIZE_BYTES + 1048576)`.
`UPLOAD_REQUEST_TIMEOUT_SECONDS` bounds both upload-slot admission and body
receipt and cannot exceed 300 seconds. Configure the reverse proxy with a
request-body timeout no greater than this value and reject ambiguous
`Content-Length` plus `Transfer-Encoding` framing at ingress.
If `PROCESSING_JOB_ATTEMPT_TIMEOUT_SECONDS` changes, keep
`WORKER_STOP_GRACE_PERIOD` at least that duration plus 45 seconds. The API uses
a fixed 330-second graceful-shutdown deadline inside Docker's 345-second stop
grace.

For a manual non-container deployment, set all production values through the
environment before importing the application. The SQLite parent and both
storage directories must already exist and be persistent. Apply migrations once:

```bash
python -m alembic upgrade head
python -m alembic current --check-heads
python -m alembic check
python -m workers.document_processor --check
```

Then start these as two separate supervisor units, not as sequential shell
commands. Give both units at least 345 seconds to stop before forcing
termination:

```bash
uvicorn main:app --host 0.0.0.0 --port 8000 --limit-concurrency 100 --timeout-graceful-shutdown 330
python -m workers.document_processor
```

The image installs Tesseract with English language data. Custom `OCR_LANGUAGE`
values require rebuilding the image with the matching `*.traineddata` packages.
OCR-dependent documents fail with a safe `OCR_UNAVAILABLE` code when the runtime
does not provide the selected language.

## Health probes

The API exposes unauthenticated operational probes:

| Endpoint | Success | Failure meaning |
| --- | --- | --- |
| `GET /health/live` | `200 {"status":"alive"}` | The process cannot serve HTTP |
| `GET /health/ready` | `200 {"status":"ready"}` | `503 {"status":"not_ready"}` |

Liveness does not access external dependencies. Readiness requires database
connectivity, an exact match between database and code-side Alembic heads, all
required role seeds, and a successful temporary write/read/delete cycle in
document storage. Failure responses deliberately omit provider paths, migration
identifiers, and exception details.

Use liveness only to recycle an unresponsive process. Route traffic only while
readiness succeeds. Keep both endpoints on a trusted probe network instead of
exposing them through public ingress.
