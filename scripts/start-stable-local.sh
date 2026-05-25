#!/usr/bin/env sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
STABLE_DIR=${TRACK_ANYWHERE_STABLE_DIR:-/Users/xuyanyue/Documents/track-anywhere-stable-backend}
BASE_URL=${TRACK_ANYWHERE_STABLE_BASE_URL:-http://127.0.0.1:12306}

if [ ! -f "$STABLE_DIR/compose.yaml" ] || [ ! -f "$STABLE_DIR/.env" ]; then
  printf 'Stable backend context is missing compose.yaml or .env: %s\n' "$STABLE_DIR" >&2
  exit 1
fi

if [ "${TRACK_ANYWHERE_STABLE_REBUILD:-0}" = "1" ]; then
  "$ROOT/scripts/build-stable-local-image.sh"
fi

cd "$STABLE_DIR"
TRACK_ANYWHERE_API_IMAGE=${TRACK_ANYWHERE_API_IMAGE:-track-anywhere-api:stable} \
  docker compose --env-file .env -f compose.yaml up -d

attempt=1
until curl -fsS "$BASE_URL/api/v1/ready" >/dev/null 2>&1; do
  if [ "$attempt" -ge "${TRACK_ANYWHERE_READY_ATTEMPTS:-30}" ]; then
    printf 'Stable API did not become ready: %s\n' "$BASE_URL" >&2
    exit 1
  fi
  attempt=$((attempt + 1))
  sleep 1
done
printf 'Track Anywhere stable API ready: %s\n' "$BASE_URL"
