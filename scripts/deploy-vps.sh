#!/usr/bin/env sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
HOST=${1:-${TRACK_ANYWHERE_DEPLOY_HOST:-root@cc6}}
REMOTE_DIR=${TRACK_ANYWHERE_REMOTE_DIR:-/opt/track-anywhere-docker}
IMAGE=${TRACK_ANYWHERE_IMAGE:?set TRACK_ANYWHERE_IMAGE to an immutable image tag or digest}
ENV_SOURCE=${TRACK_ANYWHERE_REMOTE_ENV_SOURCE:-/etc/track-anywhere/backend.env}

ssh "$HOST" "mkdir -p '$REMOTE_DIR/deploy/env'"
scp "$ROOT/compose.prod.yaml" "$HOST:$REMOTE_DIR/compose.prod.yaml"
scp "$ROOT/deploy/env/prod.env.example" "$HOST:$REMOTE_DIR/deploy/env/prod.env.example"
scp "$ROOT/deploy/env/prod.migrate.env.example" \
  "$HOST:$REMOTE_DIR/deploy/env/prod.migrate.env.example"

ssh "$HOST" "REMOTE_DIR='$REMOTE_DIR' ENV_SOURCE='$ENV_SOURCE' IMAGE='$IMAGE' sh -s" <<'REMOTE'
set -eu

env_file="$REMOTE_DIR/deploy/env/prod.env"
migration_env="$REMOTE_DIR/deploy/env/prod.migrate.env"
if [ ! -f "$env_file" ]; then
  if [ -f "$ENV_SOURCE" ]; then
    cp "$ENV_SOURCE" "$env_file"
  else
    cp "$REMOTE_DIR/deploy/env/prod.env.example" "$env_file"
  fi
fi
if [ ! -f "$migration_env" ]; then
  printf 'Missing %s; create it from prod.migrate.env.example with the migrator DSN\n' \
    "$migration_env" >&2
  exit 2
fi

tmp_file="${env_file}.tmp"
awk -F= '$1 != "TRACK_ANYWHERE_IMAGE"' "$env_file" > "$tmp_file"
mv "$tmp_file" "$env_file"
printf 'TRACK_ANYWHERE_IMAGE=%s\n' "$IMAGE" >> "$env_file"
REMOTE

COMPOSE="cd '$REMOTE_DIR' && docker compose --env-file deploy/env/prod.env -f compose.prod.yaml"
ssh "$HOST" "$COMPOSE pull api migrate cli"
ssh "$HOST" "$COMPOSE --profile migrate run --rm migrate"
ssh "$HOST" "if systemctl list-unit-files track-anywhere-api.service >/dev/null 2>&1; then systemctl disable --now track-anywhere-api.service || true; fi"
ssh "$HOST" "$COMPOSE up -d --remove-orphans api"

ssh "$HOST" "REMOTE_DIR='$REMOTE_DIR' IMAGE='$IMAGE' sh -s" <<'REMOTE'
set -eu
cat > /usr/local/bin/ta <<EOF
#!/usr/bin/env sh
set -eu
cd "$REMOTE_DIR"
TRACK_ANYWHERE_IMAGE=\${TRACK_ANYWHERE_IMAGE:-$IMAGE} exec docker compose --env-file deploy/env/prod.env -f compose.prod.yaml run --rm cli "\$@"
EOF
chmod 0755 /usr/local/bin/ta
ln -sf /usr/local/bin/ta /usr/local/bin/track-anywhere
REMOTE

API_PORT=$(ssh "$HOST" "awk -F= '\$1 == \"TRACK_ANYWHERE_PROD_API_PORT\" { value = \$2 } END { print value }' '$REMOTE_DIR/deploy/env/prod.env'")
API_PORT=${API_PORT:-8000}
attempt=1
while ! ssh "$HOST" "curl -fsS http://127.0.0.1:$API_PORT/api/v2/ready >/dev/null && curl -fsS http://127.0.0.1:$API_PORT/ >/dev/null"; do
  if [ "$attempt" -ge 30 ]; then
    ssh "$HOST" "docker logs --tail 80 track-anywhere-prod-api" || true
    exit 1
  fi
  attempt=$((attempt + 1))
  sleep 2
done
