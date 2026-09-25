#!/bin/sh
# Back up the Oracle single-host stack: a PostgreSQL custom-format dump and an
# archive of the document volume, kept for BACKUP_RETENTION_DAYS (default 7).
# Runs from cron and before every deploy. See docs/runbooks/oracle-single-host.md.
set -eu

backup_dir="${LUMINA_BACKUP_DIRECTORY:-$HOME/backups}"
retention_days="${BACKUP_RETENTION_DAYS:-7}"
project="${COMPOSE_PROJECT_NAME:-lumina}"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"

mkdir -p "$backup_dir"

docker exec "${project}-db-1" pg_dump -U postgres -Fc lumina > "$backup_dir/db-$stamp.dump.partial"
mv "$backup_dir/db-$stamp.dump.partial" "$backup_dir/db-$stamp.dump"

docker run --rm \
  -v "${project}_minio-data:/data:ro" \
  -v "$backup_dir:/out" \
  alpine:3.22@sha256:5291449c3df73caf6ed85e649dec1b9e818b39a5d8c871e97afc13e9cd5e8fa8 \
  tar czf "/out/docs-$stamp.tgz" -C /data .

find "$backup_dir" -maxdepth 1 -type f \( -name 'db-*.dump' -o -name 'docs-*.tgz' \) \
  -mtime +"$retention_days" -delete

echo "Backup written: db-$stamp.dump docs-$stamp.tgz"
