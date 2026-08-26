# Deployment readiness

Lumina's API and durable document processor support production in two container
topologies, both built from the same image:

- `self_hosted` with SQLite, local filesystem document storage, and Chroma
  vectors, via the root `Dockerfile` and `docker-compose.yml`; and
- `hosted` with PostgreSQL, S3-compatible document storage, and pgvector
  vectors, via `docker-compose.hosted.yml`.

The relational contract is tested against PostgreSQL 17.8 in CI on a
pgvector-enabled image, and the hosted topology is exercised live against
pinned MinIO and pgvector containers before it is shipped. Chunk embeddings are
stored, indexed, and searchable behind course isolation in both backends (see
`docs/vector_storage.md`). Hosted production fails at startup unless
`STORAGE_BACKEND=s3` provides durable shared storage; a single instance's local
disk never qualifies.

## Container architecture

The production image:

- pins the Python 3.12 Debian base by immutable OCI index digest;
- installs only hash-locked binary dependencies from `requirements.txt`;
- selectively copies runtime files instead of the repository;
- runs as unprivileged UID/GID `10001:10001`;
- keeps application files root-owned and non-writable; and
- uses an inert entrypoint that never applies migrations.

Compose runs three roles from that image in both topologies:

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

## Hosted topology

`docker-compose.hosted.yml` runs the same three roles against PostgreSQL and an
S3-compatible object store, using the same pinned production image:

| Service | Responsibility | Expected state |
| --- | --- | --- |
| `db` | pgvector-enabled PostgreSQL | Running and healthy |
| `minio` | S3-compatible document storage | Running |
| `minio-init` | Create `S3_BUCKET` once | Exited with code 0 |
| `migrate` | Apply `alembic upgrade head` once | Exited with code 0 |
| `api` | Serve HTTP and readiness probes | Running and healthy |
| `worker` | Claim and process durable document jobs | Running and healthy |

The stack requires `COMPOSE_PROJECT_NAME`, `STORAGE_NAMESPACE`,
`S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`, `JWT_SECRET_KEY`,
`BOOTSTRAP_ADMIN_EMAIL`, and `BOOTSTRAP_ADMIN_TOKEN` from `.env`. The MinIO
root user and password are the S3 access and secret keys, so keep both values
secret and at least as strong as the JWT secret. `S3_BUCKET` defaults to
`lumina` and is created by `minio-init`; never point an existing stack at a new
namespace or bucket, because jobs for the prior provider identity are stranded.

The API and worker healthchecks gate MinIO startup: the readiness probe runs a
temporary S3 write/read/delete cycle (`check_ready`), so a stack started before
MinIO serves retries until the bucket answers, and `docker compose up --wait`
fails if the bucket never becomes writable. The MinIO image has no shell, so it
declares no container healthcheck of its own.

For a real AWS deployment, keep the same environment except:
`S3_ENDPOINT_URL` and `S3_FORCE_PATH_STYLE` are removed, `S3_REGION` is set to
the bucket's region, static credentials are optional (prefer an IAM role for the
EC2 instance or ECS task), and the bucket is provisioned outside the stack
(`minio`/`minio-init` services are not used).

## AWS production topology (Terraform)

The `terraform/` configuration provisions the hosted production topology on
AWS and is the supported way to run Lumina in the cloud. It builds the same
image (`Dockerfile`) and runs the same three roles:

| Resource | Provisioned by Terraform |
| --- | --- |
| VPC | Public/private subnets, one NAT gateway, route tables |
| ECR | `lumina` repository (immutable tags, scan on push, keep 20 images) |
| S3 | Separate private document and frontend buckets (versioned, encrypted, TLS-only policies) |
| CloudFront | OAC static delivery; `/api` and `/api/*` proxy to the ALB without caching |
| RDS | PostgreSQL 16.8+, pgvector 0.8+, storage autoscaling, Performance Insights |
| RDS Proxy | TLS-only runtime connection pool; direct RDS access is migrator-only |
| ECS | Fargate `api` + `worker` services, one-off `migrate` task definition |
| ALB | HTTPS (ACM) listener, HTTP-to-HTTPS redirect, `/health/ready` target check |
| Route53 | Optional frontend A/AAAA aliases to CloudFront and API-origin A alias to the ALB |

