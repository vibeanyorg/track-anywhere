#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly POSTGRES_IMAGE='postgres:17-alpine@sha256:742f40ea20b9ff2ff31db5458d127452988a2164df9e17441e191f3b72252193'
readonly SOURCE_DUMP_SHA256='a125b857a317e8c017d7028a26f78cf664ff6d57f6c0e698b3c229acf5d6cf9e'
readonly SOURCE_DUMP_BYTES='193256'
readonly SOURCE_MANIFEST_SHA256='f1cb565c646dc759b203efad3a5584492f3c824a16abb728bbce83150413597f'
readonly CREDIT_CARD_REVIEW_SHA256='237f964a990018eb7fc91b9e45ba001ebb319456577f14263e3a444dc4d54430'
readonly PLAN_SHA256='c93ed8aed78918d71caab6e7178a2c4347d72b55a08dfeace1d95eeb81604ec8'
readonly TERMINAL_HASH='bcc2828422fda617df93fb2fc92e41599f0c694f9f1d502f1dcd22f4d85186fc'
readonly CATALOG_IDENTITY_SHA256='3b7556099f961ffdd65869fd2cd41af97aa0360406586734fab0cd71bce2dc02'
readonly EXPECTED_ALEMBIC_VERSION='v2_0013_frozen_import_fence'
readonly TARGET_BOOK_ID='a682ddd2-f26a-5ad1-8f0a-f2fc1f75fa3d'
readonly EXPECTED_VERIFICATION_COUNTS_JSON='{"accounts":121,"archives":1,"assets":20,"async_projection_rows":30,"categories":37,"category_versions":37,"credit_card_transactions":0,"descriptions":138,"journal_postings":290,"journal_transactions":138,"ledger_events":176,"quarantine":0,"reporting_lines":38,"reversals":8,"synchronous_projection_applied_events":176}'
readonly EXPECTED_INSERTED_COUNTS_JSON='{"accounts":57,"archives":1,"assets":4,"categories":37,"category_versions":37,"credit_card_transactions":0,"descriptions":138,"events":176,"journal_transactions":138,"postings":290,"quarantine":0,"reporting_lines":38,"reversals":8}'
readonly RECEIPT_STATE_JSON='{"first_apply":"completed","first_apply_replayed":false,"replay":"completed","replay_inserted_total":0,"replayed":true}'
readonly ROLE_NAMES_JSON='{"migrator":"frozen_migrator","owner":"frozen_owner","runtime":"frozen_runtime","source_reader":"frozen_source_reader"}'
readonly REPORT_ALLOWLIST='alembic_version archive_sha256 balance_sha256 candidate_image_id catalog_identity_sha256 catalog_sha256 counts credit_card_review_sha256 description_plaintext_sha256 deterministic_ids_sha256 event_order_sha256 event_payloads_sha256 plan_sha256 postgres_version_num projection_sha256 quarantine_count receipt_state resource_counts role_names run_id source_commit source_dump_bytes source_dump_sha256 source_manifest_sha256 status terminal_hash'

CANDIDATE_IMAGE=""
SOURCE_COMMIT=""
RUN_ID=""
REPORT_DIR=""

usage() {
  echo "--candidate-image, --source-commit, --run-id, and --report-dir are required" >&2
  exit 2
}

if (($# != 8)); then
  usage
fi
while (($#)); do
  case "$1" in
    --candidate-image)
      [[ -z "$CANDIDATE_IMAGE" ]] || usage
      CANDIDATE_IMAGE="$2"
      shift 2
      ;;
    --source-commit)
      [[ -z "$SOURCE_COMMIT" ]] || usage
      SOURCE_COMMIT="$2"
      shift 2
      ;;
    --run-id)
      [[ -z "$RUN_ID" ]] || usage
      RUN_ID="$2"
      shift 2
      ;;
    --report-dir)
      [[ -z "$REPORT_DIR" ]] || usage
      REPORT_DIR="$2"
      shift 2
      ;;
    *) usage ;;
  esac
done

initialization_failure() {
  echo "rehearsal initialization failed" >&2
  return 1
}

readonly sha256_pattern='^[0-9a-f]{64}$'
readonly commit_pattern='^[0-9a-f]{40}$'
readonly uuid_pattern='^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
readonly candidate_pattern='^([^[:space:]]+@sha256:[0-9a-f]{64}|sha256:[0-9a-f]{64})$'
[[ "$SOURCE_COMMIT" =~ $commit_pattern ]] || usage
[[ "$RUN_ID" =~ $uuid_pattern ]] || usage
[[ "$CANDIDATE_IMAGE" =~ $candidate_pattern ]] || usage
[[ ! -e "$REPORT_DIR" ]] || {
  echo "report directory must not already exist" >&2
  exit 2
}
: "${TRACK_ANYWHERE_CREDIT_CARD_REVIEW_FILE:?required approved review file}"
[[ -f "$TRACK_ANYWHERE_CREDIT_CARD_REVIEW_FILE" ]] || {
  echo "approved review file is unavailable" >&2
  exit 2
}

for command in docker git jq openssl python3 sha256sum stat tar; do
  command -v "$command" >/dev/null || {
    echo "required rehearsal command is unavailable" >&2
    exit 2
  }
done

[[ "$(git -C "$ROOT_DIR" rev-parse HEAD)" == "$SOURCE_COMMIT" ]] || {
  echo "source commit mismatch" >&2
  exit 2
}
[[ -z "$(git -C "$ROOT_DIR" status --porcelain --untracked-files=all)" ]] || {
  echo "source checkout must be clean" >&2
  exit 2
}
RUNNING_SCRIPT_SHA256="$(sha256sum "${BASH_SOURCE[0]}" | awk '{print $1}')" || initialization_failure
readonly RUNNING_SCRIPT_SHA256
COMMITTED_SCRIPT_SHA256="$(
  git -C "$ROOT_DIR" show "${SOURCE_COMMIT}:scripts/rehearse-frozen-v1-history.sh" |
    sha256sum | awk '{print $1}'
)" || initialization_failure
readonly COMMITTED_SCRIPT_SHA256
[[ "$RUNNING_SCRIPT_SHA256" == "$COMMITTED_SCRIPT_SHA256" ]] || {
  echo "rehearsal script does not match source commit" >&2
  exit 2
}

