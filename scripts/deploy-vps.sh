#!/usr/bin/env sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
HOST=${1:-${TRACK_ANYWHERE_DEPLOY_HOST:-root@cc6}}
REMOTE_DIR=${TRACK_ANYWHERE_REMOTE_DIR:-/opt/track-anywhere-docker}
API_IMAGE=${TRACK_ANYWHERE_API_IMAGE:-ghcr.io/vibeanyorg/track-anywhere-api:latest}
WEB_IMAGE=${TRACK_ANYWHERE_WEB_IMAGE:-ghcr.io/vibeanyorg/track-anywhere-web:latest}
ENV_SOURCE=${TRACK_ANYWHERE_REMOTE_ENV_SOURCE:-/etc/track-anywhere/backend.env}

ssh "$HOST" "mkdir -p '$REMOTE_DIR/deploy/env'"
scp "$ROOT/compose.prod.yaml" "$HOST:$REMOTE_DIR/compose.prod.yaml"
scp "$ROOT/deploy/env/prod.env.example" "$HOST:$REMOTE_DIR/deploy/env/prod.env.example"

ssh "$HOST" "REMOTE_DIR='$REMOTE_DIR' ENV_SOURCE='$ENV_SOURCE' API_IMAGE='$API_IMAGE' WEB_IMAGE='$WEB_IMAGE' sh -s" <<'REMOTE'
set -eu

env_file="$REMOTE_DIR/deploy/env/prod.env"
if [ ! -f "$env_file" ]; then
  if [ -f "$ENV_SOURCE" ]; then
    cp "$ENV_SOURCE" "$env_file"
  else
    cp "$REMOTE_DIR/deploy/env/prod.env.example" "$env_file"
  fi
fi

ensure_env() {
  key=$1
  value=$2
  if ! grep -Eq "^${key}=" "$env_file"; then
    printf '%s=%s\n' "$key" "$value" >> "$env_file"
  fi
}

upsert_env() {
  key=$1
  value=$2
  tmp_file="${env_file}.tmp"
  awk -F= -v key="$key" '$1 != key' "$env_file" > "$tmp_file"
  mv "$tmp_file" "$env_file"
  printf '%s=%s\n' "$key" "$value" >> "$env_file"
}

first_origin=$(
  awk -F= '$1 == "TRACK_ANYWHERE_ALLOWED_ORIGINS" { print $2; exit }' "$env_file" \
    | cut -d, -f1 \
    | tr -d "[:space:]'\""
)
if [ -z "$first_origin" ]; then
  first_origin="https://ledger.example.com"
fi

public_base=$(
  awk -F= '$1 == "TRACK_ANYWHERE_PUBLIC_BASE_URL" { print $2; exit }' "$env_file" \
    | tr -d "[:space:]'\""
)
if [ -z "$public_base" ]; then
  public_base="$first_origin"
fi

upsert_env TRACK_ANYWHERE_PUBLIC_BASE_URL "$public_base"
upsert_env TRACK_ANYWHERE_API_IMAGE "$API_IMAGE"
upsert_env TRACK_ANYWHERE_WEB_IMAGE "$WEB_IMAGE"
ensure_env TRACK_ANYWHERE_TLS 1
ensure_env TRACK_ANYWHERE_KEY_PROVIDER 1
ensure_env TRACK_ANYWHERE_ENCRYPTED_VOLUME 1
ensure_env TRACK_ANYWHERE_BACKUP_DOC 1
ensure_env TRACK_ANYWHERE_CLAMAV_HOST clamav
ensure_env TRACK_ANYWHERE_CLAMAV_PORT 3310
ensure_env TRACK_ANYWHERE_API http://127.0.0.1:8000
ensure_env TRACK_ANYWHERE_SERVICE_URL http://127.0.0.1:8000
ensure_env TRACK_ANYWHERE_PROD_API_BIND 127.0.0.1
ensure_env TRACK_ANYWHERE_PROD_API_PORT 8000
ensure_env TRACK_ANYWHERE_PROD_WEB_BIND 127.0.0.1
ensure_env TRACK_ANYWHERE_PROD_WEB_PORT 3000
REMOTE
ssh "$HOST" "cd '$REMOTE_DIR' && docker compose --env-file deploy/env/prod.env -f compose.prod.yaml pull"
ssh "$HOST" "if systemctl list-unit-files track-anywhere-api.service >/dev/null 2>&1; then systemctl disable --now track-anywhere-api.service || true; fi"
ssh "$HOST" "if systemctl list-unit-files track-anywhere-web.service >/dev/null 2>&1; then systemctl disable --now track-anywhere-web.service || true; fi"
ssh "$HOST" "cd '$REMOTE_DIR' && docker compose --env-file deploy/env/prod.env -f compose.prod.yaml up -d --remove-orphans"
ssh "$HOST" "REMOTE_DIR='$REMOTE_DIR' API_IMAGE='$API_IMAGE' WEB_IMAGE='$WEB_IMAGE' sh -s" <<'REMOTE'
set -eu
cat > /usr/local/bin/ta <<EOF
#!/usr/bin/env sh
set -eu
cd "$REMOTE_DIR"
TRACK_ANYWHERE_API_IMAGE=\${TRACK_ANYWHERE_API_IMAGE:-$API_IMAGE} TRACK_ANYWHERE_WEB_IMAGE=\${TRACK_ANYWHERE_WEB_IMAGE:-$WEB_IMAGE} exec docker compose --env-file deploy/env/prod.env -f compose.prod.yaml run --rm cli "\$@"
EOF
chmod 0755 /usr/local/bin/ta
ln -sf /usr/local/bin/ta /usr/local/bin/track-anywhere
REMOTE
attempt=1
API_PORT=$(ssh "$HOST" "awk -F= '\$1 == \"TRACK_ANYWHERE_PROD_API_PORT\" { value = \$2 } END { print value }' '$REMOTE_DIR/deploy/env/prod.env'")
WEB_PORT=$(ssh "$HOST" "awk -F= '\$1 == \"TRACK_ANYWHERE_PROD_WEB_PORT\" { value = \$2 } END { print value }' '$REMOTE_DIR/deploy/env/prod.env'")
API_PORT=${API_PORT:-8000}
WEB_PORT=${WEB_PORT:-3000}

while ! ssh "$HOST" "curl -fsS http://127.0.0.1:$API_PORT/api/v1/health"; do
  if [ "$attempt" -ge 30 ]; then
    ssh "$HOST" "docker logs --tail 80 track-anywhere-prod-api" || true
    exit 1
  fi
  attempt=$((attempt + 1))
  sleep 2
done
attempt=1
while ! ssh "$HOST" "curl -fsS http://127.0.0.1:$WEB_PORT/api/v1/health"; do
  if [ "$attempt" -ge 30 ]; then
    ssh "$HOST" "docker logs --tail 80 track-anywhere-prod-web" || true
    exit 1
  fi
  attempt=$((attempt + 1))
  sleep 2
done
