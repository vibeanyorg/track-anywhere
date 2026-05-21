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
  docker ps --filter "name=track-anywhere-dev-" --format '{{.Ports}}' | grep -Eq "(^|[.:])${port}->"
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

if [ "$SELECTED_API_PORT" != "$API_PORT" ]; then
  env_set TRACK_ANYWHERE_DEV_API_PORT "$SELECTED_API_PORT"
  env_set TRACK_ANYWHERE_API "http://$API_BIND:$SELECTED_API_PORT"
  env_set TRACK_ANYWHERE_SERVICE_URL "http://$API_BIND:$SELECTED_API_PORT"
fi
if [ "$SELECTED_POSTGRES_PORT" != "$POSTGRES_PORT" ]; then
  env_set TRACK_ANYWHERE_DEV_POSTGRES_PORT "$SELECTED_POSTGRES_PORT"
fi

cd "$ROOT"
docker compose --env-file "$ENV_FILE" -f compose.dev.yaml up -d --build

SERVICE_URL=$(awk -F= '/^TRACK_ANYWHERE_SERVICE_URL=/{print $2}' "$ENV_FILE" | tail -n 1)
if [ -z "$SERVICE_URL" ]; then
  SERVICE_URL=$(awk -F= '/^TRACK_ANYWHERE_API=/{print $2}' "$ENV_FILE" | tail -n 1)
fi
printf 'Track Anywhere dev service: %s\n' "${SERVICE_URL:-http://127.0.0.1:8000}"
