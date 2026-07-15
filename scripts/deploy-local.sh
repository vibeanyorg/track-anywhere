#!/usr/bin/env sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
ENV_FILE=${TRACK_ANYWHERE_DEV_ENV_FILE:-"$ROOT/deploy/env/dev.env"}

if [ ! -f "$ENV_FILE" ]; then
  cp "$ROOT/deploy/env/dev.env.example" "$ENV_FILE"
fi

env_get() {
  key=$1
  awk -F= -v key="$key" '$1 == key { value = $2 } END { print value }' "$ENV_FILE"
}

env_set() {
  key=$1
  value=$2
  tmp_file="${ENV_FILE}.tmp"
  awk -F= -v key="$key" '$1 != key' "$ENV_FILE" > "$tmp_file"
  mv "$tmp_file" "$ENV_FILE"
  printf '%s=%s\n' "$key" "$value" >> "$ENV_FILE"
}

port_in_use() {
  port=$1
  if command -v lsof >/dev/null 2>&1; then
    lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1
  else
    return 1
  fi
}

port_owned_by_dev_stack() {
  port=$1
  docker ps --filter "name=track-anywhere-dev-" --format '{{.Ports}}' \
    | grep -Eq "(^|[.:])${port}->"
}

choose_port() {
  port=$1
  while port_in_use "$port" && ! port_owned_by_dev_stack "$port"; do
    port=$((port + 1))
  done
  printf '%s\n' "$port"
}

API_BIND=$(env_get TRACK_ANYWHERE_DEV_API_BIND)
API_PORT=$(env_get TRACK_ANYWHERE_DEV_API_PORT)
POSTGRES_PORT=$(env_get TRACK_ANYWHERE_DEV_POSTGRES_PORT)
API_BIND=${API_BIND:-127.0.0.1}
API_PORT=${API_PORT:-8000}
POSTGRES_PORT=${POSTGRES_PORT:-55433}

SELECTED_API_PORT=$(choose_port "$API_PORT")
SELECTED_POSTGRES_PORT=$(choose_port "$POSTGRES_PORT")

env_set TRACK_ANYWHERE_DEV_API_PORT "$SELECTED_API_PORT"
env_set TRACK_ANYWHERE_DEV_POSTGRES_PORT "$SELECTED_POSTGRES_PORT"

PUBLIC_HOST=$API_BIND
if [ "$PUBLIC_HOST" = "0.0.0.0" ]; then
  PUBLIC_HOST=127.0.0.1
fi
PUBLIC_ORIGIN="http://$PUBLIC_HOST:$SELECTED_API_PORT"
env_set TRACK_ANYWHERE_API "$PUBLIC_ORIGIN"
env_set TRACK_ANYWHERE_SERVICE_URL "$PUBLIC_ORIGIN"
env_set TRACK_ANYWHERE_PUBLIC_BASE_URL "$PUBLIC_ORIGIN"
env_set TRACK_ANYWHERE_ALLOWED_ORIGINS "http://localhost:$SELECTED_API_PORT,$PUBLIC_ORIGIN"
env_set TRACK_ANYWHERE_PROJECTION_POLL_SECONDS "${TRACK_ANYWHERE_PROJECTION_POLL_SECONDS:-2}"

cd "$ROOT"
COMPOSE="docker compose --env-file $ENV_FILE -f compose.dev.yaml"
if [ "${TRACK_ANYWHERE_DEV_REBUILD:-0}" = "1" ]; then
  # shellcheck disable=SC2086
  $COMPOSE build api
fi
# Force the one-shot privilege bootstrap and Alembic migration to run on every
# local start; both are idempotent and the application waits for both.
# shellcheck disable=SC2086
$COMPOSE rm -sf bootstrap migrate >/dev/null 2>&1 || true
# shellcheck disable=SC2086
$COMPOSE up -d api

printf 'Track Anywhere dev: %s\n' "$PUBLIC_ORIGIN"
