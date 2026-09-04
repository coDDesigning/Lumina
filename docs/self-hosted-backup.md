# Self-hosted backup and restore

This runbook covers the supported single-host Compose topology. It creates one
checksummed archive containing an online SQLite snapshot, every local upload
referenced by that snapshot, and an offline Chroma snapshot. Restore accepts
only an empty target, checks every payload, requires a revision known to the
current Alembic chain, and installs the database only after verification. The
one-shot migrator upgrades an older retained backup after restore.

The archive contains personal data and password hashes and is not encrypted by
Lumina. Store it on encrypted media or encrypted object storage with access
logging. Never keep the only copy in the `lumina-data` volume or in the source
host's `./backups` directory.

## Service objectives

The minimum operating baseline is:

| Objective | Baseline |
| --- | --- |
| Complete-backup RPO | 24 hours |
| Restore RTO | 2 hours for up to 50 GiB when the archive is locally available |
| Retention | 7 daily and 4 weekly complete archives |
| Restore qualification | Quarterly and before a storage or schema migration |

Shorten the schedule when 24 hours of accepted writes is too much data loss.
Measure restore time with production-sized data. The target assumes a complete
archive is locally available and includes verification and embedding backfill.
Archive retrieval and no-vector recovery need separately measured objectives. A
restore drill that misses either target is a failed drill.

## Durability contract

File-backed SQLite runs in WAL mode with `synchronous=FULL`, a five-second busy
timeout, a 1,000-page automatic checkpoint, and a 64 MiB retained-WAL cap after
successful checkpoints. Long-running readers can delay checkpoints and let the
live WAL exceed that cap. WAL permits normal readers while a writer commits; it
does not make SQLite a multi-host database. Never copy `lumina.db`,
`lumina.db-wal`, or the Chroma directory directly while services are running.

`workers.self_hosted_backup` uses SQLite's online backup API and verifies
`quick_check`, foreign keys, and the Alembic head. It copies only uploads
referenced by the database snapshot and checks each against its recorded byte
size and SHA-256 digest. Chroma is a raw snapshot and therefore requires the API
and worker to be stopped.

One container serves the interface and the API, so stopping `lumina` takes the
interface down with it: for the duration of a backup the deployment does not
answer at all, rather than loading and failing every request. The wrapper
restarts exactly the services it stopped, and records which ones those were in
the volume so an interrupted run can still recover them.

## Complete backup

Set `LUMINA_BACKUP_DIRECTORY` to a host directory outside the repository and
outside the Docker volume. Ensure the container user `10001` can write it. Stop
both writers before taking the complete snapshot. The host wrapper serializes
invocations with a Compose-project-scoped `flock`, verifies shutdown, preserves
a failing exit status, and health-checks only services that were running. Before
stopping them it persists that service set in `lumina-data`; a later invocation
recovers and clears the marker after `SIGKILL`, host failure, or Docker failure.
Recovery runs before checking the off-host backup mount, so a missing mount
cannot leave previously running services stopped:

```bash
set -euo pipefail
export LUMINA_BACKUP_DIRECTORY=/mnt/lumina-backups
sudo install -d -o 10001 -g 10001 -m 0700 "${LUMINA_BACKUP_DIRECTORY}"
sh ops/self_hosted_backup.sh
```

The `--include-chroma-offline` flag in the maintenance service is an explicit
operator assertion that no Chroma writer is running. A missing referenced
upload, changed file hash, database integrity error, schema mismatch, symlink,
or archive write error fails the command and leaves the prior archive unchanged.

Copy the completed archive off-host, verify the destination checksum, and apply
the retention policy. A successful command without an off-host copy does not
satisfy the recovery objective.

Schedule the same fail-closed wrapper at least daily. For example, a Linux cron
entry at 02:00 UTC is:

```cron
0 2 * * * cd /opt/lumina && LUMINA_BACKUP_DIRECTORY=/mnt/lumina-backups /bin/sh ops/self_hosted_backup.sh
```

