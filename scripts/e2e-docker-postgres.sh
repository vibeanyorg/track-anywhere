#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT_NAME="${TRACK_ANYWHERE_E2E_PROJECT:-track-anywhere-e2e-$$}"
COMPOSE_FILE="$ROOT_DIR/compose.e2e.yaml"
WORK_DIR="$(mktemp -d)"
DOCKER_CLI_TIMEOUT_SECONDS="${TRACK_ANYWHERE_DOCKER_CLI_TIMEOUT_SECONDS:-20}"
DOCKER_COMPOSE_TIMEOUT_SECONDS="${TRACK_ANYWHERE_DOCKER_COMPOSE_TIMEOUT_SECONDS:-900}"
HTTP_TIMEOUT_SECONDS="${TRACK_ANYWHERE_HTTP_TIMEOUT_SECONDS:-15}"
OWNER_ROLE="${TRACK_ANYWHERE_OWNER_ROLE:-track_anywhere_owner}"
MIGRATOR_ROLE="${TRACK_ANYWHERE_MIGRATOR_ROLE:-track_anywhere_migrator}"
MIGRATOR_PASSWORD="${TRACK_ANYWHERE_MIGRATOR_PASSWORD:-track_anywhere_migrator_test}"
RUNTIME_ROLE="${TRACK_ANYWHERE_RUNTIME_ROLE:-track_anywhere_runtime}"
RUNTIME_PASSWORD="${TRACK_ANYWHERE_RUNTIME_PASSWORD:-track_anywhere_runtime_test}"
RAW_API_KEY="ta_v2_local_e2e"

pick_port() {
  python3 - <<'PY'
import socket
with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
    sock.bind(("127.0.0.1", 0))
    print(sock.getsockname()[1])
PY
}

