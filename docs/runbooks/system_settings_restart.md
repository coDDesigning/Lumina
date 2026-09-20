# System Settings and Controlled Restart

Recovery procedures for the administrator system settings page
(`/admin/system-settings`) and the restart it requests.

Applies to self-hosted Compose deployments only. Hosted deployments manage
configuration through their own infrastructure and expose no editable surface.

See [System settings and controlled restart](../deployment.md#system-settings-and-controlled-restart)
for the design this runbook operates.

## What the feature touches

| Path | Contents |
| :--- | :--- |
| `/data/system-settings/overrides.json` | The saved revision: administrator-set values. |
| `/data/system-settings/last-known-good.json` | The newest revision that reached `/health/ready`. |
| `/data/system-settings/restart.json` | The current or last restart request and its state. |
| `/data/system-settings/boot.json` | Boot attempts for a revision that has not yet proved itself. |
| `/data/system-settings/.lock` | Held briefly while a write is in progress. |

All of it lives in the `lumina-data` volume. Nothing is ever written to `.env`
or `.env.example`.

## Scenario: a restart never reports ready

The page polls until the target revision is active. If it gives up:

```bash
docker compose ps
docker compose logs --tail 100 lumina
```

An unhealthy `lumina` container that keeps restarting means the saved
configuration cannot start. Lumina rolls this back on its own after three boot
attempts by a serving process, which usually takes seconds. Watch for it:

```bash
docker compose logs lumina | grep -E "system_restart|rolled_back"
```

Once it has rolled back, the container comes up on the last-known-good revision
and the page shows a rolled-back warning naming the revision that failed.

## Scenario: rollback did not happen, or the stack is still down

The override file is the only thing the application reads that an operator can
change from outside. Remove it and Lumina starts on `.env` alone.

```bash
docker compose stop lumina lumina-worker

docker compose run --rm --no-deps --entrypoint sh lumina -c \
  'rm -f /data/system-settings/overrides.json /data/system-settings/boot.json /data/system-settings/restart.json'

docker compose up -d --wait lumina lumina-worker
```

This is the break-glass path. It discards every override; the deployment returns
to exactly what `.env` specifies. Saved secrets are discarded with it, so any
value that existed only as an override has to be set again.

To keep the overrides and inspect them first:

```bash
docker compose run --rm --no-deps --entrypoint sh lumina -c \
  'cat /data/system-settings/overrides.json'
```

Secret values appear in that file in plaintext, because the application has to
read them. Treat its contents as credentials: do not paste them into tickets.

## Scenario: the API is healthy but the worker keeps restarting

The worker adopts configuration the same way the API does, so a value that the
API tolerates but the worker cannot (a job concurrency beyond the database pool,
for example) shows up as a worker-only crash loop. The boot counter is shared,
so the rollback still fires and the API then stops to return to the good
revision as well. If it does not, use the break-glass path above.

`depends_on: service_healthy` orders the first `docker compose up` only; Docker
does not re-evaluate it when it restarts a container on its own. A worker that
comes back before the API has finished migrating retries and settles.

## Scenario: a restart is stuck in queued or draining

A restart waits for in-flight document and generation jobs, bounded by
`SYSTEM_RESTART_DRAIN_TIMEOUT_SECONDS` (default 120). Long visual-description
jobs can hold the full window. Check what is running:

```bash
docker compose exec lumina python -c "
from backend.app.database import SessionLocal
from services.system_restart import count_in_flight_work
with SessionLocal() as session:
    print(count_in_flight_work(session).as_payload())
"
```

The wait is an upper bound, not a condition: once it expires the restart
proceeds and anything still running returns to the queue without spending an
attempt. If the request is still `queued` long past the deadline, the API
process that owned the coordinator is gone. Clear the request and try again:

```bash
docker compose run --rm --no-deps --entrypoint sh lumina -c \
  'rm -f /data/system-settings/restart.json'
```

## Scenario: two administrators edited at once

Every write carries the revision it expects. The second save is refused with
`settings_revision_conflict` and the page reloads to show the newer values.
Nothing is merged silently and nothing is lost; the second administrator
reapplies their change on top of what they can now see.

## Scenario: a setting cannot be edited

Keys pinned by the container (`DATABASE_URL`, `UPLOAD_DIRECTORY`,
`DEPLOYMENT_MODE` and the other `/data` paths) and keys consumed by the Compose
CLI (`LUMINA_PORT`, `LUMINA_IMAGE`, `COMPOSE_PROJECT_NAME`, the resource limits)
are shown read-only, with the reason in the row. Change those by editing `.env`
on the host:

```bash
$EDITOR .env
docker compose up -d
```

`COMPOSE_PROJECT_NAME` also names the `lumina-data` volume. Changing it points
the stack at a different, empty volume; see
[Persistent state](../deployment.md#persistent-state) before touching it.

## Backup and restore

`/data/system-settings/` is included in the archive
`ops/self_hosted_backup.sh` produces, so a restored deployment comes back with
the configuration it was running. An archive taken before this feature existed
restores normally and simply has no overrides in it.

See [Self-Hosted Backup & Restore](../self-hosted-backup.md).

## Audit trail

Every save, reset and restart is recorded as an operational event with the
acting administrator, the revision and the **names** of the keys that changed.
Values never appear, and a name that is not in the registry is masked. Query
them from the admin log centre, or:

```bash
docker compose logs lumina | grep -E "system_settings_|system_restart_"
```