readonly RUN_SCOPE="taf-${SOURCE_COMMIT:0:12}-${RUN_ID//-/}"
readonly RUN_LABEL="track-anywhere.frozen-rehearsal=$RUN_SCOPE"
readonly NETWORK_NAME="${RUN_SCOPE}-net"
readonly SOURCE_CONTAINER="${RUN_SCOPE}-src"
readonly TARGET_A_CONTAINER="${RUN_SCOPE}-a"
readonly TARGET_B_CONTAINER="${RUN_SCOPE}-b"
readonly SOURCE_VOLUME="${RUN_SCOPE}-sv"
readonly TARGET_A_VOLUME="${RUN_SCOPE}-av"
readonly TARGET_B_VOLUME="${RUN_SCOPE}-bv"
readonly resource_name_pattern='^[a-z0-9][a-z0-9-]*$'
for resource_name in \
  "$NETWORK_NAME" "$SOURCE_CONTAINER" "$TARGET_A_CONTAINER" \
  "$TARGET_B_CONTAINER" "$SOURCE_VOLUME" "$TARGET_A_VOLUME" \
  "$TARGET_B_VOLUME"; do
  [[ "${#resource_name}" -le 63 ]] || usage
  [[ "$resource_name" =~ $resource_name_pattern ]] || usage
done
readonly SNAPSHOT_DIR="/dev/shm/${RUN_SCOPE}-snapshot"
readonly KEYRING_DIR="/dev/shm/${RUN_SCOPE}-keyring"
readonly CLAIM_DIR="/dev/shm/${RUN_SCOPE}-claim"
readonly KEYRING_FILE="$KEYRING_DIR/protected-content.json"
readonly REVIEW_FILE="$KEYRING_DIR/approved-card-review.json"
readonly MANIFEST_CONTAINER_PATH='/workspace/backend/tests/v2/imports/fixtures/frozen_full_manifest.json'
readonly REVIEW_CONTAINER_PATH='/run/secrets/approved-card-review.json'
readonly KEYRING_CONTAINER_PATH='/run/secrets/protected-content.json'
readonly SOURCE_DATABASE='frozen_source'
readonly TARGET_DATABASE='track_anywhere'
readonly OWNER_ROLE='frozen_owner'
readonly MIGRATOR_ROLE='frozen_migrator'
readonly RUNTIME_ROLE='frozen_runtime'
readonly PYTHON_MODULE_BOOTSTRAP='import runpy,sys;sys.path.insert(0,"/workspace");module=sys.argv.pop(1);runpy.run_module(module,run_name="__main__",alter_sys=True)'
readonly PYTHON_SCRIPT_BOOTSTRAP='import runpy,sys;sys.path.insert(0,"/workspace");path=sys.argv.pop(1);runpy.run_path(path,run_name="__main__")'
readonly -a SNAPSHOT_PATHS=(
  backend/tools/__init__.py
  backend/tools/frozen_v1_history
  backend/tests/v2/imports/fixtures/frozen_full_manifest.json
  backend/tests/v2/imports/fixtures/frozen_production_catalog_baseline.json
  docker/postgres/init/001-v2-roles.sh
  scripts/seed-frozen-production-catalog.py
  scripts/stream-v1-dump-to-postgres.py
  scripts/verify-frozen-history-target.py
)

POSTGRES_PASSWORD="$(openssl rand -hex 24)" || initialization_failure
readonly POSTGRES_PASSWORD
SOURCE_READER_PASSWORD="$(openssl rand -hex 24)" || initialization_failure
readonly SOURCE_READER_PASSWORD
MIGRATOR_PASSWORD="$(openssl rand -hex 24)" || initialization_failure
readonly MIGRATOR_PASSWORD
RUNTIME_PASSWORD="$(openssl rand -hex 24)" || initialization_failure
readonly RUNTIME_PASSWORD
readonly SOURCE_URL="postgresql+psycopg://frozen_source_reader:${SOURCE_READER_PASSWORD}@${SOURCE_CONTAINER}:5432/${SOURCE_DATABASE}"
readonly TARGET_A_MIGRATOR_URL="postgresql+psycopg://${MIGRATOR_ROLE}:${MIGRATOR_PASSWORD}@${TARGET_A_CONTAINER}:5432/${TARGET_DATABASE}"
readonly TARGET_B_MIGRATOR_URL="postgresql+psycopg://${MIGRATOR_ROLE}:${MIGRATOR_PASSWORD}@${TARGET_B_CONTAINER}:5432/${TARGET_DATABASE}"
readonly TARGET_A_RUNTIME_URL="postgresql+psycopg://${RUNTIME_ROLE}:${RUNTIME_PASSWORD}@${TARGET_A_CONTAINER}:5432/${TARGET_DATABASE}"
readonly TARGET_B_RUNTIME_URL="postgresql+psycopg://${RUNTIME_ROLE}:${RUNTIME_PASSWORD}@${TARGET_B_CONTAINER}:5432/${TARGET_DATABASE}"

CANDIDATE_IMAGE_ID=""
CLEANUP_FAILED=0
CLAIM_OWNED=0
PRESERVE_CLAIM=0

query_run_resources() {
  local result
  if ! result="$(docker "$@" 2>/dev/null)"; then
    return 1
  fi
  printf '%s' "$result"
}