require_identifier() {
  local value="$1"
  local label="$2"
  if [[ ! "$value" =~ ^[a-z_][a-z0-9_]*$ ]] || (( ${#value} > 63 )); then
    printf '%s must be a safe PostgreSQL identifier\n' "$label" >&2
    exit 2
  fi
}

require_identifier "$OWNER_ROLE" "owner role"
require_identifier "$MIGRATOR_ROLE" "migrator role"
require_identifier "$RUNTIME_ROLE" "runtime role"

export TRACK_ANYWHERE_E2E_API_BIND="${TRACK_ANYWHERE_E2E_API_BIND:-127.0.0.1}"
export TRACK_ANYWHERE_E2E_POSTGRES_BIND="${TRACK_ANYWHERE_E2E_POSTGRES_BIND:-127.0.0.1}"
export TRACK_ANYWHERE_E2E_API_PORT="${TRACK_ANYWHERE_E2E_API_PORT:-$(pick_port)}"
export TRACK_ANYWHERE_E2E_POSTGRES_PORT="${TRACK_ANYWHERE_E2E_POSTGRES_PORT:-$(pick_port)}"

API_URL="http://${TRACK_ANYWHERE_E2E_API_BIND}:${TRACK_ANYWHERE_E2E_API_PORT}"
MIGRATOR_URL="postgresql+psycopg://${MIGRATOR_ROLE}:${MIGRATOR_PASSWORD}@${TRACK_ANYWHERE_E2E_POSTGRES_BIND}:${TRACK_ANYWHERE_E2E_POSTGRES_PORT}/track_anywhere?connect_timeout=5"
RUNTIME_URL="postgresql+psycopg://${RUNTIME_ROLE}:${RUNTIME_PASSWORD}@${TRACK_ANYWHERE_E2E_POSTGRES_BIND}:${TRACK_ANYWHERE_E2E_POSTGRES_PORT}/track_anywhere?connect_timeout=5"
COMPOSE=(docker compose -p "$PROJECT_NAME" -f "$COMPOSE_FILE")
if [[ -x "$ROOT_DIR/.venv/bin/python" ]]; then
  PY=("$ROOT_DIR/.venv/bin/python")
else
  PY=(uv run --extra postgres python)
fi
export PYTHONPATH="$ROOT_DIR/backend/app:$ROOT_DIR/cli${PYTHONPATH:+:$PYTHONPATH}"
cd "$ROOT_DIR"

run_with_timeout() {
  local timeout_seconds="$1"
  shift
  python3 - "$timeout_seconds" "$@" <<'PY'
import subprocess
import sys

timeout_seconds = float(sys.argv[1])
command = sys.argv[2:]
try:
    raise SystemExit(subprocess.run(command, timeout=timeout_seconds).returncode)
except subprocess.TimeoutExpired:
    print(
        f"command timed out after {timeout_seconds:g}s: {' '.join(command)}",
        file=sys.stderr,
    )
    raise SystemExit(124)
PY
}

cleanup() {
  local exit_code=$?
  if [[ "$exit_code" -ne 0 ]]; then
    run_with_timeout "$DOCKER_CLI_TIMEOUT_SECONDS" "${COMPOSE[@]}" logs --no-color api postgres || true
  fi
  if [[ "${TRACK_ANYWHERE_E2E_KEEP:-0}" != "1" ]]; then
    run_with_timeout "$DOCKER_CLI_TIMEOUT_SECONDS" "${COMPOSE[@]}" down -v --remove-orphans >/dev/null 2>&1 || true
    rm -rf "$WORK_DIR"
  else
    printf 'Keeping E2E project %s and work dir %s\n' "$PROJECT_NAME" "$WORK_DIR" >&2
  fi
  exit "$exit_code"
}
trap cleanup EXIT

assert_json_expr() {
  local file="$1"
  local expr="$2"
  "${PY[@]}" - "$file" "$expr" <<'PY'
import json
import sys

data = json.loads(open(sys.argv[1], encoding="utf-8").read())
safe_globals = {
    "__builtins__": {},
    "all": all,
    "any": any,
    "int": int,
    "isinstance": isinstance,
    "len": len,
    "sorted": sorted,
    "str": str,
}
if not eval(sys.argv[2], safe_globals, {"data": data}):
    raise SystemExit(f"assertion failed: {sys.argv[2]}")
PY
}

post_json() {
  local path="$1"
  local output="$2"
  local idempotency_key="$3"
  local payload="$4"
  local headers=(-H "X-API-Key: $RAW_API_KEY" -H "Content-Type: application/json")
  if [[ "$idempotency_key" != "-" ]]; then
    headers+=(-H "X-Idempotency-Key: $idempotency_key")
  fi
  curl --connect-timeout 3 --max-time "$HTTP_TIMEOUT_SECONDS" -fsS \
    -X POST "$API_URL$path" \
    "${headers[@]}" \
    --data "$payload" \
    >"$output"
}

get_json() {
  local path="$1"
  local output="$2"
  curl --connect-timeout 3 --max-time "$HTTP_TIMEOUT_SECONDS" -fsS \
    "$API_URL$path" \
    -H "Authorization: Bearer $RAW_API_KEY" \
    >"$output"
}

printf 'Starting isolated PostgreSQL 17 on port %s\n' "$TRACK_ANYWHERE_E2E_POSTGRES_PORT"
run_with_timeout "$DOCKER_CLI_TIMEOUT_SECONDS" docker version --format '{{.Server.Version}}' >/dev/null
run_with_timeout "$DOCKER_COMPOSE_TIMEOUT_SECONDS" "${COMPOSE[@]}" up -d postgres
run_with_timeout "$DOCKER_CLI_TIMEOUT_SECONDS" \
  "${COMPOSE[@]}" exec -T postgres \
  psql --username track_anywhere --dbname postgres --set ON_ERROR_STOP=1 \
  --command "ALTER DATABASE track_anywhere OWNER TO \"$OWNER_ROLE\""

printf 'Migrating the disposable database with the dedicated migrator role\n'
TRACK_ANYWHERE_DATABASE_URL="$MIGRATOR_URL" \
TRACK_ANYWHERE_DB_RUNTIME_ROLE="$RUNTIME_ROLE" \
  run_with_timeout "$DOCKER_COMPOSE_TIMEOUT_SECONDS" \
  "${PY[@]}" -m alembic upgrade head

printf 'Seeding one disposable V2 API key through the runtime role\n'
TRACK_ANYWHERE_E2E_RAW_API_KEY="$RAW_API_KEY" "${PY[@]}" - "$RUNTIME_URL" <<'PY'
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import os
import sys
from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from track_anywhere.infrastructure.db.models.auth import CredentialRecord, UserRecord

raw_api_key = os.environ["TRACK_ANYWHERE_E2E_RAW_API_KEY"]
now = datetime.now(UTC)
engine = create_engine(sys.argv[1], pool_pre_ping=True)
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
                    "account:read",
                    "account:write",
                    "book:read",
                    "book:write",
                    "ledger:read",
                    "ledger:write",
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

printf 'Building and starting the local V2 API at %s\n' "$API_URL"
run_with_timeout "$DOCKER_COMPOSE_TIMEOUT_SECONDS" "${COMPOSE[@]}" up -d --build api
for _ in {1..90}; do
  if curl --connect-timeout 2 --max-time 5 -fsS "$API_URL/api/v2/ready" >"$WORK_DIR/ready.json"; then
    break
  fi
  sleep 2
done
curl --connect-timeout 3 --max-time "$HTTP_TIMEOUT_SECONDS" -fsS \
  "$API_URL/api/v2/ready" >"$WORK_DIR/ready.json"
curl --connect-timeout 3 --max-time "$HTTP_TIMEOUT_SECONDS" -fsS \
  "$API_URL/api/v2/health" >"$WORK_DIR/health.json"
assert_json_expr "$WORK_DIR/health.json" "data == {'status': 'ok', 'api_version': 'v2'}"
assert_json_expr "$WORK_DIR/ready.json" "data['status'] == 'ok' and data['api_version'] == 'v2' and data['checks'] == {'database': 'ok', 'schema': 'ok'}"

mapfile -t ids < <("${PY[@]}" - <<'PY'
from uuid import uuid4
for _ in range(14):
    print(uuid4())
PY
)
BOOK_ID="${ids[0]}"
DEBIT_ACCOUNT_ID="${ids[1]}"
CREDIT_ACCOUNT_ID="${ids[2]}"
CATEGORY_ID="${ids[3]}"
CATEGORY_VERSION_ID="${ids[4]}"
TRANSACTION_ID="${ids[5]}"
POST_COMMAND_ID="${ids[6]}"
DEBIT_POSTING_ID="${ids[7]}"
CREDIT_POSTING_ID="${ids[8]}"
REPORTING_LINE_ID="${ids[9]}"
REPORTING_LINE_VERSION_ID="${ids[10]}"
REPORTING_COMMAND_ID="${ids[11]}"
REVERSAL_TRANSACTION_ID="${ids[12]}"
REVERSAL_COMMAND_ID="${ids[13]}"

post_json "/api/v2/books" "$WORK_DIR/book.json" - \
  "{\"book_id\":\"$BOOK_ID\",\"current_name\":\"Local E2E Book\",\"base_asset_code\":null}"
assert_json_expr "$WORK_DIR/book.json" "data == {'book_id': '$BOOK_ID', 'as_of_book_position': 0}"

post_json "/api/v2/books/$BOOK_ID/assets" "$WORK_DIR/asset.json" - \
  '{"asset_code":"USD","kind":"fiat","ledger_scale":2,"input_scale":2,"display_scale":2,"current_name":"US Dollar"}'
assert_json_expr "$WORK_DIR/asset.json" "data['asset_code'] == 'USD' and data['as_of_book_position'] == 0"

post_json "/api/v2/books/$BOOK_ID/accounts" "$WORK_DIR/debit-account.json" - \
  "{\"account_id\":\"$DEBIT_ACCOUNT_ID\",\"asset_code\":\"USD\",\"account_type\":\"asset\",\"current_name\":\"Cash\",\"system_role\":null}"
post_json "/api/v2/books/$BOOK_ID/accounts" "$WORK_DIR/credit-account.json" - \
  "{\"account_id\":\"$CREDIT_ACCOUNT_ID\",\"asset_code\":\"USD\",\"account_type\":\"expense\",\"current_name\":\"Food expense\",\"system_role\":null}"
assert_json_expr "$WORK_DIR/debit-account.json" "data['account_id'] == '$DEBIT_ACCOUNT_ID' and data['as_of_book_position'] == 0"
assert_json_expr "$WORK_DIR/credit-account.json" "data['account_id'] == '$CREDIT_ACCOUNT_ID' and data['as_of_book_position'] == 0"

post_json "/api/v2/books/$BOOK_ID/categories" "$WORK_DIR/category.json" - \
  "{\"category_id\":\"$CATEGORY_ID\",\"category_version_id\":\"$CATEGORY_VERSION_ID\",\"name\":\"Food\",\"parent_category_id\":null,\"change_reason_code\":\"created\"}"
assert_json_expr "$WORK_DIR/category.json" "data['category_id'] == '$CATEGORY_ID' and data['category_version_id'] == '$CATEGORY_VERSION_ID'"

post_json "/api/v2/books/$BOOK_ID/journal/transactions" "$WORK_DIR/posted.json" \
  "e2e-post-$TRANSACTION_ID" \
  "{\"command_id\":\"$POST_COMMAND_ID\",\"transaction_id\":\"$TRANSACTION_ID\",\"expected_stream_version\":0,\"kind\":\"standard\",\"effective_at\":\"2026-07-14T12:30:00Z\",\"description_ref\":null,\"external_references\":[],\"postings\":[{\"posting_id\":\"$DEBIT_POSTING_ID\",\"account_id\":\"$DEBIT_ACCOUNT_ID\",\"asset_code\":\"USD\",\"side\":\"debit\",\"amount\":\"12.34\"},{\"posting_id\":\"$CREDIT_POSTING_ID\",\"account_id\":\"$CREDIT_ACCOUNT_ID\",\"asset_code\":\"USD\",\"side\":\"credit\",\"amount\":\"12.34\"}]}"
assert_json_expr "$WORK_DIR/posted.json" "data == {'transaction_id': '$TRANSACTION_ID', 'as_of_book_position': 1}"

get_json "/api/v2/books/$BOOK_ID/journal?limit=10&as_of_book_position=1" "$WORK_DIR/journal-before.json"
assert_json_expr "$WORK_DIR/journal-before.json" "data['as_of_book_position'] == 1 and len(data['items']) == 1 and data['items'][0]['transaction_id'] == '$TRANSACTION_ID' and data['items'][0]['is_reversed'] is False and all(isinstance(posting['units'], str) and posting['units'] == '1234' for posting in data['items'][0]['postings'])"

get_json "/api/v2/books/$BOOK_ID/balances?as_of_book_position=1" "$WORK_DIR/balances.json"
assert_json_expr "$WORK_DIR/balances.json" "data['as_of_book_position'] == 1 and sorted(item['units'] for item in data['items']) == ['-1234', '1234'] and all(isinstance(item['units'], str) for item in data['items'])"

post_json "/api/v2/books/$BOOK_ID/journal/transactions/$TRANSACTION_ID/reporting-lines/assign" \
  "$WORK_DIR/reporting-assigned.json" "e2e-reporting-$TRANSACTION_ID" \
  "{\"command_id\":\"$REPORTING_COMMAND_ID\",\"expected_revision\":0,\"effective_at\":\"2026-07-14T12:31:00Z\",\"lines\":[{\"line_id\":\"$REPORTING_LINE_ID\",\"line_version_id\":\"$REPORTING_LINE_VERSION_ID\",\"catalog_id\":\"$CATEGORY_VERSION_ID\",\"asset_code\":\"USD\",\"units\":\"1234\",\"line_kind\":\"expense\",\"dimension\":\"category\",\"dimension_id\":\"$CATEGORY_ID\",\"description_ref\":null}]}"
assert_json_expr "$WORK_DIR/reporting-assigned.json" "data['transaction_id'] == '$TRANSACTION_ID' and data['classification_revision'] == 1 and data['as_of_book_position'] == 2"

get_json "/api/v2/books/$BOOK_ID/reporting-lines?as_of_book_position=2" "$WORK_DIR/reporting-lines.json"
assert_json_expr "$WORK_DIR/reporting-lines.json" "data['as_of_book_position'] == 2 and len(data['items']) == 1 and data['items'][0]['transaction_id'] == '$TRANSACTION_ID' and data['items'][0]['units'] == '1234' and isinstance(data['items'][0]['units'], str)"

post_json "/api/v2/books/$BOOK_ID/journal/transactions/$TRANSACTION_ID/reverse" \
  "$WORK_DIR/reversed.json" "e2e-reverse-$TRANSACTION_ID" \
  "{\"command_id\":\"$REVERSAL_COMMAND_ID\",\"reversal_transaction_id\":\"$REVERSAL_TRANSACTION_ID\",\"expected_stream_version\":0,\"reason_code\":\"duplicate\",\"effective_at\":\"2026-07-14T12:32:00Z\",\"description_ref\":null}"
assert_json_expr "$WORK_DIR/reversed.json" "data['reversal_transaction_id'] == '$REVERSAL_TRANSACTION_ID' and data['reverses_transaction_id'] == '$TRANSACTION_ID' and data['as_of_book_position'] == 3"

get_json "/api/v2/books/$BOOK_ID/journal?limit=10&as_of_book_position=3" "$WORK_DIR/journal-after.json"
assert_json_expr "$WORK_DIR/journal-after.json" "data['as_of_book_position'] == 3 and len(data['items']) == 2 and any(item['transaction_id'] == '$TRANSACTION_ID' and item['is_reversed'] and item['reversed_by_transaction_id'] == '$REVERSAL_TRANSACTION_ID' for item in data['items']) and any(item['transaction_id'] == '$REVERSAL_TRANSACTION_ID' and item['reverses_transaction_id'] == '$TRANSACTION_ID' for item in data['items'])"

printf 'Track Anywhere local V2 E2E passed: api=%s book=%s tx=%s\n' \
  "$API_URL" "$BOOK_ID" "$TRANSACTION_ID"
