#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

SOURCE_COMMIT="${1:-}"
RUN_ID="${2:-}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
POSTGRES_IMAGE='postgres:17-alpine@sha256:742f40ea20b9ff2ff31db5458d127452988a2164df9e17441e191f3b72252193'
NODE_VERSION='22.23.1'
NODE_ARCHIVE="node-v${NODE_VERSION}-linux-x64.tar.xz"
NODE_SHA256='9749e988f437343b7fa832c69ded82a312e41a03116d766797ac14f6f9eee578'
UV_VERSION='0.11.16'
UV_ARCHIVE='uv-x86_64-unknown-linux-gnu.tar.gz'
UV_SHA256='74947fe2c03315cf07e82ab3acc703eddef01aba4d5232a98e4c6825ec116131'
LABEL_KEY='track-anywhere.validation'

if [[ ! "$SOURCE_COMMIT" =~ ^[0-9a-f]{40}$ ]]; then
  printf 'invalid source commit\n' >&2
  exit 2
fi
if ! RUN_ID_CANONICAL="$(python3 - "$RUN_ID" <<'PY'
import sys
from uuid import UUID
try:
    print(UUID(sys.argv[1]))
except ValueError:
    raise SystemExit(2) from None
PY
)" || [[ "$RUN_ID_CANONICAL" != "$RUN_ID" ]]; then
  printf 'invalid run id\n' >&2
  exit 2
fi

RUN_SHORT="${RUN_ID//-/}"
RUN_SHORT="${RUN_SHORT:0:12}"
LABEL_VALUE="v2-${RUN_ID}"
RUN_ROOT="$HOME/.cache/track-anywhere-validation/$RUN_ID"
REPO="$ROOT_DIR"
DOWNLOADS="$RUN_ROOT/downloads"
TOOLS_DIR="$RUN_ROOT/tools"
PG_NAME="ta-v2-pg-$RUN_SHORT"
PG_NETWORK="ta-v2-net-$RUN_SHORT"
PG_ENV_FILE="/dev/shm/ta-v2-pg-$RUN_SHORT.env"
PG_INIT_MOUNT="$RUN_ROOT/001-v2-roles.sh"
IMAGE_TAG="track-anywhere-api:v1-backfill-${SOURCE_COMMIT:0:12}-$RUN_SHORT"
IMAGE_IID_FILE="$RUN_ROOT/candidate-image.iid"
PG_CREATED=0
NETWORK_CREATED=0
STAGING_PROJECT=''
TASK14_PASS=0

if [[ -e "$RUN_ROOT" || -L "$RUN_ROOT" ]]; then
  printf 'run root already exists\n' >&2
  exit 2