cleanup() {
  local original_status=$?
  if [[ "$CLAIM_OWNED" != 1 ]]; then
    return "$original_status"
  fi
  local container_resources=""
  local network_resources=""
  local volume_resources=""
  local resource
  set +e
  if ! container_resources="$(docker ps -aq --filter "label=$RUN_LABEL")"; then
    CLEANUP_FAILED=1
  fi
  while IFS= read -r resource; do
    [[ -z "$resource" ]] && continue
    docker rm -f "$resource" >/dev/null 2>&1 || CLEANUP_FAILED=1
  done <<<"$container_resources"
  if ! network_resources="$(
    docker network ls -q --filter "label=$RUN_LABEL"
  )"; then
    CLEANUP_FAILED=1
  fi
  while IFS= read -r resource; do
    [[ -z "$resource" ]] && continue
    docker network rm "$resource" >/dev/null 2>&1 || CLEANUP_FAILED=1
  done <<<"$network_resources"
  if ! volume_resources="$(
    docker volume ls -q --filter "label=$RUN_LABEL"
  )"; then
    CLEANUP_FAILED=1
  fi
  while IFS= read -r resource; do
    [[ -z "$resource" ]] && continue
    docker volume rm -f "$resource" >/dev/null 2>&1 || CLEANUP_FAILED=1
  done <<<"$volume_resources"
  rm -f -- "$KEYRING_FILE" || CLEANUP_FAILED=1
  rm -f -- "$REVIEW_FILE" || CLEANUP_FAILED=1
  rmdir "$KEYRING_DIR" >/dev/null 2>&1 || CLEANUP_FAILED=1
  if [[ -e "$SNAPSHOT_DIR" ]]; then
    chmod -R u+w "$SNAPSHOT_DIR" >/dev/null 2>&1 || CLEANUP_FAILED=1
    rm -rf -- "$SNAPSHOT_DIR" || CLEANUP_FAILED=1
  fi
  if [[ "$PRESERVE_CLAIM" != 1 ]]; then
    rmdir "$CLAIM_DIR" >/dev/null 2>&1 || CLEANUP_FAILED=1
    [[ ! -e "$CLAIM_DIR" ]] || CLEANUP_FAILED=1
    if [[ ! -e "$CLAIM_DIR" ]]; then
      CLAIM_OWNED=0
    fi
  fi
  set -e
  return "$original_status"
}
trap cleanup EXIT

if ! mkdir -m 0700 "$CLAIM_DIR"; then
  echo "rehearsal run is already claimed" >&2
  exit 2
fi
CLAIM_OWNED=1
mkdir -m 0700 "$REPORT_DIR"
mkdir -m 0700 "$KEYRING_DIR"
mkdir -m 0755 "$SNAPSHOT_DIR"
git -C "$ROOT_DIR" archive --format=tar "$SOURCE_COMMIT" -- "${SNAPSHOT_PATHS[@]}" |
  tar -xf - -C "$SNAPSHOT_DIR"
chmod -R a-w,a+rX "$SNAPSHOT_DIR"

CANDIDATE_IMAGE_ID="$(docker image inspect "$CANDIDATE_IMAGE" --format '{{.Id}}')" || initialization_failure
[[ "$CANDIDATE_IMAGE_ID" =~ ^sha256:[0-9a-f]{64}$ ]] || {
  echo "candidate image identity is invalid" >&2
  exit 1
}
readonly CANDIDATE_IMAGE_ID
CANDIDATE_REVISION="$(
  docker image inspect "$CANDIDATE_IMAGE_ID" \
    --format '{{index .Config.Labels "org.opencontainers.image.revision"}}'
)" || initialization_failure
readonly CANDIDATE_REVISION
[[ "$CANDIDATE_REVISION" == "$SOURCE_COMMIT" ]] || {
  echo "candidate image revision mismatch" >&2
  exit 1
}
docker image inspect "$POSTGRES_IMAGE" >/dev/null

CANDIDATE_UID="$(
  docker run --rm --pull never --network none \
    --label "$RUN_LABEL" \
    --entrypoint id "$CANDIDATE_IMAGE_ID" -u
)" || initialization_failure
readonly CANDIDATE_UID
CANDIDATE_GID="$(
  docker run --rm --pull never --network none \
    --label "$RUN_LABEL" \
    --entrypoint id "$CANDIDATE_IMAGE_ID" -g
)" || initialization_failure
readonly CANDIDATE_GID
[[ "$CANDIDATE_UID" =~ ^[0-9]+$ && "$CANDIDATE_GID" =~ ^[0-9]+$ ]] || {
  echo "candidate runtime identity is invalid" >&2
  exit 1
}

MASTER_KEY="$(openssl rand -base64 32 | tr -d '\n')" || initialization_failure
readonly MASTER_KEY
printf '{"active_key_ref":"rehearsal-v1","keys":{"rehearsal-v1":"%s"},"version":1}\n' \
  "$MASTER_KEY" >"$KEYRING_FILE"
cp "$TRACK_ANYWHERE_CREDIT_CARD_REVIEW_FILE" "$REVIEW_FILE"
chmod 0400 "$KEYRING_FILE"
chmod 0400 "$REVIEW_FILE"
docker run --rm --pull never --network none \
  --label "$RUN_LABEL" \
  --user 0:0 \
  --entrypoint chown \
  --mount "type=bind,src=$KEYRING_FILE,dst=/run/rehearsal-secrets/protected-content.json" \
  --mount "type=bind,src=$REVIEW_FILE,dst=/run/rehearsal-secrets/approved-card-review.json" \
  "$CANDIDATE_IMAGE_ID" \
  "$CANDIDATE_UID:$CANDIDATE_GID" \
  /run/rehearsal-secrets/protected-content.json \
  /run/rehearsal-secrets/approved-card-review.json
for secret_file in "$KEYRING_FILE" "$REVIEW_FILE"; do
  [[ "$(stat -c '%u:%g:%a' "$secret_file")" == \
    "$CANDIDATE_UID:$CANDIDATE_GID:400" ]] || {
    echo "rehearsal secret ownership or mode is invalid" >&2
    exit 1
  }
