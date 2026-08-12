# Deployment readiness

Lumina's API and durable document processor currently support production only
in `self_hosted` mode with SQLite and local filesystem document storage. The
relational contract is tested against PostgreSQL 17.6 in CI, but hosted and
PostgreSQL production remain blocked until durable shared storage and deployment
topology are qualified. Vector retrieval is not implemented or
production-qualified.

The tracked Docker and Compose files are experimental development artifacts.
They do not yet implement the migration ownership, non-root runtime, API/worker
composition, health checks, or immutable image requirements in this document
and must not be used as a production deployment path.

## Production configuration

Set configuration through the environment before importing or starting the
application. Production requires:

| Variable | Requirement |
| --- | --- |
| `APP_ENV` | `production` |
| `APP_DEBUG` | `false` |
| `DEPLOYMENT_MODE` | `self_hosted` |
| `JWT_SECRET_KEY` | Explicit value containing at least 32 characters |
| `BOOTSTRAP_ADMIN_EMAIL` | Email allowed to claim the initial administrator |
| `BOOTSTRAP_ADMIN_TOKEN` | At least 32 visible ASCII characters |
| `DATABASE_URL` | Explicit SQLite URL with an absolute database path |
| `UPLOAD_DIRECTORY` | Absolute persistent document-storage path |
| `CHROMA_PERSIST_DIRECTORY` | Absolute reserved vector-data path |
| `OCR_LANGUAGE` | Tesseract language data installed in the worker runtime |

The database and document-storage directory must be retained across application
restarts. Before migration, provision the parent directory of the SQLite file.
Provision `UPLOAD_DIRECTORY` as an existing directory or mounted volume before
checking readiness. Production deliberately does not recreate either missing
path. A writable directory cannot prove that the intended volume is mounted, so
the deployment platform must verify mount identity before migration and startup.
API and worker processes must use the same database, `STORAGE_NAMESPACE`, and
document-storage contents.

## Startup sequence

Apply migrations before starting either process. Readiness checks verify state;
they never migrate, stamp, seed, or repair it.

```bash
python -m alembic upgrade head
python -m alembic current --check-heads
python -m alembic check
python -m workers.document_processor --check
uvicorn main:app --host 0.0.0.0 --port 8000
python -m workers.document_processor
```

Run the API and worker under separate supervisors. Configure the worker's
termination grace as described in [`database.md`](database.md).
Register `BOOTSTRAP_ADMIN_EMAIL` with the configured token in the
`X-Bootstrap-Token` header over a trusted route before opening public ingress.

The default `docker-compose.yml` starts both processes with the same `data`
volume and waits for API readiness before starting the worker. The image installs
Tesseract with English language data. Custom `OCR_LANGUAGE` values require the
matching `*.traineddata` packages in the image. OCR-dependent documents fail
with a safe `OCR_UNAVAILABLE` code when the runtime does not provide the selected
language.

## Health probes

The API exposes unauthenticated operational probes:

| Endpoint | Success | Failure meaning |
| --- | --- | --- |
| `GET /health/live` | `200 {"status":"alive"}` | The process cannot serve HTTP |
| `GET /health/ready` | `200 {"status":"ready"}` | `503 {"status":"not_ready"}` |

Liveness does not access external dependencies. Readiness requires database
connectivity, an exact match between the database and code-side Alembic heads,
all required role seeds, and a successful temporary write/read/delete cycle in
document storage. Failure responses deliberately omit provider paths, migration
identifiers, and exception details.

Use liveness only to recycle an unresponsive process. Route traffic only while
readiness succeeds. Keep both operational endpoints on a trusted probe network
instead of exposing them through the public ingress.