fi
mkdir -p "$DOWNLOADS" "$TOOLS_DIR" "$RUN_ROOT/cache"
chmod 700 "$RUN_ROOT" "$DOWNLOADS" "$TOOLS_DIR" "$RUN_ROOT/cache"
unset DOCKER_HOST DOCKER_CONTEXT
DOCKER_ENDPOINT="$(docker context inspect "$(docker context show)" --format '{{.Endpoints.docker.Host}}')"
[[ "$DOCKER_ENDPOINT" == unix://* ]]
if [[ -e "$PG_ENV_FILE" || -L "$PG_ENV_FILE" ]]; then
  printf 'postgres environment path already exists\n' >&2
  exit 2
fi

printf 'phase=exact_checkout\n'
RESOLVED_COMMIT="$(git -C "$REPO" rev-parse --verify "$SOURCE_COMMIT^{commit}")"
[[ "$RESOLVED_COMMIT" == "$SOURCE_COMMIT" ]]
[[ "$(git -C "$REPO" rev-parse HEAD)" == "$SOURCE_COMMIT" ]]
[[ -z "$(git -C "$REPO" status --porcelain)" ]]
PG_INIT_SCRIPT="$REPO/docker/postgres/init/001-v2-roles.sh"
PG_INIT_COMMITTED_BLOB="$(git -C "$REPO" rev-parse "$SOURCE_COMMIT:docker/postgres/init/001-v2-roles.sh")"
PG_INIT_WORKTREE_BLOB="$(git -C "$REPO" hash-object "$PG_INIT_SCRIPT")"
[[ "$PG_INIT_WORKTREE_BLOB" == "$PG_INIT_COMMITTED_BLOB" ]]
# Keep the checkout file writable for mutation tests. The first PG17 container
# receives an exact-blob, run-scoped copy readable by its postgres uid.
cp "$PG_INIT_SCRIPT" "$PG_INIT_MOUNT"
chmod 0555 "$PG_INIT_MOUNT"
[[ "$(stat -c '%a' "$PG_INIT_MOUNT")" == '555' ]]
[[ "$(git -C "$REPO" hash-object "$PG_INIT_MOUNT")" == "$PG_INIT_COMMITTED_BLOB" ]]
[[ -z "$(git -C "$REPO" status --porcelain)" ]]

container_fingerprint() {
  docker ps -a --no-trunc --format '{{.ID}}|{{.Names}}|{{.Image}}' \
    | sort \
    | sha256sum \
    | awk '{print $1}'
}

control_plane_fingerprint() {
  local service_ids
  service_ids="$(docker service ls -q)"
  if [[ -z "$service_ids" ]]; then
    printf 'no-swarm-services'
    return 0
  fi
  # shellcheck disable=SC2086
  docker service inspect $service_ids \
    --format '{{.ID}}|{{.Version.Index}}|{{if .UpdateStatus}}{{.UpdateStatus.State}}{{else}}none{{end}}' \
    | sort \
    | sha256sum \
    | awk '{print $1}'
}

assert_control_plane_idle() {
  local build_clients bad_updates unconverged
  build_clients="$(ps -eo args= | awk '
    {
      executable = $1
      sub(/^.*\//, "", executable)
      if ((executable == "docker" && ($2 == "build" || $2 == "buildx")) ||
          executable == "buildctl" || executable == "buildx") {
        n++
      }
    }
    END {print n+0}
  ')"
  [[ "$build_clients" == '0' ]] || {
    printf 'another Docker build client is active\n' >&2
    return 1
  }
  bad_updates="$(docker service ls -q | while IFS= read -r service_id; do
    [[ -z "$service_id" ]] && continue
    docker service inspect "$service_id" --format '{{if .UpdateStatus}}{{.UpdateStatus.State}}{{else}}none{{end}}'
  done | awk '$1 != "none" && $1 != "completed" {n++} END {print n+0}')"
  [[ "$bad_updates" == '0' ]] || {
    printf 'a Swarm service update is active or unhealthy\n' >&2
    return 1
  }
  unconverged="$(docker service ls --format '{{.Replicas}}' | awk -F/ '$1 != $2 {n++} END {print n+0}')"
  [[ "$unconverged" == '0' ]] || {
    printf 'a Swarm service is not converged\n' >&2
    return 1
  }
}

resource_count() {
  local kind="$1"
  case "$kind" in
    container)
      docker ps -aq --filter "label=$LABEL_KEY=$LABEL_VALUE" | wc -l | tr -d ' '
      ;;
    network)
      docker network ls -q --filter "label=$LABEL_KEY=$LABEL_VALUE" | wc -l | tr -d ' '
      ;;
    volume)
      docker volume ls -q --filter "label=$LABEL_KEY=$LABEL_VALUE" | wc -l | tr -d ' '
      ;;
    *)
      return 2
      ;;
  esac
}