done
docker run --rm --pull never --network none \
  --label "$RUN_LABEL" \
  --user "$CANDIDATE_UID:$CANDIDATE_GID" \
  --mount "type=bind,src=$SNAPSHOT_DIR,dst=/workspace,readonly" \
  --mount "type=bind,src=$REVIEW_FILE,dst=$REVIEW_CONTAINER_PATH,readonly" \
  --workdir /workspace \
  "$CANDIDATE_IMAGE_ID" \
  python -I -c "$PYTHON_MODULE_BOOTSTRAP" \
    backend.tools.frozen_v1_history \
    verify-review-content "$REVIEW_CONTAINER_PATH" >/dev/null || {
  echo "approved review content mismatch" >&2
  exit 1
}

docker network create --internal --label "$RUN_LABEL" "$NETWORK_NAME" >/dev/null
docker volume create --label "$RUN_LABEL" "$SOURCE_VOLUME" >/dev/null
docker volume create --label "$RUN_LABEL" "$TARGET_A_VOLUME" >/dev/null
docker volume create --label "$RUN_LABEL" "$TARGET_B_VOLUME" >/dev/null

start_postgres() {
  local container="$1"
  local database="$2"
  local volume="$3"
  local timezone="$4"
  local locale="$5"
  shift 5
  docker run -d --pull never \
    --name "$container" \
    --label "$RUN_LABEL" \
    --network "$NETWORK_NAME" \
    --tmpfs /var/lib/postgresql/data:rw,nosuid,noexec,size=1g \
    --mount "type=volume,source=$volume,target=/rehearsal" \
    --env "POSTGRES_DB=$database" \
    --env POSTGRES_USER=postgres \
    --env "POSTGRES_PASSWORD=$POSTGRES_PASSWORD" \
    --env "POSTGRES_INITDB_ARGS=--locale=$locale --encoding=UTF8" \
    --env "TZ=$timezone" \
    --env "LC_ALL=$locale" \
    "$@" \
    "$POSTGRES_IMAGE" >/dev/null
}

start_postgres "$SOURCE_CONTAINER" "$SOURCE_DATABASE" "$SOURCE_VOLUME" UTC C
start_postgres \
  "$TARGET_A_CONTAINER" "$TARGET_DATABASE" "$TARGET_A_VOLUME" UTC C \
  --env "TRACK_ANYWHERE_OWNER_ROLE=$OWNER_ROLE" \
  --env "TRACK_ANYWHERE_MIGRATOR_ROLE=$MIGRATOR_ROLE" \
  --env "TRACK_ANYWHERE_MIGRATOR_PASSWORD=$MIGRATOR_PASSWORD" \
  --env "TRACK_ANYWHERE_RUNTIME_ROLE=$RUNTIME_ROLE" \
  --env "TRACK_ANYWHERE_RUNTIME_PASSWORD=$RUNTIME_PASSWORD" \
  --mount "type=bind,src=$SNAPSHOT_DIR/docker/postgres/init/001-v2-roles.sh,dst=/docker-entrypoint-initdb.d/001-v2-roles.sh,readonly"
start_postgres \
  "$TARGET_B_CONTAINER" "$TARGET_DATABASE" "$TARGET_B_VOLUME" \
  Pacific/Auckland C.UTF-8 \
  --env "TRACK_ANYWHERE_OWNER_ROLE=$OWNER_ROLE" \
  --env "TRACK_ANYWHERE_MIGRATOR_ROLE=$MIGRATOR_ROLE" \
  --env "TRACK_ANYWHERE_MIGRATOR_PASSWORD=$MIGRATOR_PASSWORD" \
  --env "TRACK_ANYWHERE_RUNTIME_ROLE=$RUNTIME_ROLE" \
  --env "TRACK_ANYWHERE_RUNTIME_PASSWORD=$RUNTIME_PASSWORD" \
  --mount "type=bind,src=$SNAPSHOT_DIR/docker/postgres/init/001-v2-roles.sh,dst=/docker-entrypoint-initdb.d/001-v2-roles.sh,readonly"

wait_postgres() {
  local container="$1"
  local database="$2"
  local attempt
  for attempt in {1..60}; do
    if docker exec "$container" \
      pg_isready -h 127.0.0.1 -U postgres -d "$database" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  echo "PostgreSQL readiness failed" >&2
  return 1
}
wait_postgres "$SOURCE_CONTAINER" "$SOURCE_DATABASE"
wait_postgres "$TARGET_A_CONTAINER" "$TARGET_DATABASE"
wait_postgres "$TARGET_B_CONTAINER" "$TARGET_DATABASE"

assert_target_roles_ready() {
  local container="$1"
  local role_state
  if ! role_state="$(
    docker exec \
      --env "PGPASSWORD=$POSTGRES_PASSWORD" \
      "$container" psql \
      --host 127.0.0.1 \
      --username postgres \
      --dbname "$TARGET_DATABASE" \
      --tuples-only \
      --no-align \
      --set ON_ERROR_STOP=1 \
      --command "
        SELECT
          (SELECT count(*) FROM pg_roles
           WHERE rolname IN ('$OWNER_ROLE', '$MIGRATOR_ROLE', '$RUNTIME_ROLE'))
          || '|' || pg_get_userbyid(datdba)
        FROM pg_database
        WHERE datname = current_database()
      "
  )"; then
    echo "target role read-back failed" >&2
    return 1
  fi
  [[ "$role_state" == "3|$OWNER_ROLE" ]] || {
    echo "target roles or database owner are invalid" >&2
    return 1
  }
}
assert_target_roles_ready "$TARGET_A_CONTAINER"
assert_target_roles_ready "$TARGET_B_CONTAINER"

python3 "$SNAPSHOT_DIR/scripts/stream-v1-dump-to-postgres.py" \
  --container "$SOURCE_CONTAINER" \
  --database "$SOURCE_DATABASE" \
  --username postgres \
  --expected-bytes "$SOURCE_DUMP_BYTES" \
  --expected-sha256 "$SOURCE_DUMP_SHA256" \
  >"$REPORT_DIR/source-restore.json"

docker exec -i "$SOURCE_CONTAINER" psql \
  --username postgres \
  --dbname "$SOURCE_DATABASE" \
  --set ON_ERROR_STOP=1 \
  --set "reader_password=$SOURCE_READER_PASSWORD" <<'SQL'
