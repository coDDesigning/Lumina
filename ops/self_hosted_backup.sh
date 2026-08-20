#!/bin/sh

set -eu

LUMINA_BACKUP_DIRECTORY=${LUMINA_BACKUP_DIRECTORY:-./backups}
BACKUP_ARCHIVE_NAME=${BACKUP_ARCHIVE_NAME:-lumina-$(date -u +%Y%m%dT%H%M%SZ).tar.gz}
export LUMINA_BACKUP_DIRECTORY BACKUP_ARCHIVE_NAME

case "$BACKUP_ARCHIVE_NAME" in
  ""|*/*|*\\*|.|..)
    echo "BACKUP_ARCHIVE_NAME must be a file name, not a path." >&2
    exit 2
    ;;
esac

if ! command -v flock >/dev/null 2>&1; then
  echo "flock is required for crash-safe backup serialization." >&2
  exit 2
fi
project_name=${COMPOSE_PROJECT_NAME:-}
if [ -z "$project_name" ]; then
  project_name=$(docker compose config --format json | awk -F '"' '/^[[:space:]]*"name":/ { print $4; exit }')
fi
if [ -z "$project_name" ]; then
  echo "Unable to resolve the Compose project name." >&2
  exit 2
fi
lock_file=${TMPDIR:-/tmp}/lumina-${project_name}-backup.lock
exec 9>>"$lock_file"
if ! flock -n 9; then
  echo "Another self-hosted backup is already running." >&2
  exit 3
fi

running_services=

validate_services() {
  for service in $1; do
    case "$service" in
      api|worker) ;;
      *)
        echo "Invalid service in persisted backup recovery state." >&2
        return 1
        ;;
    esac
  done
}

read_restart_state() {
  docker compose run --rm --no-deps migrate sh -c \
    'test ! -f /data/.lumina-backup-restart-services || cat /data/.lumina-backup-restart-services'
}

write_restart_state() {
  docker compose run --rm --no-deps \
    --env LUMINA_RESTART_SERVICES="$running_services" migrate sh -c '
      umask 077
      temporary=/data/.lumina-backup-restart-services.tmp
      printf "%s\n" "$LUMINA_RESTART_SERVICES" > "$temporary"
      mv "$temporary" /data/.lumina-backup-restart-services
    '
}

clear_restart_state() {
  docker compose run --rm --no-deps migrate \
    rm -f /data/.lumina-backup-restart-services
}

cleanup() {
  status=$?
  trap - EXIT INT TERM
  if [ -n "$running_services" ]; then
    # Intentional splitting restores exactly the services that were running.
    if docker compose up -d --wait --wait-timeout 180 $running_services; then
      if ! clear_restart_state; then
        [ "$status" -ne 0 ] || status=1
      fi
    else
      [ "$status" -ne 0 ] || status=1
    fi
  fi
  exit "$status"
}

trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

pending_services=$(read_restart_state | tr '\r\n' ' ' | sed 's/[[:space:]]*$//')
validate_services "$pending_services"
if [ -n "$pending_services" ]; then
  running_services=$pending_services
  # Intentional splitting recovers the persisted service set.
  docker compose up -d --wait --wait-timeout 180 $running_services
  clear_restart_state
  running_services=
fi

if [ ! -d "$LUMINA_BACKUP_DIRECTORY" ]; then
  echo "LUMINA_BACKUP_DIRECTORY must be provisioned before backup." >&2
  exit 2
fi
running_services=$(docker compose ps --status running --services api worker | tr '\r\n' ' ' | sed 's/[[:space:]]*$//')
validate_services "$running_services"
if [ -n "$running_services" ]; then
  write_restart_state
fi
docker compose stop api worker
if [ -n "$(docker compose ps --status running --services api worker)" ]; then
  echo "API or worker is still running; refusing an offline Chroma snapshot." >&2
  exit 4
fi
docker compose --profile maintenance run --rm backup