### Hosted frontend decision and routing contract

Hosted production uses private S3 plus CloudFront rather than an Nginx ECS
sidecar. Static delivery therefore does not consume API/worker capacity or add
frontend files to the backend image. The public application hostname points to
CloudFront. CloudFront serves `current/` from the frontend bucket by OAC and
routes exact `/api` plus `/api/*` paths to the ALB with caching disabled. The
browser sees one origin, so the production setting `VITE_API_BASE_URL=/api`
needs no CORS.

A distinct `api_origin_domain_name` points to the ALB. During the staged
transition, the regional ALB certificate must cover both that name and
`frontend_domain_name`; after cutover only the API-origin name reaches the ALB.
The CloudFront viewer certificate must cover `frontend_domain_name` and reside
in `us-east-1`. Reusing the public frontend hostname as the origin would
recurse after DNS cutover, while using the raw ALB hostname would not match the
customer ACM certificate.

The static default behavior uses a viewer-request function to rewrite `/` and
extensionless GET/HEAD routes to `/index.html`. `/assets/*` bypasses the
function. API behaviors are separate, so backend error statuses, JSON bodies,
`X-Error-Code`, and `X-Request-ID` pass through unchanged. There is deliberately
no distribution-wide 403/404-to-index fallback. Every behavior adds HSTS,
content-type, frame, referrer, and content-security headers. The API origin and
ALB use 120-second response/idle timeouts. Hosted retrieval embeddings are
bounded to 10 seconds and each generation attempt to 30 seconds, so retrieval,
three generation attempts, and their three seconds of backoff fit within 103
seconds and leave headroom inside that edge budget.

Cross-origin builds are optional. Set `VITE_API_BASE_URL` to the complete
absolute API prefix and configure `CORS_ALLOWED_ORIGINS` with exact comma-
separated frontend origins. Wildcards are rejected. CORS permits the explicit
bearer `Authorization` header, does not enable cookie credentials, and exposes
`X-Error-Code`. Leave the setting empty for same-origin deployment.

The first rollout is deliberately two-phase. Before applying the OIDC trust
policy, create the GitHub `production` environment and restrict its deployment
branches to `main`; the workflow also refuses to deploy any other ref. Apply
Terraform with
`frontend_dns_cutover=false` so the moved application A record remains on the
ALB while CloudFront and the empty frontend bucket are created. Configure the
workflow's `FRONTEND_URL` from the `cloudfront_url` output and deploy once to
publish and verify `current/index.html`. Then apply with
`frontend_dns_cutover=true`, which retargets the A record and creates the AAAA
record, and switch the workflow variable to the public `frontend_url`. This
avoids an empty-bucket 403 window. Set `dns_record_name` to the existing
record's full hostname through the Terraform state move so Route53 does not
replace the record. External DNS follows the same sequence.

Apply order matters once, on the first rollout: the ECS tasks read
`JWT_SECRET_KEY`, `BOOTSTRAP_ADMIN_TOKEN`, and `GEMINI_API_KEY` from SSM
parameter paths under `/<project>-<environment>/` (see `terraform/README.md`),
and the runtime `DATABASE_URL` from Secrets Manager. The secrets module (SCRUM-94)
creates those parameters, so run the full Terraform apply before the first
deploy pipeline run. ECS services retry task starts until the parameters
exist. Alembic installs and upgrades the `vector` extension and refuses a
version older than 0.8.0.

On AWS the application uses IAM roles, not static credentials: the ECS task
role gets `s3:GetObject`/`s3:PutObject`/`s3:DeleteObject` on the document
bucket, and `S3_ENDPOINT_URL`/`S3_FORCE_PATH_STYLE` are not set. The worker
autoscales between `worker_min_instances` and `worker_max_instances` on the
oldest queued-job age; the API autoscales between `api_min_instances` and
`api_max_instances` on CPU.