CREATE ROLE frozen_source_reader
  LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS NOINHERIT;
ALTER ROLE frozen_source_reader PASSWORD :'reader_password';
ALTER ROLE frozen_source_reader SET default_transaction_read_only=on;
GRANT CONNECT ON DATABASE frozen_source TO frozen_source_reader;
GRANT USAGE ON SCHEMA public TO frozen_source_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO frozen_source_reader;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO frozen_source_reader;
SQL
# The role default makes this implicit transaction equivalent to BEGIN READ ONLY.
SOURCE_READER_IDENTITY="$(
  docker exec \
    --env "PGPASSWORD=$SOURCE_READER_PASSWORD" \
    "$SOURCE_CONTAINER" psql \
    --host 127.0.0.1 \
    --username frozen_source_reader \
    --dbname "$SOURCE_DATABASE" \
    --tuples-only \
    --no-align \
    --set ON_ERROR_STOP=1 \
    --command "
      SELECT session_user || '|' || current_user || '|' ||
             current_setting('transaction_read_only')
    "
)" || {
  echo "source reader identity read-back failed" >&2
  exit 1
}
readonly SOURCE_READER_IDENTITY
[[ "$SOURCE_READER_IDENTITY" == "frozen_source_reader|frozen_source_reader|on" ]] || {
  echo "source reader identity or transaction mode is invalid" >&2
  exit 1
}

run_migration() {
  local database_url="$1"
  docker run --rm --pull never \
    --label "$RUN_LABEL" \
    --network "$NETWORK_NAME" \
    --env "TRACK_ANYWHERE_DATABASE_URL=$database_url" \
    --env "TRACK_ANYWHERE_DB_RUNTIME_ROLE=$RUNTIME_ROLE" \
    "$CANDIDATE_IMAGE_ID" \
    python -m alembic upgrade head
}
run_migration "$TARGET_A_MIGRATOR_URL"
run_migration "$TARGET_B_MIGRATOR_URL"

variant_values() {
  case "$1" in
    A)
      TZ=UTC
      LC_ALL=C
      TRACK_ANYWHERE_FROZEN_SHUFFLE_SEED=0
      TRACK_ANYWHERE_FROZEN_BATCH_SIZE=37
      TRACK_ANYWHERE_FROZEN_WORKERS=1
      ;;
    B)
      TZ=Pacific/Auckland
      LC_ALL=C.UTF-8
      TRACK_ANYWHERE_FROZEN_SHUFFLE_SEED=731
      TRACK_ANYWHERE_FROZEN_BATCH_SIZE=13
      TRACK_ANYWHERE_FROZEN_WORKERS=4
      ;;
    *) return 2 ;;
  esac
  export TZ LC_ALL TRACK_ANYWHERE_FROZEN_SHUFFLE_SEED
  export TRACK_ANYWHERE_FROZEN_BATCH_SIZE TRACK_ANYWHERE_FROZEN_WORKERS
}

run_planner() {
  variant_values "$1"
  docker run --rm --pull never \
    --label "$RUN_LABEL" \
    --network "$NETWORK_NAME" \
    --mount "type=bind,src=$SNAPSHOT_DIR,dst=/workspace,readonly" \
    --mount "type=bind,src=$REVIEW_FILE,dst=$REVIEW_CONTAINER_PATH,readonly" \
    --workdir /workspace \
    --env "TRACK_ANYWHERE_FROZEN_SOURCE_URL=$SOURCE_URL" \
    --env "TRACK_ANYWHERE_FROZEN_MANIFEST_PATH=$MANIFEST_CONTAINER_PATH" \
    --env "TRACK_ANYWHERE_CREDIT_CARD_REVIEW_PATH=$REVIEW_CONTAINER_PATH" \
    --env "TRACK_ANYWHERE_FROZEN_SHUFFLE_SEED=$TRACK_ANYWHERE_FROZEN_SHUFFLE_SEED" \
    --env "TRACK_ANYWHERE_FROZEN_BATCH_SIZE=$TRACK_ANYWHERE_FROZEN_BATCH_SIZE" \
    --env "TRACK_ANYWHERE_FROZEN_WORKERS=$TRACK_ANYWHERE_FROZEN_WORKERS" \
    --env "TZ=$TZ" \
    --env "LC_ALL=$LC_ALL" \
    "$CANDIDATE_IMAGE_ID" \
    python -I -c "$PYTHON_MODULE_BOOTSTRAP" \
      backend.tools.frozen_v1_history
}

target_runtime_url() {
  case "$1" in
    A) printf '%s' "$TARGET_A_RUNTIME_URL" ;;
    B) printf '%s' "$TARGET_B_RUNTIME_URL" ;;
    *) return 2 ;;
  esac
}

seed_catalog() {
  local database_url
  database_url="$(target_runtime_url "$1")"
  docker run --rm --pull never -i \
    --label "$RUN_LABEL" \
    --network "$NETWORK_NAME" \
    --mount "type=bind,src=$SNAPSHOT_DIR,dst=/workspace,readonly" \
    --workdir /workspace \
    --env "TRACK_ANYWHERE_DATABASE_URL=$database_url" \
    "$CANDIDATE_IMAGE_ID" \
    python -I -c "$PYTHON_SCRIPT_BOOTSTRAP" \
      /workspace/scripts/seed-frozen-production-catalog.py \
      --plan-sha256 "$PLAN_SHA256" --stdin
}

run_importer() {
  local database_url
  database_url="$(target_runtime_url "$1")"
  docker run --rm --pull never -i \
    --label "$RUN_LABEL" \
    --network "$NETWORK_NAME" \
    --mount "type=bind,src=$KEYRING_FILE,dst=$KEYRING_CONTAINER_PATH,readonly" \
    --env "TRACK_ANYWHERE_DATABASE_URL=$database_url" \
    --env "TRACK_ANYWHERE_PROTECTED_CONTENT_KEYRING_FILE=$KEYRING_CONTAINER_PATH" \
    "$CANDIDATE_IMAGE_ID" \
    python -m track_anywhere.offline.import_frozen_financial_history \
      --target-book-id "$TARGET_BOOK_ID" \
      --plan-sha256 "$PLAN_SHA256" \
      --stdin
}