Route cron or systemd nonzero exits to the deployment alert channel. The example
assumes `/mnt/lumina-backups` is an encrypted off-host mount. If it is local,
sync each completed `0600` archive before considering the run successful.
Configure the destination lifecycle for 7 daily and 4 weekly archives. On
Windows, apply an owner-only ACL to the host backup directory.

An online archive without Chroma can reduce downtime but requires embedding
backfill after restore:

```bash
set -euo pipefail
export BACKUP_ARCHIVE_NAME="lumina-$(date -u +%Y%m%dT%H%M%SZ)-no-vectors.tar.gz"
docker compose --profile maintenance run --rm backup \
  python -m workers.self_hosted_backup backup "/backup/${BACKUP_ARCHIVE_NAME}" \
  --archive-root /backup
```

## Restore drill and recovery

Never restore over the active volume. Keep the original stack stopped and use a
new Compose project name, which creates a fresh `lumina-data` volume and leaves
the original volume available for immediate rollback:

```bash
set -euo pipefail
export ORIGINAL_PROJECT_NAME="lumina-production" # exact value from .env
export RESTORE_PROJECT_NAME="lumina-restore-$(date -u +%Y%m%dT%H%M%SZ)"
docker compose stop lumina lumina-worker
test -z "$(docker compose ps --status running --services lumina lumina-worker)"

COMPOSE_PROJECT_NAME="${RESTORE_PROJECT_NAME}" \
BACKUP_ARCHIVE_NAME="lumina-20260820T120000Z.tar.gz" \
docker compose --profile maintenance run --rm restore

COMPOSE_PROJECT_NAME="${RESTORE_PROJECT_NAME}" docker compose run --rm migrate
COMPOSE_PROJECT_NAME="${RESTORE_PROJECT_NAME}" docker compose run --rm lumina-worker \
  python -m workers.embedding_backfill --prune-orphans
COMPOSE_PROJECT_NAME="${RESTORE_PROJECT_NAME}" LUMINA_PORT=10313 docker compose up -d \
  --no-build --wait --wait-timeout 180 lumina lumina-worker
curl --fail --show-error http://127.0.0.1:10313/health/ready
```

The restored project is given its own `LUMINA_PORT` so it cannot collide with
the original, and it is started by explicit service name so nothing else in it
claims a port either. Verify the restored copy at `http://127.0.0.1:10313`,
interface included, before cutting over.

The restore rejects an existing database, nonempty upload/Chroma directories,
unsafe archive paths, links, checksum mismatches, foreign-key violations, and an
unknown schema revision. Extraction stages on the fresh `lumina-data` volume,
not the bounded `/tmp` filesystem; provision capacity for the uncompressed
archive, the restored state, and one temporary Chroma validation copy. The
embedding backfill is mandatory after every restore: it recreates omitted
vectors, fills any gaps, and prunes records that are not present in the restored
database. `OLLAMA_BASE_URL` must be reachable from the container, or
`GEMINI_API_KEY` must be present when Gemini embeddings are selected.

Verify authentication, course/document counts, representative document reads,
semantic retrieval, and one new upload. Record archive checksum, source release,
target release, start/end timestamps, restored byte count, health result,
backfill result, and operator in the drill log. Do not delete the original
project or volume until the retention window expires.

## Rollback

If validation fails, stop the restored project and restart the untouched
original project:

```bash
set -euo pipefail
COMPOSE_PROJECT_NAME="${RESTORE_PROJECT_NAME}" docker compose stop lumina lumina-worker
test -z "$(COMPOSE_PROJECT_NAME="${RESTORE_PROJECT_NAME}" docker compose ps --status running --services lumina lumina-worker)"
COMPOSE_PROJECT_NAME="${ORIGINAL_PROJECT_NAME}" docker compose up -d \
  --no-build --wait --wait-timeout 180 lumina lumina-worker
curl --fail --show-error "http://127.0.0.1:${LUMINA_PORT:-10312}/health/ready"
```

Investigate the failed archive or restore without modifying either volume. Never
use `docker compose down --volumes` as part of backup, restore, or rollback.