Horizontal scaling is qualified only for the AWS hosted topology:
PostgreSQL `SKIP LOCKED` partitions claims across workers, claim tokens and
leases fence stale workers, RDS Proxy bounds database connections, and S3 plus
pgvector provide shared durable state. Scale-in has a 120-second Fargate stop
timeout; a task killed after that cannot publish through an expired token and
the next worker recovers its lease. Self-hosted SQLite/local/Chroma remains a
single-host, single-worker topology. Provider concurrency and upload limits are
per process, so raising replica maxima multiplies upstream AI/embedding load.

Deployments use one commit SHA for the backend image, frontend release, and
sanitized task-definition documents. The workflow archives the frontend and
task definitions before runtime mutation, runs the one-off `migrate` task,
rolls out both ECS services, then promotes an exact copy of the static output
and uploads `index.html` last. ECS deployment circuit breakers and the workflow
restore both previous service revisions if either rollout fails. The state
bucket is passed to Terraform with
`-backend-config`; never commit `.tfstate`, saved plans, or `terraform.tfvars`.

### AWS secrets management

Runtime secrets are stored in AWS Systems Manager Parameter Store as
`SecureString` parameters under `/<project>-<environment>/` and are injected
into the ECS task definitions at task start (container `secrets` entries, read
by the task execution role). The Terraform `secrets` module creates them from
the `runtime_secrets` map in `terraform.tfvars`; no secret value is committed
or stored in GitHub. Secrets Manager holds separate TLS URLs for runtime and
migration: API/worker use RDS Proxy, while the one-shot migrator connects
directly to RDS. A third credential document is readable only by RDS Proxy.
The ECS task role authenticates to S3 with an IAM role; no static AWS keys
exist on the platform side.

The GitHub Actions deploy role uses OIDC federation. Its trust policy accepts
the exact GitHub `production` environment subject. Configure that environment's
deployment branch policy to permit `main` only; environment protection is the
branch boundary because environment jobs receive an environment-shaped OIDC
subject. The role can deploy but cannot read runtime secrets. Set its ARN as
`AWS_DEPLOY_ROLE_ARN` on the production environment.

### AWS observability

Application and worker logs are single-line privacy-safe JSON in CloudWatch
Logs. Request IDs correlate API events; worker queue and outcome metrics use
CloudWatch Embedded Metric Format. Terraform provisions the operations
dashboard, SNS alarm topic, and baseline ALB/ECS/RDS/RDS Proxy/queue alarms.
Set the optional `alarm_email` Terraform variable and confirm the SNS
subscription before launch. See `docs/observability.md` for the field contract,
thresholds, and required staging alarm exercise.

### AWS deploy pipeline

`.github/workflows/deploy.yml` deploys the repository to the AWS topology on a
push to `main` or through manual dispatch from `main`. The workflow authenticates with the
GitHub OIDC role created by the `github-oidc` module, never with stored
long-lived keys. It requires these repository environment variables and
secrets on the `production` environment:

| Setting | Source |
| --- | --- |
| `vars.AWS_REGION`, `vars.ECR_REPOSITORY`, `vars.ECS_CLUSTER` | Terraform outputs |
| `vars.API_SERVICE`, `vars.WORKER_SERVICE` | Terraform outputs |
| `vars.API_TASK_DEFINITION`, `vars.WORKER_TASK_DEFINITION`, `vars.MIGRATE_TASK_DEFINITION` | Terraform outputs |
| `vars.PRIVATE_SUBNETS`, `vars.ECS_SECURITY_GROUP` | `private_subnet_ids_csv`, `ecs_security_group_id` outputs |
| `vars.FRONTEND_BUCKET` | `frontend_bucket_name` output |
| `vars.CLOUDFRONT_DISTRIBUTION_ID` | `cloudfront_distribution_id` output |
| `vars.FRONTEND_URL` | `cloudfront_url` before DNS cutover; `frontend_url` afterward |
| `vars.VITE_API_BASE_URL` | `/api` for the supported same-origin topology |
| `secrets.AWS_DEPLOY_ROLE_ARN` | `github-oidc` module output |

