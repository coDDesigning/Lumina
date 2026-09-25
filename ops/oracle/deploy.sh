#!/bin/sh
# Deploy one commit of main to the Oracle single-host stack.
#
# Installed outside the checkout (install -m 755 ops/oracle/deploy.sh
# ~/bin/lumina-deploy) so the checkout it performs cannot rewrite the running
# script, and pinned as the forced command of the GitHub deploy key, which is
# why the release SHA arrives in SSH_ORIGINAL_COMMAND. The SHA must already be
# on origin/main: the key can deploy any released commit and nothing else.
#
#   lumina-deploy <40-character commit SHA>
#
# See docs/runbooks/oracle-single-host.md.
set -eu

main() {
  release="${SSH_ORIGINAL_COMMAND:-${1:-}}"
  checkout="${LUMINA_CHECKOUT:-$HOME/Lumina}"

  case "$release" in
    *[!0-9a-f]* | "")
      echo "error: expected a lowercase 40-character commit SHA" >&2
      exit 64
      ;;
  esac
  if [ "${#release}" -ne 40 ]; then
    echo "error: expected a lowercase 40-character commit SHA" >&2
    exit 64
  fi

  cd "$checkout"
  git fetch --quiet origin main
  if ! git merge-base --is-ancestor "$release" origin/main; then
    echo "error: $release is not on origin/main" >&2
    exit 65
  fi

  compose="docker compose -f docker-compose.hosted.yml -f docker-compose.oracle.yml"
  previous="$(git rev-parse HEAD)"
  echo "Deploying $release (previous $previous)"

  git checkout --quiet --detach "$release"
  docker build --pull --tag lumina .

  # Back up after the build succeeds and before migrate touches the database.
  sh ops/oracle/backup.sh

  # Wait on the long-running services only: `--wait` fails when the one-shot
  # migrate and minio-init containers exit, even with status 0. Both are
  # dependencies of these two, so a failed migration still fails the deploy.
  $compose up -d --no-build --remove-orphans --wait --wait-timeout 300 lumina lumina-worker
  curl -fsS --retry 10 --retry-delay 3 --retry-all-errors \
    http://127.0.0.1:10312/health/ready
  echo
  docker image prune -f >/dev/null
  echo "Deployed $release"
}

main "$@"