cleanup_staging() {
  if [[ -z "$STAGING_PROJECT" ]]; then
    return 0
  fi
  docker compose -p "$STAGING_PROJECT" -f "$REPO/compose.e2e.yaml" down -v --remove-orphans >/dev/null 2>&1 || true
  local id
  while IFS= read -r id; do
    [[ -z "$id" ]] || docker rm -f "$id" >/dev/null 2>&1 || true
  done < <(docker ps -aq --filter "label=com.docker.compose.project=$STAGING_PROJECT")
  while IFS= read -r id; do
    [[ -z "$id" ]] || docker network rm "$id" >/dev/null 2>&1 || true
  done < <(docker network ls -q --filter "label=com.docker.compose.project=$STAGING_PROJECT")
  while IFS= read -r id; do
    [[ -z "$id" ]] || docker volume rm "$id" >/dev/null 2>&1 || true
  done < <(docker volume ls -q --filter "label=com.docker.compose.project=$STAGING_PROJECT")
}

cleanup_pg() {
  if [[ "$PG_CREATED" == '1' ]]; then
    docker rm -f "$PG_NAME" >/dev/null 2>&1 || true
    PG_CREATED=0
  fi
  if [[ "$NETWORK_CREATED" == '1' ]]; then
    docker network rm "$PG_NETWORK" >/dev/null 2>&1 || true
    NETWORK_CREATED=0
  fi
  rm -f "$PG_ENV_FILE"
}

cleanup() {
  local code=$?
  trap - EXIT
  cleanup_staging
  cleanup_pg
  local containers networks volumes staging_containers staging_networks staging_volumes
  containers="$(resource_count container)"
  networks="$(resource_count network)"
  volumes="$(resource_count volume)"
  if [[ "$containers" != '0' || "$networks" != '0' || "$volumes" != '0' ]]; then
    printf 'task14_cleanup_failed containers=%s networks=%s volumes=%s\n' "$containers" "$networks" "$volumes" >&2
    code=1
  fi
  if [[ -n "$STAGING_PROJECT" ]]; then
    staging_containers="$(docker ps -aq --filter "label=com.docker.compose.project=$STAGING_PROJECT" | wc -l | tr -d ' ')"
    staging_networks="$(docker network ls -q --filter "label=com.docker.compose.project=$STAGING_PROJECT" | wc -l | tr -d ' ')"
    staging_volumes="$(docker volume ls -q --filter "label=com.docker.compose.project=$STAGING_PROJECT" | wc -l | tr -d ' ')"
    if [[ "$staging_containers" != '0' || "$staging_networks" != '0' || "$staging_volumes" != '0' ]]; then
      printf 'task14_staging_cleanup_failed containers=%s networks=%s volumes=%s\n' "$staging_containers" "$staging_networks" "$staging_volumes" >&2
      code=1
    fi
  fi
  if [[ "$code" -eq 0 && "$TASK14_PASS" != '1' ]]; then
    code=1
  fi
  exit "$code"
}
trap cleanup EXIT

if [[ "$(resource_count container)" != '0' || "$(resource_count network)" != '0' || "$(resource_count volume)" != '0' ]]; then
  printf 'run label is not clean\n' >&2
  exit 2
fi

assert_control_plane_idle
HOST_CONTAINERS_BEFORE="$(container_fingerprint)"
CONTROL_PLANE_BEFORE="$(control_plane_fingerprint)"
printf 'phase=toolchain\n'

curl -fsSLo "$DOWNLOADS/$NODE_ARCHIVE" "https://nodejs.org/dist/v${NODE_VERSION}/$NODE_ARCHIVE"
curl -fsSLo "$DOWNLOADS/SHASUMS256.txt" "https://nodejs.org/dist/v${NODE_VERSION}/SHASUMS256.txt"
NODE_ACTUAL="$(sha256sum "$DOWNLOADS/$NODE_ARCHIVE" | awk '{print $1}')"
[[ "$NODE_ACTUAL" == "$NODE_SHA256" ]]
(
  cd "$DOWNLOADS"
  grep "  $NODE_ARCHIVE\$" SHASUMS256.txt | sha256sum -c -
)
tar -xJf "$DOWNLOADS/$NODE_ARCHIVE" -C "$TOOLS_DIR"