The workflow builds the image and frontend for `github.sha`, uploads immutable
checksummed frontend and task-definition archives under `releases/<sha>/`,
registers those task definition revisions, runs migration, rolls out both
services, and then publishes the matching frontend below `current/`. Hashed
assets are immutable; unhashed files revalidate; `index.html` is the final
upload; the distribution is invalidated; and only then are stale current files
deleted.
Release archives expire after 180 days and noncurrent object versions after 30
days. A rerun must reproduce the archived checksums or it fails before runtime
infrastructure mutation.

A rollback accepts only a full commit SHA. Before mutation it verifies the
immutable ECR image plus checksummed frontend and task-definition archives,
skips migration, deploys the archived frontend first, and then registers and
deploys the archived API/worker task-definition documents. Publishing the older
frontend first preserves the already-qualified
old-frontend/new-backend compatibility direction if ECS rollback fails. Database
schema is never downgraded, so migrations must remain backward compatible with
the retained rollback window. Production smoke checks exercise the SPA root,
a deep link, an asset, an unauthenticated API request, and an API 404 through
CloudFront; the raw ALB hostname is not used as an HTTPS client hostname. A
failed rollout, publication, or smoke check restores the previously captured
task definitions and exact frontend contents as needed, preserving the
qualified compatibility direction during both deployment and rollback
restoration.

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

`/data/chroma/` holds the self-hosted vector collection and is load-bearing:
`VECTOR_BACKEND` defaults to `chroma` on SQLite, and document processing writes
one embedding per chunk there. Losing the directory does not lose data, but every
affected document must be re-embedded with
`python -m workers.embedding_backfill` before semantic features work again. Back
it up with the database, not separately: a restore that pairs an old
`/data/chroma/` with a newer `/data/lumina.db` leaves vectors for chunks that no
longer exist, which `python -m workers.embedding_backfill --prune-orphans`
resolves.

After a storage or vector-store outage, `python -m workers.course_purge` finishes
course deletions that answered `500` while it was down, and `python -m workers.embedding_backfill`
re-indexes missing vectors. In production, the background worker automatically executes
both reconciliation tasks periodically on configured intervals (`COURSE_PURGE_INTERVAL_SECONDS`
and `EMBEDDING_BACKFILL_INTERVAL_SECONDS`, defaulting to 1 hour), and rerunning them is always safe.

A hosted PostgreSQL deployment sets `VECTOR_BACKEND=pgvector` instead and stores
vectors in the database, which requires the `vector` extension to be available to
the migration role. See `docs/vector_storage.md`.

The volume is shared by the migrator, API, and worker and survives
`docker compose down`. Compose derives its engine-level name from the project.
`COMPOSE_PROJECT_NAME` is required. Give each intentional stack a unique value
and never change it for an existing stack; changing it selects a different data
set. Verify the intended volume before migration and startup. Never use
`docker compose down --volumes` unless permanent deletion of the database and
uploaded documents is intended.

This topology is single-host. Do not scale the API or worker across hosts and do
not move SQLite or uploads to an unqualified network filesystem. Use the
verified backup, fresh-volume restore, and reversible cutover procedure in
[`self-hosted-backup.md`](self-hosted-backup.md).

## Production configuration

The self-hosted Compose fixes the following safety-critical values:

| Variable | Container value |
| --- | --- |
| `APP_ENV` | `production` |
| `APP_DEBUG` | `false` |
| `DEPLOYMENT_MODE` | `self_hosted` |
| `DATABASE_URL` | `sqlite:////data/lumina.db` |
| `UPLOAD_DIRECTORY` | `/data/uploads` |
| `CHROMA_PERSIST_DIRECTORY` | `/data/chroma` |
| `VECTOR_BACKEND` | `chroma` (override via `.env`) |
| `STORAGE_BACKEND` | `local` |

The hosted Compose fixes these values instead:

| Variable | Container value |
| --- | --- |
| `APP_ENV` | `production` |
| `APP_DEBUG` | `false` |
| `DEPLOYMENT_MODE` | `hosted` |
| `DATABASE_URL` | `postgresql+psycopg://postgres:postgres@db:5432/lumina` |
| `STORAGE_BACKEND` | `s3` |
| `S3_ENDPOINT_URL` | `http://minio:9000` |
| `S3_FORCE_PATH_STYLE` | `true` |
| `VECTOR_BACKEND` | `pgvector` |

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