run_reference() {
  variant_values "$1"
  docker run --rm --pull never -i \
    --label "$RUN_LABEL" \
    --network "$NETWORK_NAME" \
    --mount "type=bind,src=$SNAPSHOT_DIR,dst=/workspace,readonly" \
    --mount "type=bind,src=$REVIEW_FILE,dst=$REVIEW_CONTAINER_PATH,readonly" \
    --workdir /workspace \
    --env "TRACK_ANYWHERE_FROZEN_SOURCE_URL=$SOURCE_URL" \
    --env "TRACK_ANYWHERE_FROZEN_MANIFEST_PATH=$MANIFEST_CONTAINER_PATH" \
    --env "TRACK_ANYWHERE_CREDIT_CARD_REVIEW_PATH=$REVIEW_CONTAINER_PATH" \
    --env "TRACK_ANYWHERE_FROZEN_SHUFFLE_SEED=$TRACK_ANYWHERE_FROZEN_SHUFFLE_SEED" \
    --env "TRACK_ANYWHERE_FROZEN_BATCH_SIZE=$TRACK_ANYWHERE_FROZEN_BATCH_SIZE" \
    --env "TRACK_ANYWHERE_FROZEN_WORKERS=$TRACK_ANYWHERE_FROZEN_WORKERS" \
    --env "TZ=$TZ" \
    --env "LC_ALL=$LC_ALL" \
    "$CANDIDATE_IMAGE_ID" \
    python -I -c "$PYTHON_MODULE_BOOTSTRAP" \
      backend.tools.frozen_v1_history reference --stdin
}

run_target_verifier() {
  local database_url
  database_url="$(target_runtime_url "$1")"
  docker run --rm --pull never -i \
    --label "$RUN_LABEL" \
    --network "$NETWORK_NAME" \
    --mount "type=bind,src=$SNAPSHOT_DIR,dst=/workspace,readonly" \
    --mount "type=bind,src=$KEYRING_FILE,dst=$KEYRING_CONTAINER_PATH,readonly" \
    --workdir /workspace \
    --env "TRACK_ANYWHERE_DATABASE_URL=$database_url" \
    --env "TRACK_ANYWHERE_PROTECTED_CONTENT_KEYRING_FILE=$KEYRING_CONTAINER_PATH" \
    "$CANDIDATE_IMAGE_ID" \
    python -I -c "$PYTHON_SCRIPT_BOOTSTRAP" \
      /workspace/scripts/verify-frozen-history-target.py --stdin
}

run_planner A | seed_catalog A >"$REPORT_DIR/catalog-a.json"
run_planner B | seed_catalog B >"$REPORT_DIR/catalog-b.json"
run_planner A | run_importer A >"$REPORT_DIR/receipt-a.json"
run_planner B | run_importer B >"$REPORT_DIR/receipt-b.json"
run_planner A | run_importer A >"$REPORT_DIR/replay-a.json"
run_planner B | run_importer B >"$REPORT_DIR/replay-b.json"
run_planner A | run_reference A | run_target_verifier A >"$REPORT_DIR/verify-a.json"
run_planner B | run_reference B | run_target_verifier B >"$REPORT_DIR/verify-b.json"

assert_catalog_identity_exact() {
  jq -e \
    --arg identity "$CATALOG_IDENTITY_SHA256" \
    --arg plan "$PLAN_SHA256" \
    '.status == "PASS" and .accounts == 64 and .assets == 16 and
     .fixture_identity_sha256 == $identity and .plan_sha256 == $plan and
     (.catalog_sha256 | test("^[0-9a-f]{64}$"))' \
    "$1" >/dev/null
}
assert_catalog_identity_exact "$REPORT_DIR/catalog-a.json"
assert_catalog_identity_exact "$REPORT_DIR/catalog-b.json"

assert_first_receipt_completed() {
  jq -e \
    --arg plan "$PLAN_SHA256" \
    --arg terminal "$TERMINAL_HASH" \
    --argjson counts "$EXPECTED_VERIFICATION_COUNTS_JSON" \
    --argjson inserted "$EXPECTED_INSERTED_COUNTS_JSON" \
    'keys == ["counts", "expected_terminal_hash", "first_book_position",
              "inserted_counts", "last_book_position", "plan_hash",
              "receipt_state", "replayed"] and
     .receipt_state == "completed" and .replayed == false and
     .plan_hash == $plan and .expected_terminal_hash == $terminal and
     .counts == $counts and .inserted_counts == $inserted and
     .first_book_position == 1 and .last_book_position == 176' \
    "$1" >/dev/null
}
assert_first_receipt_completed "$REPORT_DIR/receipt-a.json"
assert_first_receipt_completed "$REPORT_DIR/receipt-b.json"

assert_zero_inserted_counts() {
  jq -e \
    --arg plan "$PLAN_SHA256" \
    --arg terminal "$TERMINAL_HASH" \
    --argjson counts "$EXPECTED_VERIFICATION_COUNTS_JSON" \
    --argjson inserted "$EXPECTED_INSERTED_COUNTS_JSON" \
    'keys == ["counts", "expected_terminal_hash", "first_book_position",
              "inserted_counts", "last_book_position", "plan_hash",
              "receipt_state", "replayed"] and
     .receipt_state == "completed" and .replayed == true and
     .plan_hash == $plan and .expected_terminal_hash == $terminal and
     .counts == $counts and
     .inserted_counts == ($inserted | with_entries(.value = 0)) and
     .first_book_position == 1 and .last_book_position == 176' \
    "$1" >/dev/null
}
assert_zero_inserted_counts "$REPORT_DIR/replay-a.json"
assert_zero_inserted_counts "$REPORT_DIR/replay-b.json"

compare_catalog_hashes() {
  cmp --silent "$REPORT_DIR/catalog-a.json" "$REPORT_DIR/catalog-b.json"
}
compare_catalog_hashes