gh release download "$UV_VERSION" --repo astral-sh/uv --pattern "$UV_ARCHIVE" --dir "$DOWNLOADS"
UV_ACTUAL="$(sha256sum "$DOWNLOADS/$UV_ARCHIVE" | awk '{print $1}')"
[[ "$UV_ACTUAL" == "$UV_SHA256" ]]
gh attestation verify "$DOWNLOADS/$UV_ARCHIVE" --repo astral-sh/uv >/dev/null
tar -xzf "$DOWNLOADS/$UV_ARCHIVE" -C "$TOOLS_DIR"

export PATH="$TOOLS_DIR/node-v${NODE_VERSION}-linux-x64/bin:$TOOLS_DIR/uv-x86_64-unknown-linux-gnu:$PATH"
export UV_PYTHON_DOWNLOADS=never
export UV_PYTHON=/usr/bin/python3
export UV_PROJECT_ENVIRONMENT="$RUN_ROOT/venv"
export UV_CACHE_DIR="$RUN_ROOT/cache/uv"
export npm_config_cache="$RUN_ROOT/cache/npm"
export NEXT_TELEMETRY_DISABLED=1
[[ "$(node --version)" == "v${NODE_VERSION}" ]]
[[ "$(uv --version)" == "uv ${UV_VERSION}"* ]]

printf 'phase=postgres17_full_verification\n'
POSTGRES_PASSWORD="$(openssl rand -hex 24)"
MIGRATOR_PASSWORD="$(openssl rand -hex 24)"
RUNTIME_PASSWORD="$(openssl rand -hex 24)"
OWNER_ROLE="ta_owner_$RUN_SHORT"
MIGRATOR_ROLE="ta_migrator_$RUN_SHORT"
RUNTIME_ROLE="ta_runtime_$RUN_SHORT"

{
  printf 'POSTGRES_DB=postgres\n'
  printf 'POSTGRES_USER=postgres\n'
  printf 'POSTGRES_PASSWORD=%s\n' "$POSTGRES_PASSWORD"
  printf 'TRACK_ANYWHERE_OWNER_ROLE=%s\n' "$OWNER_ROLE"
  printf 'TRACK_ANYWHERE_MIGRATOR_ROLE=%s\n' "$MIGRATOR_ROLE"
  printf 'TRACK_ANYWHERE_MIGRATOR_PASSWORD=%s\n' "$MIGRATOR_PASSWORD"
  printf 'TRACK_ANYWHERE_RUNTIME_ROLE=%s\n' "$RUNTIME_ROLE"
  printf 'TRACK_ANYWHERE_RUNTIME_PASSWORD=%s\n' "$RUNTIME_PASSWORD"
} >"$PG_ENV_FILE"
chmod 600 "$PG_ENV_FILE"

# The host test factory requires a loopback TCP URL. Docker 29 does not create
# a host port mapping for an internal-only network, so this dedicated bridge is
# isolated by its unique name/label while the published port stays on 127.0.0.1.
docker network create --driver bridge --label "$LABEL_KEY=$LABEL_VALUE" "$PG_NETWORK" >/dev/null
NETWORK_CREATED=1
[[ "$(docker network inspect "$PG_NETWORK" --format '{{.Internal}}')" == 'false' ]]
docker run -d \
  --pull never \
  --name "$PG_NAME" \
  --network "$PG_NETWORK" \
  --publish 127.0.0.1::5432 \
  --label "$LABEL_KEY=$LABEL_VALUE" \
  --tmpfs /var/lib/postgresql/data:rw,nosuid,noexec,size=2g \
  --env-file "$PG_ENV_FILE" \
  -v "$PG_INIT_MOUNT:/docker-entrypoint-initdb.d/001-v2-roles.sh:ro" \
  "$POSTGRES_IMAGE" >/dev/null
PG_CREATED=1
rm -f "$PG_ENV_FILE"

READY=0
for _ in $(seq 1 120); do
  if docker exec "$PG_NAME" pg_isready -h 127.0.0.1 -U postgres -d postgres >/dev/null 2>&1; then
    READY=1
    break
  fi
  sleep 1
