#!/usr/bin/env sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
HOST=${1:-${TRACK_ANYWHERE_DEPLOY_HOST:-root@cc6}}
REMOTE_DIR=${TRACK_ANYWHERE_REMOTE_DIR:-/opt/track-anywhere-docker}
IMAGE=${TRACK_ANYWHERE_IMAGE:-ghcr.io/vibeanyorg/track-anywhere:latest}
ENV_SOURCE=${TRACK_ANYWHERE_REMOTE_ENV_SOURCE:-/etc/track-anywhere/backend.env}

ssh "$HOST" "mkdir -p '$REMOTE_DIR/deploy/env'"
scp "$ROOT/compose.prod.yaml" "$HOST:$REMOTE_DIR/compose.prod.yaml"
scp "$ROOT/deploy/env/prod.env.example" "$HOST:$REMOTE_DIR/deploy/env/prod.env.example"

ssh "$HOST" "if [ ! -f '$REMOTE_DIR/deploy/env/prod.env' ]; then if [ -f '$ENV_SOURCE' ]; then cp '$ENV_SOURCE' '$REMOTE_DIR/deploy/env/prod.env'; else cp '$REMOTE_DIR/deploy/env/prod.env.example' '$REMOTE_DIR/deploy/env/prod.env'; fi; fi"
ssh "$HOST" "cd '$REMOTE_DIR' && TRACK_ANYWHERE_IMAGE='$IMAGE' docker compose --env-file deploy/env/prod.env -f compose.prod.yaml pull"
ssh "$HOST" "if systemctl list-unit-files track-anywhere-api.service >/dev/null 2>&1; then systemctl disable --now track-anywhere-api.service || true; fi"
ssh "$HOST" "cd '$REMOTE_DIR' && TRACK_ANYWHERE_IMAGE='$IMAGE' docker compose --env-file deploy/env/prod.env -f compose.prod.yaml up -d"
ssh "$HOST" "curl -fsS http://127.0.0.1:\${TRACK_ANYWHERE_PROD_API_PORT:-8000}/api/v1/health"