compare_two_target_determinism() {
  cmp --silent "$REPORT_DIR/receipt-a.json" "$REPORT_DIR/receipt-b.json"
  cmp --silent "$REPORT_DIR/replay-a.json" "$REPORT_DIR/replay-b.json"
  cmp --silent "$REPORT_DIR/verify-a.json" "$REPORT_DIR/verify-b.json"
}
compare_two_target_determinism

read_hash() {
  jq -er --arg key "$2" '
    select(.status == "PASS")
    | .hashes
    | select(type == "object")
    | .[$key]
    | select(type == "string" and test("^[0-9a-f]{64}$"))
  ' "$1"
}
sha256_values() {
  printf '%s\n' "$@" | sha256sum | awk '{print $1}'
}
read_target_sql_scalar() {
  local container="$1"
  local statement="$2"
  docker exec \
    --env "PGPASSWORD=$POSTGRES_PASSWORD" \
    "$container" psql \
    --host 127.0.0.1 \
    --username postgres \
    --dbname "$TARGET_DATABASE" \
    --tuples-only \
    --no-align \
    --set ON_ERROR_STOP=1 \
    --command "$statement"
}

readonly VERIFY_REPORT="$REPORT_DIR/verify-a.json"
report_value_failure() {
  echo "verification report value is invalid" >&2
  return 1
}

POSTGRES_VERSION_NUM_A="$(
  read_target_sql_scalar "$TARGET_A_CONTAINER" 'SHOW server_version_num'
)" || report_value_failure
readonly POSTGRES_VERSION_NUM_A
POSTGRES_VERSION_NUM_B="$(
  read_target_sql_scalar "$TARGET_B_CONTAINER" 'SHOW server_version_num'
)" || report_value_failure
readonly POSTGRES_VERSION_NUM_B
readonly postgres_version_pattern='^17[0-9]{4}$'
[[ "$POSTGRES_VERSION_NUM_A" =~ $postgres_version_pattern ]] || {
  echo "PostgreSQL version is invalid" >&2
  exit 1
}
[[ "$POSTGRES_VERSION_NUM_A" == "$POSTGRES_VERSION_NUM_B" ]] || {
  echo "PostgreSQL versions differ" >&2
  exit 1
}
readonly POSTGRES_VERSION_NUM="$POSTGRES_VERSION_NUM_A"
ALEMBIC_VERSION_A="$(
  read_target_sql_scalar \
    "$TARGET_A_CONTAINER" 'SELECT version_num FROM alembic_version'
)" || report_value_failure
readonly ALEMBIC_VERSION_A
ALEMBIC_VERSION_B="$(
  read_target_sql_scalar \
    "$TARGET_B_CONTAINER" 'SELECT version_num FROM alembic_version'
)" || report_value_failure
readonly ALEMBIC_VERSION_B
[[ "$ALEMBIC_VERSION_A" == "$EXPECTED_ALEMBIC_VERSION" ]] || {
  echo "Alembic version is invalid" >&2
  exit 1
}
[[ "$ALEMBIC_VERSION_A" == "$ALEMBIC_VERSION_B" ]] || {
  echo "Alembic versions differ" >&2
  exit 1
}
readonly ALEMBIC_VERSION="$ALEMBIC_VERSION_A"
OBSERVED_COUNTS_JSON="$(jq -ceS '.counts' "$VERIFY_REPORT")" || report_value_failure
readonly OBSERVED_COUNTS_JSON
[[ "$OBSERVED_COUNTS_JSON" == "$EXPECTED_VERIFICATION_COUNTS_JSON" ]] || {
  echo "verification counts are invalid" >&2
  exit 1
}
QUARANTINE_COUNT="$(jq -er '.counts.quarantine' "$VERIFY_REPORT")" || report_value_failure
readonly QUARANTINE_COUNT
[[ "$QUARANTINE_COUNT" == 0 ]] || {
  echo "quarantine count is nonzero" >&2
  exit 1
}
ACCOUNTS_SHA256="$(read_hash "$VERIFY_REPORT" accounts)" || report_value_failure
readonly ACCOUNTS_SHA256
ASSETS_SHA256="$(read_hash "$VERIFY_REPORT" assets)" || report_value_failure
readonly ASSETS_SHA256
CATEGORIES_SHA256="$(read_hash "$VERIFY_REPORT" categories)" || report_value_failure
readonly CATEGORIES_SHA256
JOURNAL_TRANSACTIONS_SHA256="$(read_hash "$VERIFY_REPORT" journal_transactions)" || report_value_failure
readonly JOURNAL_TRANSACTIONS_SHA256
SYNC_PROJECTION_SHA256="$(read_hash "$VERIFY_REPORT" synchronous_projection)" || report_value_failure
readonly SYNC_PROJECTION_SHA256
ASYNC_PROJECTION_SHA256="$(read_hash "$VERIFY_REPORT" async_projection)" || report_value_failure
readonly ASYNC_PROJECTION_SHA256
STORED_BALANCE_SHA256="$(read_hash "$VERIFY_REPORT" balances)" || report_value_failure
readonly STORED_BALANCE_SHA256
ARCHIVE_METADATA_SHA256="$(read_hash "$VERIFY_REPORT" archive_metadata)" || report_value_failure
readonly ARCHIVE_METADATA_SHA256
ARCHIVE_PLAINTEXT_SHA256="$(read_hash "$VERIFY_REPORT" archive_plaintext)" || report_value_failure
readonly ARCHIVE_PLAINTEXT_SHA256
ARCHIVE_SEAL_SHA256="$(read_hash "$VERIFY_REPORT" archive_seal)" || report_value_failure
readonly ARCHIVE_SEAL_SHA256
DETERMINISTIC_IDS_SHA256="$(sha256_values \
  "$ACCOUNTS_SHA256" "$ASSETS_SHA256" "$CATEGORIES_SHA256" \
  "$JOURNAL_TRANSACTIONS_SHA256")" || report_value_failure