done
if [[ "$READY" != '1' ]]; then
  PG_STATE_FILE="$RUN_ROOT/postgres-init-state.txt"
  PG_LOG_FILE="$RUN_ROOT/postgres-init.log"
  docker inspect "$PG_NAME" \
    --format 'exit_code={{.State.ExitCode}} oom={{.State.OOMKilled}} error={{.State.Error}}' \
    >"$PG_STATE_FILE" 2>&1 || true
  docker logs "$PG_NAME" 2>&1 \
    | sed -e "s/$POSTGRES_PASSWORD/[REDACTED]/g" \
          -e "s/$MIGRATOR_PASSWORD/[REDACTED]/g" \
          -e "s/$RUNTIME_PASSWORD/[REDACTED]/g" \
    >"$PG_LOG_FILE" || true
  chmod 600 "$PG_STATE_FILE" "$PG_LOG_FILE"
  printf 'postgres readiness failed; redacted diagnostics are in %s\n' "$RUN_ROOT" >&2
  exit 1
fi
PG_PORT_LINE="$(docker port "$PG_NAME" 5432/tcp)"
[[ "$PG_PORT_LINE" == 127.0.0.1:* ]]
PG_PORT="${PG_PORT_LINE##*:}"
[[ "$PG_PORT" =~ ^[0-9]+$ ]]

export TRACK_ANYWHERE_TEST_POSTGRES_ADMIN_URL="postgresql+psycopg://postgres:${POSTGRES_PASSWORD}@127.0.0.1:${PG_PORT}/postgres"
export TRACK_ANYWHERE_TEST_POSTGRES_MIGRATOR_BASE_URL="postgresql+psycopg://${MIGRATOR_ROLE}:${MIGRATOR_PASSWORD}@127.0.0.1:${PG_PORT}/postgres"
export TRACK_ANYWHERE_TEST_POSTGRES_RUNTIME_BASE_URL="postgresql+psycopg://${RUNTIME_ROLE}:${RUNTIME_PASSWORD}@127.0.0.1:${PG_PORT}/postgres"
export TRACK_ANYWHERE_OWNER_ROLE="$OWNER_ROLE"
export TRACK_ANYWHERE_MIGRATOR_ROLE="$MIGRATOR_ROLE"
export TRACK_ANYWHERE_RUNTIME_ROLE="$RUNTIME_ROLE"
(
  cd "$REPO"
  bash scripts/verify-v2.sh
)

REMAINING_DATABASES="$(docker exec "$PG_NAME" psql -U postgres -d postgres --tuples-only --no-align --set ON_ERROR_STOP=1 --command "select count(*) from pg_database where left(datname, 6) = 'ta_v2_'")"
[[ "$REMAINING_DATABASES" == '0' ]]
unset TRACK_ANYWHERE_TEST_POSTGRES_ADMIN_URL TRACK_ANYWHERE_TEST_POSTGRES_MIGRATOR_BASE_URL TRACK_ANYWHERE_TEST_POSTGRES_RUNTIME_BASE_URL
unset POSTGRES_PASSWORD MIGRATOR_PASSWORD RUNTIME_PASSWORD
cleanup_pg
[[ "$(resource_count container)" == '0' && "$(resource_count network)" == '0' && "$(resource_count volume)" == '0' ]]

printf 'phase=do_only_image_build\n'
assert_control_plane_idle
[[ "$(container_fingerprint)" == "$HOST_CONTAINERS_BEFORE" ]]
[[ "$(control_plane_fingerprint)" == "$CONTROL_PLANE_BEFORE" ]]
[[ ! -e "$IMAGE_IID_FILE" && ! -L "$IMAGE_IID_FILE" ]]
(
  cd "$REPO"
  git archive "$SOURCE_COMMIT" | docker build --pull --target api-runtime \
    --iidfile "$IMAGE_IID_FILE" \
    --label "org.opencontainers.image.revision=$SOURCE_COMMIT" \
    --tag "$IMAGE_TAG" -
)
chmod 600 "$IMAGE_IID_FILE"
IMAGE_IID_SIZE="$(wc -c <"$IMAGE_IID_FILE" | tr -d ' ')"
if [[ "$IMAGE_IID_SIZE" != '71' && "$IMAGE_IID_SIZE" != '72' ]]; then
  printf 'invalid candidate image id file length\n' >&2
  exit 1
