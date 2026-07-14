#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if (($# == 0)); then
  echo "usage: pg17-client.sh {psql|pg_restore|pg_dump} [arguments...]" >&2
  exit 2
fi

case "$1" in
  psql|pg_restore|pg_dump) ;;
  *)
    echo "unsupported PostgreSQL client command" >&2
    exit 2
    ;;
esac

exec docker compose \
  -p track-anywhere-v2-test \
  -f "$ROOT_DIR/compose.e2e.yaml" \
  run --rm -T --no-deps pg17-client "$@"
