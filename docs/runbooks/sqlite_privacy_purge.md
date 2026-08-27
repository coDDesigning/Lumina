# SQLite privacy purge

Lumina enables SQLite `secure_delete` on every application connection, so new
row deletions overwrite the deleted cells. `VACUUM` is still required after a
privacy erasure when an operator must remove historical free pages written
before that setting existed, and the WAL must be truncated so an old frame does
not retain deleted content. This is an offline maintenance operation.

Backups are separate retained copies. A purge does not rewrite an existing
backup; expire every backup containing the erased data under the documented
backup retention policy.

## Procedure

1. Preview and enforce masked AI usage retention while the stack is available:

```bash
docker compose --profile maintenance run --rm ai-usage-cleanup --dry-run
docker compose --profile maintenance run --rm ai-usage-cleanup
```

2. Stop every SQLite writer and verify that none remains:

```bash
docker compose stop api worker
test -z "$(docker compose ps --status running --services api worker)"
```

3. Checkpoint, compact, and verify the database in the mounted data volume:

```bash
docker compose run --rm --no-deps migrate python -c "import sqlite3; c=sqlite3.connect('/data/lumina.db'); c.execute('PRAGMA secure_delete=ON'); c.execute('PRAGMA wal_checkpoint(TRUNCATE)'); c.execute('VACUUM'); assert c.execute('PRAGMA quick_check').fetchone()==('ok',); assert c.execute('PRAGMA foreign_key_check').fetchall()==[]; assert c.execute('PRAGMA freelist_count').fetchone()==(0,); c.close()"
```

4. Start the services and verify readiness:

```bash
docker compose up -d --wait --wait-timeout 180 api worker
curl --fail --show-error http://127.0.0.1:8000/health/ready
```

Verify each erased profile-knowledge or course identifier returns `404`, the
profile-knowledge list no longer contains erased rows, and the corresponding
path below `/data/uploads` does not exist. Never delete the database, WAL, or
upload tree directly while either writer is running.