fi
IMAGE_ID="$(<"$IMAGE_IID_FILE")"
if [[ ! "$IMAGE_ID" =~ ^sha256:[0-9a-f]{64}$ ]]; then
  printf 'invalid candidate image id\n' >&2
  exit 1
fi
IMAGE_REVISION="$(docker image inspect "$IMAGE_ID" --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}')"
TAG_ID="$(docker image inspect "$IMAGE_TAG" --format '{{.Id}}')"
if [[ "$IMAGE_REVISION" != "$SOURCE_COMMIT" || "$TAG_ID" != "$IMAGE_ID" ]]; then
  printf 'candidate image provenance mismatch\n' >&2
  exit 1
fi

printf 'phase=isolated_staging\n'
# Compose bind-mounts the checkout path. All mutation tests have completed, so
# make only this exact tracked blob readable/executable inside the PG container.
[[ "$(git -C "$REPO" hash-object "$PG_INIT_SCRIPT")" == "$PG_INIT_COMMITTED_BLOB" ]]
chmod 0555 "$PG_INIT_SCRIPT"
[[ "$(stat -c '%a' "$PG_INIT_SCRIPT")" == '555' ]]
[[ "$(git -C "$REPO" hash-object "$PG_INIT_SCRIPT")" == "$PG_INIT_COMMITTED_BLOB" ]]
[[ -z "$(git -C "$REPO" status --porcelain)" ]]
STAGING_RUN_ID="$(python3 - <<'PY'
from uuid import uuid4
print(uuid4())
PY
)"
STAGING_PROJECT="track-anywhere-v2-staging-${STAGING_RUN_ID//-/}"
STAGING_REPORT="$REPO/output/v2-staging-$SOURCE_COMMIT-$STAGING_RUN_ID"
STAGING_SUFFIX="${STAGING_RUN_ID//-/}"
STAGING_SUFFIX="${STAGING_SUFFIX:0:12}"
export TRACK_ANYWHERE_E2E_API_IMAGE="$IMAGE_ID"
export TRACK_ANYWHERE_POSTGRES_IMAGE="$POSTGRES_IMAGE"
export TRACK_ANYWHERE_OWNER_ROLE="ta_stage_owner_$STAGING_SUFFIX"
export TRACK_ANYWHERE_MIGRATOR_ROLE="ta_stage_migrator_$STAGING_SUFFIX"
export TRACK_ANYWHERE_RUNTIME_ROLE="ta_stage_runtime_$STAGING_SUFFIX"
export TRACK_ANYWHERE_MIGRATOR_PASSWORD="$(openssl rand -hex 24)"
export TRACK_ANYWHERE_RUNTIME_PASSWORD="$(openssl rand -hex 24)"
(
  cd "$REPO"
  bash scripts/staging-v2-smoke.sh \
    --source-commit "$SOURCE_COMMIT" \
    --run-id "$STAGING_RUN_ID" \
    --report-dir "$STAGING_REPORT"
)
unset TRACK_ANYWHERE_MIGRATOR_PASSWORD TRACK_ANYWHERE_RUNTIME_PASSWORD

POSTGRES_IMAGE_ID="$(docker image inspect "$POSTGRES_IMAGE" --format '{{.Id}}')"
python3 - "$STAGING_REPORT/verification.json" "$SOURCE_COMMIT" "$IMAGE_ID" "$POSTGRES_IMAGE" "$POSTGRES_IMAGE_ID" "$STAGING_RUN_ID" "$TRACK_ANYWHERE_OWNER_ROLE" "$TRACK_ANYWHERE_MIGRATOR_ROLE" "$TRACK_ANYWHERE_RUNTIME_ROLE" <<'PY'
import json
import sys
from pathlib import Path

