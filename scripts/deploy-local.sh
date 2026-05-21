#!/usr/bin/env sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
ENV_FILE=${TRACK_ANYWHERE_DEV_ENV_FILE:-"$ROOT/deploy/env/dev.env"}

if [ ! -f "$ENV_FILE" ]; then
  cp "$ROOT/deploy/env/dev.env.example" "$ENV_FILE"
fi

cd "$ROOT"
docker compose --env-file "$ENV_FILE" -f compose.dev.yaml up -d --build

SERVICE_URL=$(awk -F= '/^TRACK_ANYWHERE_SERVICE_URL=/{print $2}' "$ENV_FILE" | tail -n 1)
if [ -z "$SERVICE_URL" ]; then
  SERVICE_URL=$(awk -F= '/^TRACK_ANYWHERE_API=/{print $2}' "$ENV_FILE" | tail -n 1)
fi
printf 'Track Anywhere dev service: %s\n' "${SERVICE_URL:-http://127.0.0.1:8000}"