readonly DETERMINISTIC_IDS_SHA256
EVENT_ORDER_SHA256="$(read_hash "$VERIFY_REPORT" event_order)" || report_value_failure
readonly EVENT_ORDER_SHA256
EVENT_PAYLOADS_SHA256="$(read_hash "$VERIFY_REPORT" event_payloads)" || report_value_failure
readonly EVENT_PAYLOADS_SHA256
BALANCE_SHA256="$(read_hash "$VERIFY_REPORT" account_balances_semantic)" || report_value_failure
readonly BALANCE_SHA256
PROJECTION_SHA256="$(sha256_values \
  "$SYNC_PROJECTION_SHA256" "$ASYNC_PROJECTION_SHA256" "$STORED_BALANCE_SHA256")" || report_value_failure
readonly PROJECTION_SHA256
DESCRIPTION_PLAINTEXT_SHA256="$(read_hash "$VERIFY_REPORT" description_aggregate)" || report_value_failure
readonly DESCRIPTION_PLAINTEXT_SHA256
ARCHIVE_SHA256="$(sha256_values \
  "$ARCHIVE_METADATA_SHA256" "$ARCHIVE_PLAINTEXT_SHA256" "$ARCHIVE_SEAL_SHA256")" || report_value_failure
readonly ARCHIVE_SHA256
OBSERVED_TERMINAL_HASH="$(read_hash "$VERIFY_REPORT" terminal)" || report_value_failure
readonly OBSERVED_TERMINAL_HASH
CATALOG_SHA256="$(jq -er '.catalog_sha256' "$REPORT_DIR/catalog-a.json")" || report_value_failure
readonly CATALOG_SHA256
[[ "$OBSERVED_TERMINAL_HASH" == "$TERMINAL_HASH" ]] || {
  echo "terminal hash mismatch" >&2
  exit 1
}
for digest in \
  "$DETERMINISTIC_IDS_SHA256" "$EVENT_ORDER_SHA256" \
  "$EVENT_PAYLOADS_SHA256" "$BALANCE_SHA256" "$PROJECTION_SHA256" \
  "$DESCRIPTION_PLAINTEXT_SHA256" "$ARCHIVE_SHA256" "$CATALOG_SHA256"; do
  [[ "$digest" =~ $sha256_pattern ]] || {
    echo "verification digest is invalid" >&2
    exit 1
  }
done

assert_no_run_resources() {
  local resources
  if ! resources="$(
    query_run_resources ps -aq --filter "label=$RUN_LABEL"
  )"; then
    return 1
  fi
  [[ -z "$resources" ]] || return 1
  if ! resources="$(
    query_run_resources network ls -q --filter "label=$RUN_LABEL"
  )"; then
    return 1
  fi
  [[ -z "$resources" ]] || return 1
  if ! resources="$(
    query_run_resources volume ls -q --filter "label=$RUN_LABEL"
  )"; then
    return 1
  fi
  [[ -z "$resources" ]]
}

validate_report_allowlist() {
  local keys
  keys="$(jq -r 'keys | join(" ")' "$1")"
  [[ "$keys" == "$REPORT_ALLOWLIST" ]]
}

write_report() {
  local status="$1"
  local temporary="$REPORT_DIR/.summary.json.tmp"
  [[ "$status" == "PASS" ]] || return 2
  printf '%s\n' \
    "{\"alembic_version\":\"$ALEMBIC_VERSION\",\"archive_sha256\":\"$ARCHIVE_SHA256\",\"balance_sha256\":\"$BALANCE_SHA256\",\"candidate_image_id\":\"$CANDIDATE_IMAGE_ID\",\"catalog_identity_sha256\":\"$CATALOG_IDENTITY_SHA256\",\"catalog_sha256\":\"$CATALOG_SHA256\",\"counts\":$OBSERVED_COUNTS_JSON,\"credit_card_review_sha256\":\"$CREDIT_CARD_REVIEW_SHA256\",\"description_plaintext_sha256\":\"$DESCRIPTION_PLAINTEXT_SHA256\",\"deterministic_ids_sha256\":\"$DETERMINISTIC_IDS_SHA256\",\"event_order_sha256\":\"$EVENT_ORDER_SHA256\",\"event_payloads_sha256\":\"$EVENT_PAYLOADS_SHA256\",\"plan_sha256\":\"$PLAN_SHA256\",\"postgres_version_num\":$POSTGRES_VERSION_NUM,\"projection_sha256\":\"$PROJECTION_SHA256\",\"quarantine_count\":$QUARANTINE_COUNT,\"receipt_state\":$RECEIPT_STATE_JSON,\"resource_counts\":{\"containers\":0,\"networks\":0,\"volumes\":0},\"role_names\":$ROLE_NAMES_JSON,\"run_id\":\"$RUN_ID\",\"source_commit\":\"$SOURCE_COMMIT\",\"source_dump_bytes\":$SOURCE_DUMP_BYTES,\"source_dump_sha256\":\"$SOURCE_DUMP_SHA256\",\"source_manifest_sha256\":\"$SOURCE_MANIFEST_SHA256\",\"status\":\"$status\",\"terminal_hash\":\"$OBSERVED_TERMINAL_HASH\"}" \
    >"$temporary"
  validate_report_allowlist "$temporary"
  mv "$temporary" "$REPORT_DIR/summary.json"
}

PRESERVE_CLAIM=1
cleanup
PRESERVE_CLAIM=0
[[ "$CLEANUP_FAILED" == 0 ]] || {
  echo "resource removal failed" >&2
  exit 1
}
assert_no_run_resources
write_report "PASS"
if ! rmdir "$CLAIM_DIR"; then
  rm -f -- "$REPORT_DIR/summary.json"
  echo "run claim removal failed" >&2
  exit 1
fi
[[ ! -e "$CLAIM_DIR" ]] || {
  rm -f -- "$REPORT_DIR/summary.json"
  echo "run claim still exists after removal" >&2
  exit 1
}
CLAIM_OWNED=0
trap - EXIT
printf 'frozen_history_rehearsal=PASS\n'