(
    path,
    source_commit,
    image_id,
    postgres_image,
    postgres_image_id,
    run_id,
    owner_role,
    migrator_role,
    runtime_role,
) = sys.argv[1:]
report = json.loads(Path(path).read_text(encoding="utf-8"))
assert report["status"] == "PASS"
assert report["stage"] == "complete"
assert report["source_commit"] == source_commit
assert report["run_id"] == run_id
assert report["production_deploy"] == "NOT_PERFORMED"
assert report["images"]["api"]["reference"] == image_id
assert report["images"]["api"]["content_digest"] == image_id
assert report["images"]["api"]["revision"] == source_commit
assert report["images"]["postgres"]["reference"] == postgres_image
assert report["images"]["postgres"]["content_digest"] == postgres_image_id
version = int(report["postgres_server_version_num"])
assert 170000 <= version < 180000
assert report["alembic_head"] == "v2_0013_frozen_import_fence"
assert report["database_owner"] == owner_role
assert report["roles"] == {"migrator": migrator_role, "runtime": runtime_role}
assert report["checks"]["runtime_cannot_update_events"] == "PASS"
assert report["checks"]["runtime_cannot_disable_triggers"] == "PASS"
assert report["checks"]["legacy_route_http_status"] == "404"
assert report["checks"]["public_app_health"] == {"api_version": "v2", "status": "ok"}
assert report["runtime_smoke"]["fresh_connection_balance_visibility"] is True
assert report["projection"]["status"] == "PASS"
assert report["projection"]["projection_lag"] == 0
assert report["independent_verifier"]["status"] == "PASS"
print("staging_report=PASS")
PY

cleanup_staging
STAGING_CONTAINERS="$(docker ps -aq --filter "label=com.docker.compose.project=$STAGING_PROJECT" | wc -l | tr -d ' ')"
STAGING_NETWORKS="$(docker network ls -q --filter "label=com.docker.compose.project=$STAGING_PROJECT" | wc -l | tr -d ' ')"
STAGING_VOLUMES="$(docker volume ls -q --filter "label=com.docker.compose.project=$STAGING_PROJECT" | wc -l | tr -d ' ')"
[[ "$STAGING_CONTAINERS" == '0' && "$STAGING_NETWORKS" == '0' && "$STAGING_VOLUMES" == '0' ]]

assert_control_plane_idle
HOST_CONTAINERS_AFTER="$(container_fingerprint)"
CONTROL_PLANE_AFTER="$(control_plane_fingerprint)"
[[ "$HOST_CONTAINERS_AFTER" == "$HOST_CONTAINERS_BEFORE" ]]
[[ "$CONTROL_PLANE_AFTER" == "$CONTROL_PLANE_BEFORE" ]]

STATE_FILE="$RUN_ROOT/task14-state.env"
{
  printf 'SOURCE_COMMIT=%q\n' "$SOURCE_COMMIT"
  printf 'RUN_ID=%q\n' "$RUN_ID"
  printf 'RUN_ROOT=%q\n' "$RUN_ROOT"
  printf 'REPO=%q\n' "$REPO"
  printf 'IMAGE_ID=%q\n' "$IMAGE_ID"
  printf 'IMAGE_TAG=%q\n' "$IMAGE_TAG"
  printf 'STAGING_RUN_ID=%q\n' "$STAGING_RUN_ID"
  printf 'HOST_CONTAINERS_FINGERPRINT=%q\n' "$HOST_CONTAINERS_BEFORE"
  printf 'CONTROL_PLANE_FINGERPRINT=%q\n' "$CONTROL_PLANE_BEFORE"
} >"$STATE_FILE"
chmod 600 "$STATE_FILE"

TASK14_PASS=1
printf 'TASK14_PASS source_commit=%s image_id=%s staging_run_id=%s run_root=%s\n' "$SOURCE_COMMIT" "$IMAGE_ID" "$STAGING_RUN_ID" "$RUN_ROOT"
