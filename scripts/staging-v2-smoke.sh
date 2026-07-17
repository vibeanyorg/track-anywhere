#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="$ROOT_DIR/compose.e2e.yaml"
SOURCE_COMMIT=""
RUN_ID=""
REPORT_ARGUMENT=""
LEGACY_API_PATH='/api/'"v1"

usage() {
  printf 'usage: %s --source-commit SHA --run-id UUID --report-dir DIR\n' "$0" >&2
}

while (( $# )); do
  case "$1" in
    --source-commit)
      [[ $# -ge 2 ]] || { usage; exit 2; }
      SOURCE_COMMIT="$2"
      shift 2
      ;;
    --run-id)
      [[ $# -ge 2 ]] || { usage; exit 2; }
      RUN_ID="$2"
      shift 2
      ;;
    --report-dir)
      [[ $# -ge 2 ]] || { usage; exit 2; }
      REPORT_ARGUMENT="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      printf 'unknown argument: %s\n' "$1" >&2
      usage
      exit 2
      ;;
  esac
done

if [[ -z "$SOURCE_COMMIT" || -z "$RUN_ID" || -z "$REPORT_ARGUMENT" ]]; then
  printf '%s\n' '--source-commit, --run-id, and --report-dir are required' >&2
  exit 2
fi
if [[ ! "$SOURCE_COMMIT" =~ ^[0-9a-f]{40}$ ]]; then
  printf 'source commit must be a lowercase 40-character Git SHA\n' >&2
  exit 2
fi
CANONICAL_RUN_ID="$(python3 - "$RUN_ID" <<'PY'
import sys
from uuid import UUID

try:
    value = UUID(sys.argv[1])
except ValueError:
    raise SystemExit(2) from None
print(value)
PY
)" || {
  printf 'run ID must be a canonical UUID\n' >&2
  exit 2
}
if [[ "$CANONICAL_RUN_ID" != "$RUN_ID" ]]; then
  printf 'run ID must be a canonical lowercase UUID\n' >&2
  exit 2
fi

EXPECTED_BASENAME="v2-staging-$SOURCE_COMMIT-$RUN_ID"
if [[ "$REPORT_ARGUMENT" == "$ROOT_DIR/"* ]]; then
  REPORT_RELATIVE="${REPORT_ARGUMENT#"$ROOT_DIR/"}"
else
  REPORT_RELATIVE="$REPORT_ARGUMENT"
fi
if [[ "$REPORT_RELATIVE" != "output/$EXPECTED_BASENAME" ]]; then
  printf 'report directory must be output/%s\n' "$EXPECTED_BASENAME" >&2
  exit 2
fi
REPORT_DIR="$ROOT_DIR/$REPORT_RELATIVE"
if [[ -e "$REPORT_DIR" || -L "$REPORT_DIR" ]]; then
  printf 'staging report directory must not already exist\n' >&2
  exit 2
fi
if [[ -L "$ROOT_DIR/output" ]]; then
  printf 'output must not be a symbolic link\n' >&2
  exit 2
fi

: "${TRACK_ANYWHERE_E2E_API_IMAGE:?TRACK_ANYWHERE_E2E_API_IMAGE is required}"
API_IMAGE="$TRACK_ANYWHERE_E2E_API_IMAGE"

RESOLVED_COMMIT="$(git -C "$ROOT_DIR" rev-parse --verify "$SOURCE_COMMIT^{commit}")"
if [[ "$RESOLVED_COMMIT" != "$SOURCE_COMMIT" ]]; then
  printf 'source commit does not resolve exactly\n' >&2
  exit 2
fi
if [[ "$(git -C "$ROOT_DIR" rev-parse HEAD)" != "$SOURCE_COMMIT" ]]; then
  printf 'source commit must equal the checked-out HEAD\n' >&2
  exit 2
fi
if [[ -n "$(git -C "$ROOT_DIR" status --porcelain --untracked-files=no)" ]]; then
  printf 'tracked worktree must be clean before staging validation\n' >&2
  exit 2
fi

HARNESS_SOURCE_PATHS=(
  .dockerignore
  compose.e2e.yaml
  docker/postgres/init/001-v2-roles.sh
  scripts/lib/e2e-harness-common.sh
  scripts/staging-v2-smoke.sh
  scripts/e2e-docker-postgres.sh
  docs/operations/v2-isolated-staging-checklist.md
  backend/tests/v2/unit/test_staging_harness.py
)
for harness_path in "${HARNESS_SOURCE_PATHS[@]}"; do
  if ! COMMITTED_BLOB="$(git -C "$ROOT_DIR" rev-parse "$SOURCE_COMMIT:$harness_path" 2>/dev/null)"; then
    printf 'source commit must contain tracked harness file: %s\n' "$harness_path" >&2
    exit 2
  fi
  WORKTREE_BLOB="$(git -C "$ROOT_DIR" hash-object "$ROOT_DIR/$harness_path")"
  if [[ "$WORKTREE_BLOB" != "$COMMITTED_BLOB" ]]; then
    printf 'harness file differs from source commit: %s\n' "$harness_path" >&2
    exit 2
  fi
done

# shellcheck source=scripts/lib/e2e-harness-common.sh
source "$ROOT_DIR/scripts/lib/e2e-harness-common.sh"

PROJECT_NAME="track-anywhere-v2-staging-${RUN_ID//-/}"
MIGRATION_CONTAINER="$PROJECT_NAME-migrate-proof"
export TRACK_ANYWHERE_E2E_PROJECT="$PROJECT_NAME"
export TRACK_ANYWHERE_E2E_API_BIND=127.0.0.1
export TRACK_ANYWHERE_E2E_POSTGRES_BIND=127.0.0.1
export TRACK_ANYWHERE_E2E_API_PORT="$(ta_pick_loopback_port)"
export TRACK_ANYWHERE_E2E_POSTGRES_PORT="$(ta_pick_loopback_port)"
export TRACK_ANYWHERE_E2E_NO_BUILD=1
export TRACK_ANYWHERE_E2E_EXISTING_STACK=1
POSTGRES_IMAGE_REFERENCE="${TRACK_ANYWHERE_POSTGRES_IMAGE:-postgres:17-alpine@sha256:742f40ea20b9ff2ff31db5458d127452988a2164df9e17441e191f3b72252193}"
export TRACK_ANYWHERE_POSTGRES_IMAGE="$POSTGRES_IMAGE_REFERENCE"

OWNER_ROLE="${TRACK_ANYWHERE_OWNER_ROLE:-track_anywhere_owner}"
MIGRATOR_ROLE="${TRACK_ANYWHERE_MIGRATOR_ROLE:-track_anywhere_migrator}"
MIGRATOR_PASSWORD="${TRACK_ANYWHERE_MIGRATOR_PASSWORD:-track_anywhere_migrator_test}"
RUNTIME_ROLE="${TRACK_ANYWHERE_RUNTIME_ROLE:-track_anywhere_runtime}"
RUNTIME_PASSWORD="${TRACK_ANYWHERE_RUNTIME_PASSWORD:-track_anywhere_runtime_test}"

ta_require_postgres_identifier "$OWNER_ROLE" "owner role"
ta_require_postgres_identifier "$MIGRATOR_ROLE" "migrator role"
ta_require_postgres_identifier "$RUNTIME_ROLE" "runtime role"
API_URL="http://127.0.0.1:$TRACK_ANYWHERE_E2E_API_PORT"
export TRACK_ANYWHERE_E2E_PUBLIC_BASE_URL="$API_URL"
COMPOSE=(docker compose -p "$PROJECT_NAME" -f "$COMPOSE_FILE")
DOCKER_TIMEOUT="${TRACK_ANYWHERE_DOCKER_CLI_TIMEOUT_SECONDS:-60}"
COMPOSE_TIMEOUT="${TRACK_ANYWHERE_DOCKER_COMPOSE_TIMEOUT_SECONDS:-900}"

STAGE=validated_arguments
REPORT_CREATED=0
COMPOSE_STARTED=0
PASS_WRITTEN=0
MIGRATION_CONTAINER_CREATED=0
API_IMAGE_ID=""
POSTGRES_IMAGE_ID=""
RUNNING_POSTGRES_IMAGE_ID=""
API_REVISION=""
API_REPO_DIGESTS=""
POSTGRES_VERSION=""
RUNTIME_IDENTITY=""
MIGRATOR_IDENTITY=""
DATABASE_OWNER=""
ALEMBIC_HEAD=""
runtime_cannot_update_events=NOT_RUN
runtime_cannot_disable_triggers=NOT_RUN
LEGACY_ROUTE_STATUS=""
E2E_RESULT="$REPORT_DIR/e2e-result.json"
PROJECTION_RESULT="$REPORT_DIR/projection.json"
VERIFIER_RESULT="$REPORT_DIR/independent-verifier.json"
APP_HEALTH_RESULT="$REPORT_DIR/app-health.json"

write_verification() {
  local status="$1"
  local destination="$REPORT_DIR/verification.json"
  local temporary="$destination.tmp"
  python3 - \
    "$temporary" "$status" "$SOURCE_COMMIT" "$RUN_ID" "$STAGE" \
    "$API_IMAGE" "$API_IMAGE_ID" "$API_REVISION" "$API_REPO_DIGESTS" \
    "$POSTGRES_IMAGE_REFERENCE" "$RUNNING_POSTGRES_IMAGE_ID" \
    "$POSTGRES_VERSION" "$RUNTIME_IDENTITY" "$MIGRATOR_IDENTITY" \
    "$DATABASE_OWNER" "$ALEMBIC_HEAD" "$E2E_RESULT" \
    "$PROJECTION_RESULT" "$VERIFIER_RESULT" "$APP_HEALTH_RESULT" \
    "$runtime_cannot_update_events" "$runtime_cannot_disable_triggers" \
    "$LEGACY_ROUTE_STATUS" <<'PY'
import json
import sys
from pathlib import Path

(
    destination,
    status,
    source_commit,
    run_id,
    stage,
    api_image,
    api_image_id,
    api_revision,
    api_repo_digests,
    postgres_image_reference,
    postgres_image_id,
    postgres_version,
    runtime_identity,
    migrator_identity,
    database_owner,
    alembic_head,
    e2e_path,
    projection_path,
    verifier_path,
    app_health_path,
    runtime_update_probe,
    runtime_trigger_probe,
    legacy_route_status,
) = sys.argv[1:]

def optional_json(path: str):
    candidate = Path(path)
    if not candidate.is_file():
        return None
    try:
        return json.loads(candidate.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {"status": "INCOMPLETE"}

def docker_json(value: str):
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []

report = {
    "alembic_head": alembic_head or None,
    "database_owner": database_owner or None,
    "images": {
        "api": {
            "content_digest": api_image_id or None,
            "reference": api_image,
            "repo_digests": docker_json(api_repo_digests),
            "revision": api_revision or None,
        },
        "postgres": {
            "content_digest": postgres_image_id or None,
            "reference": postgres_image_reference,
        },
    },
    "checks": {
        "legacy_route_http_status": legacy_route_status or None,
        "runtime_cannot_disable_triggers": runtime_trigger_probe,
        "runtime_cannot_update_events": runtime_update_probe,
        "public_app_health": optional_json(app_health_path),
    },
    "independent_verifier": optional_json(verifier_path),
    "postgres_server_version_num": postgres_version or None,
    "production_deploy": "NOT_PERFORMED",
    "projection": optional_json(projection_path),
    "run_id": run_id,
    "runtime_smoke": optional_json(e2e_path),
    "roles": {
        "migrator": migrator_identity or None,
        "runtime": runtime_identity or None,
    },
    "source_commit": source_commit,
    "stage": stage,
    "status": status,
}
Path(destination).write_text(
    json.dumps(report, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
PY
  mv -f "$temporary" "$destination"
}

redact_diagnostics() {
  sed -E \
    -e 's#(postgresql(\+psycopg)?://[^:/[:space:]]+:)[^@/[:space:]]+@#\1[REDACTED]@#g' \
    -e 's#(authorization: bearer )[A-Za-z0-9._-]+#\1[REDACTED]#Ig'
}

teardown_resources_strict() {
  local failed=0
  if [[ "$COMPOSE_STARTED" != "1" ]]; then
    return 0
  fi
  if [[ "$MIGRATION_CONTAINER_CREATED" == "1" ]]; then
    if ! ta_run_with_timeout "$DOCKER_TIMEOUT" \
      docker rm -f "$MIGRATION_CONTAINER" >/dev/null 2>&1; then
      failed=1
    fi
  fi
  if ! ta_run_with_timeout "$DOCKER_TIMEOUT" \
    "${COMPOSE[@]}" down -v --remove-orphans >/dev/null 2>&1; then
    failed=1
  fi
  if [[ "$failed" != "0" ]]; then
    return 1
  fi
  COMPOSE_STARTED=0
  MIGRATION_CONTAINER_CREATED=0
}

cleanup() {
  local exit_code=$?
  trap - EXIT
  if [[ "$exit_code" -eq 0 && "$PASS_WRITTEN" != "1" ]]; then
    exit_code=1
  fi
  if [[ "$REPORT_CREATED" == "1" && "$exit_code" -ne 0 ]]; then
    if [[ "$COMPOSE_STARTED" == "1" ]]; then
      ta_run_with_timeout "$DOCKER_TIMEOUT" \
        "${COMPOSE[@]}" logs --no-color postgres migrate api 2>&1 \
        | redact_diagnostics >"$REPORT_DIR/diagnostics.log" || true
    fi
    write_verification "FAIL" || true
  fi
  if [[ "$COMPOSE_STARTED" == "1" ]]; then
    ta_run_with_timeout "$DOCKER_TIMEOUT" \
      docker rm -f "$MIGRATION_CONTAINER" >/dev/null 2>&1 || true
    ta_run_with_timeout "$DOCKER_TIMEOUT" \
      "${COMPOSE[@]}" down -v --remove-orphans >/dev/null 2>&1 || true
  fi
  exit "$exit_code"
}

# The EXIT trap is installed before the run directory or any Docker resource exists.
trap cleanup EXIT
mkdir -p "$ROOT_DIR/output"
mkdir "$REPORT_DIR"
REPORT_CREATED=1

STAGE=image_preflight
if [[ -n "${DOCKER_HOST:-}" ]]; then
  DOCKER_ENDPOINT="$DOCKER_HOST"
else
  DOCKER_CONTEXT_NAME="$(ta_run_with_timeout "$DOCKER_TIMEOUT" docker context show)"
  DOCKER_ENDPOINT="$(ta_run_with_timeout "$DOCKER_TIMEOUT" \
    docker context inspect "$DOCKER_CONTEXT_NAME" --format '{{.Endpoints.docker.Host}}')"
fi
case "$DOCKER_ENDPOINT" in
  unix://*|npipe://*) ;;
  *)
    printf 'staging requires a local Docker endpoint, got %s\n' "$DOCKER_ENDPOINT" >&2
    exit 1
    ;;
esac
ta_run_with_timeout "$DOCKER_TIMEOUT" docker version --format '{{.Server.Version}}' >/dev/null
API_IMAGE_ID="$(ta_run_with_timeout "$DOCKER_TIMEOUT" docker image inspect "$API_IMAGE" --format '{{.Id}}')"
POSTGRES_IMAGE_ID="$(ta_run_with_timeout "$DOCKER_TIMEOUT" docker image inspect "$POSTGRES_IMAGE_REFERENCE" --format '{{.Id}}')"
API_REVISION="$(ta_run_with_timeout "$DOCKER_TIMEOUT" docker image inspect "$API_IMAGE" --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}')"
API_REPO_DIGESTS="$(ta_run_with_timeout "$DOCKER_TIMEOUT" docker image inspect "$API_IMAGE" --format '{{json .RepoDigests}}')"
for image_id in "$API_IMAGE_ID" "$POSTGRES_IMAGE_ID"; do
  if [[ ! "$image_id" =~ ^sha256:[0-9a-f]{64}$ ]]; then
    printf 'image content digest is not a sha256 image ID\n' >&2
    exit 1
  fi
done
if [[ "$API_REVISION" != "$SOURCE_COMMIT" ]]; then
  printf 'image revision label does not match source commit\n' >&2
  exit 1
fi
CONFIG_IMAGES="$(ta_run_with_timeout "$DOCKER_TIMEOUT" "${COMPOSE[@]}" config --images)"
grep -Fxq "$API_IMAGE" <<<"$CONFIG_IMAGES"
grep -Fxq "$POSTGRES_IMAGE_REFERENCE" <<<"$CONFIG_IMAGES"

STAGE=postgres_start
COMPOSE_STARTED=1
ta_run_with_timeout "$COMPOSE_TIMEOUT" \
  "${COMPOSE[@]}" up -d --no-build --pull never --wait postgres
POSTGRES_CONTAINER="$("${COMPOSE[@]}" ps -q postgres)"
[[ -n "$POSTGRES_CONTAINER" ]] || {
  printf 'PostgreSQL container must be running\n' >&2
  exit 1
}
RUNNING_POSTGRES_IMAGE_ID="$(ta_run_with_timeout "$DOCKER_TIMEOUT" \
  docker inspect "$POSTGRES_CONTAINER" --format '{{.Image}}')"
if [[ "$RUNNING_POSTGRES_IMAGE_ID" != "$POSTGRES_IMAGE_ID" ]]; then
  printf 'running PostgreSQL image ID differs from the validated image\n' >&2
  exit 1
fi
ta_initialize_database_owner "$DOCKER_TIMEOUT" "$OWNER_ROLE" "${COMPOSE[@]}"

STAGE=clean_migration
ta_run_with_timeout "$COMPOSE_TIMEOUT" \
  "${COMPOSE[@]}" run --pull never --name "$MIGRATION_CONTAINER" --no-deps -T migrate
MIGRATION_CONTAINER_CREATED=1
MIGRATION_IMAGE_ID="$(ta_run_with_timeout "$DOCKER_TIMEOUT" docker inspect "$MIGRATION_CONTAINER" --format '{{.Image}}')"
if [[ "$MIGRATION_IMAGE_ID" != "$API_IMAGE_ID" ]]; then
  printf 'migration container did not use the exact API image\n' >&2
  exit 1
fi

STAGE=runtime_seed
TRACK_ANYWHERE_E2E_RAW_API_KEY=ta_v2_local_e2e \
  ta_run_with_timeout "$COMPOSE_TIMEOUT" \
  "${COMPOSE[@]}" run --pull never --rm --no-deps -T \
  -e TRACK_ANYWHERE_E2E_RAW_API_KEY api python - <<'PY'
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import os
from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from track_anywhere.infrastructure.db.models.auth import CredentialRecord, UserRecord

raw_api_key = os.environ["TRACK_ANYWHERE_E2E_RAW_API_KEY"]
engine = create_engine(os.environ["TRACK_ANYWHERE_DATABASE_URL"], pool_pre_ping=True)
now = datetime.now(UTC)
try:
    with Session(engine) as session, session.begin():
        session.add(
            UserRecord(
                user_id="human:local-e2e",
                subject_type="human",
                current_display_name="Local E2E",
                status="active",
            )
        )
        session.flush()
        session.add(
            CredentialRecord(
                credential_id=uuid4(),
                token_hash=sha256(raw_api_key.encode()).digest(),
                jti=uuid4(),
                actor_subject_id="human:local-e2e",
                actor_type="human",
                auth_kind="api_key",
                book_id=None,
                scopes=[
                    "book:read", "book:write", "ledger:read", "ledger:write",
                ],
                issued_at=now - timedelta(minutes=1),
                expires_at=now + timedelta(hours=1),
                revoked_at=None,
                last_used_at=None,
            )
        )
finally:
    engine.dispose()
PY

STAGE=runtime_start
ta_run_with_timeout "$COMPOSE_TIMEOUT" \
  "${COMPOSE[@]}" up -d --no-build --pull never --wait api
API_CONTAINER="$("${COMPOSE[@]}" ps -q api)"
if [[ -z "$API_CONTAINER" ]]; then
  printf 'application container must be running\n' >&2
  exit 1
fi
if [[ "$(ta_run_with_timeout "$DOCKER_TIMEOUT" docker inspect "$API_CONTAINER" --format '{{.Image}}')" != "$API_IMAGE_ID" ]]; then
  printf 'running API image ID differs from the validated image\n' >&2
  exit 1
fi
if [[ "$(ta_run_with_timeout "$DOCKER_TIMEOUT" docker inspect "$API_CONTAINER" --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}')" != "$SOURCE_COMMIT" ]]; then
  printf 'running API revision label differs from source commit\n' >&2
  exit 1
fi
STAGE=app_smoke
APP_HEALTH_METADATA="$(curl --connect-timeout 3 --max-time 30 -sS \
  -o "$APP_HEALTH_RESULT" -w '%{http_code}|%{content_type}' \
  "$API_URL/api/v2/health")"
APP_HEALTH_STATUS="${APP_HEALTH_METADATA%%|*}"
APP_HEALTH_CONTENT_TYPE="${APP_HEALTH_METADATA#*|}"
if [[ "$APP_HEALTH_STATUS" != "200" ]]; then
  printf 'public app health must return HTTP 200 without redirect, got %s\n' \
    "$APP_HEALTH_STATUS" >&2
  exit 1
fi
python3 - "$APP_HEALTH_RESULT" "$APP_HEALTH_CONTENT_TYPE" <<'PY'
import json
import sys

media_type = sys.argv[2].partition(";")[0].strip().casefold()
if media_type != "application/json":
    raise SystemExit("public app health content type must be application/json")
try:
    payload = json.load(open(sys.argv[1], encoding="utf-8"))
except (OSError, UnicodeError, json.JSONDecodeError):
    raise SystemExit("public app health payload must be exact V2 JSON") from None
if payload != {"api_version": "v2", "status": "ok"}:
    raise SystemExit("public app health payload must be exact V2 JSON")
PY

STAGE=api_cli_and_fresh_connection_smoke
TRACK_ANYWHERE_E2E_RESULT_FILE="$E2E_RESULT" \
TRACK_ANYWHERE_E2E_API_IMAGE="$API_IMAGE" \
TRACK_ANYWHERE_E2E_NO_BUILD=1 \
TRACK_ANYWHERE_E2E_EXISTING_STACK=1 \
  bash "$ROOT_DIR/scripts/e2e-docker-postgres.sh"
BOOK_ID="$(python3 - "$E2E_RESULT" <<'PY'
import json
import sys
print(json.load(open(sys.argv[1], encoding="utf-8"))["book_id"])
PY
)"

psql_as() {
  local role="$1"
  local password="$2"
  local statement="$3"
  ta_run_with_timeout "$DOCKER_TIMEOUT" \
    "${COMPOSE[@]}" exec -T -e "PGPASSWORD=$password" postgres \
    psql -h 127.0.0.1 -U "$role" -d track_anywhere \
    --set ON_ERROR_STOP=1 --tuples-only --no-align --field-separator='|' \
    --command "$statement"
}

STAGE=database_identity_and_head
POSTGRES_VERSION="$(psql_as "$RUNTIME_ROLE" "$RUNTIME_PASSWORD" 'show server_version_num')"
if (( POSTGRES_VERSION < 170000 || POSTGRES_VERSION >= 180000 )); then
  printf 'isolated staging must run PostgreSQL 17\n' >&2
  exit 1
fi
RUNTIME_IDENTITY="$(psql_as "$RUNTIME_ROLE" "$RUNTIME_PASSWORD" 'select session_user, current_user')"
MIGRATOR_IDENTITY="$(psql_as "$MIGRATOR_ROLE" "$MIGRATOR_PASSWORD" 'select session_user, current_user')"
DATABASE_OWNER="$(psql_as "$RUNTIME_ROLE" "$RUNTIME_PASSWORD" "select pg_get_userbyid(datdba) from pg_database where datname = current_database()")"
if [[ "$RUNTIME_IDENTITY" != "$RUNTIME_ROLE|$RUNTIME_ROLE" ]]; then
  printf 'runtime database identity mismatch\n' >&2
  exit 1
fi
if [[ "$MIGRATOR_IDENTITY" != "$MIGRATOR_ROLE|$MIGRATOR_ROLE" ]]; then
  printf 'migrator database identity mismatch\n' >&2
  exit 1
fi
if [[ "$DATABASE_OWNER" != "$OWNER_ROLE" || "$RUNTIME_ROLE" == "$MIGRATOR_ROLE" || "$RUNTIME_ROLE" == "$OWNER_ROLE" ]]; then
  printf 'owner, migrator, and runtime identities must be distinct\n' >&2
  exit 1
fi
ALEMBIC_HEAD="$(psql_as "$RUNTIME_ROLE" "$RUNTIME_PASSWORD" 'select version_num from alembic_version')"
IMAGE_HEAD="$(
  ta_run_with_timeout "$DOCKER_TIMEOUT" \
    "${COMPOSE[@]}" exec -T api python -m alembic heads \
    | awk 'NR == 1 {print $1}'
)"
if [[ -z "$ALEMBIC_HEAD" || "$ALEMBIC_HEAD" != "$IMAGE_HEAD" ]]; then
  printf 'database Alembic head differs from the exact API image\n' >&2
  exit 1
fi

STAGE=runtime_privilege_probes
runtime_cannot_update_events=PASS
if psql_as "$RUNTIME_ROLE" "$RUNTIME_PASSWORD" \
  'update ledger_events set event_type = event_type where false' \
  >"$REPORT_DIR/runtime-update-probe.log" 2>&1; then
  runtime_cannot_update_events=FAIL
  printf 'runtime unexpectedly has UPDATE on immutable events\n' >&2
  exit 1
fi
runtime_cannot_disable_triggers=PASS
if psql_as "$RUNTIME_ROLE" "$RUNTIME_PASSWORD" \
  'alter table ledger_events disable trigger all' \
  >"$REPORT_DIR/runtime-trigger-probe.log" 2>&1; then
  runtime_cannot_disable_triggers=FAIL
  printf 'runtime unexpectedly can disable ledger triggers\n' >&2
  exit 1
fi

STAGE=async_projection_lag
ta_run_with_timeout "$COMPOSE_TIMEOUT" \
  "${COMPOSE[@]}" exec -T api python - "$BOOK_ID" >"$PROJECTION_RESULT" <<'PY'
import json
import os
import sys
import time
from uuid import UUID

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from track_anywhere.infrastructure.db.models.async_projection import (
    ProjectionCheckpointRecord,
    ProjectionFailureRecord,
)
from track_anywhere.infrastructure.db.models.event_store import BookEventHeadRecord
from track_anywhere.infrastructure.projections.checkpoints import PROJECTION_NAME, PROJECTOR_VERSION

book_id = UUID(sys.argv[1])
engine = create_engine(os.environ["TRACK_ANYWHERE_DATABASE_URL"], pool_pre_ping=True)
factory = sessionmaker(bind=engine, class_=Session, expire_on_commit=False)
try:
    for attempt in range(1, 121):
        with factory() as session:
            head = session.get(BookEventHeadRecord, book_id)
            checkpoint = session.get(
                ProjectionCheckpointRecord,
                (PROJECTION_NAME, PROJECTOR_VERSION, book_id),
            )
            paused = session.query(ProjectionFailureRecord).filter_by(
                projection_name=PROJECTION_NAME,
                projector_version=PROJECTOR_VERSION,
                book_id=book_id,
                retry_state="paused",
            ).first()
            if paused is not None:
                raise SystemExit("async projection paused on a recorded failure")
            if head is not None and checkpoint is not None:
                projection_lag = head.last_position - checkpoint.last_book_position
            else:
                projection_lag = None
        if projection_lag == 0:
            break
        time.sleep(0.25)
    else:
        raise SystemExit("embedded async projection runtime did not converge")
    print(json.dumps({
        "poll_attempts": attempt,
        "projection_lag": projection_lag,
        "status": "PASS",
    }, sort_keys=True))
finally:
    engine.dispose()
PY

STAGE=independent_replay_and_hash_head
ta_run_with_timeout "$COMPOSE_TIMEOUT" \
  "${COMPOSE[@]}" exec -T api python - >"$VERIFIER_RESULT" <<'PY'
import json
import os

from track_anywhere.verification import verify_v2_ledger

report = verify_v2_ledger(os.environ["TRACK_ANYWHERE_DATABASE_URL"])
print(json.dumps(report.to_dict(), sort_keys=True))
if report.status != "PASS":
    raise SystemExit("independent replay/hash verification failed")
PY

STAGE=legacy_route_absence
LEGACY_STATUS="$(curl --connect-timeout 3 --max-time 15 -sS \
  -o "$REPORT_DIR/legacy-route-response.txt" -w '%{http_code}' \
  "$API_URL$LEGACY_API_PATH/health")"
LEGACY_ROUTE_STATUS="$LEGACY_STATUS"
if [[ "$LEGACY_STATUS" != "404" ]]; then
  printf 'legacy API route is unexpectedly reachable\n' >&2
  exit 1
fi

STAGE=teardown
if ! teardown_resources_strict; then
  printf 'isolated staging teardown failed\n' >&2
  exit 1
fi
STAGE=complete
write_verification "PASS"
PASS_WRITTEN=1
printf 'status=PASS source_commit=%s run_id=%s report=%s\n' \
  "$SOURCE_COMMIT" "$RUN_ID" "$REPORT_DIR"
