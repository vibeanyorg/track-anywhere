#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT_NAME="${TRACK_ANYWHERE_E2E_PROJECT:-track-anywhere-e2e-$$}"
COMPOSE_FILE="$ROOT_DIR/compose.e2e.yaml"
WORK_DIR=""
NO_BUILD="${TRACK_ANYWHERE_E2E_NO_BUILD:-0}"
EXISTING_STACK="${TRACK_ANYWHERE_E2E_EXISTING_STACK:-0}"
RESULT_FILE="${TRACK_ANYWHERE_E2E_RESULT_FILE:-}"
DOCKER_CLI_TIMEOUT_SECONDS="${TRACK_ANYWHERE_DOCKER_CLI_TIMEOUT_SECONDS:-20}"
DOCKER_COMPOSE_TIMEOUT_SECONDS="${TRACK_ANYWHERE_DOCKER_COMPOSE_TIMEOUT_SECONDS:-900}"
HTTP_TIMEOUT_SECONDS="${TRACK_ANYWHERE_HTTP_TIMEOUT_SECONDS:-15}"
OWNER_ROLE="${TRACK_ANYWHERE_OWNER_ROLE:-track_anywhere_owner}"
MIGRATOR_ROLE="${TRACK_ANYWHERE_MIGRATOR_ROLE:-track_anywhere_migrator}"
MIGRATOR_PASSWORD="${TRACK_ANYWHERE_MIGRATOR_PASSWORD:-track_anywhere_migrator_test}"
RUNTIME_ROLE="${TRACK_ANYWHERE_RUNTIME_ROLE:-track_anywhere_runtime}"
RUNTIME_PASSWORD="${TRACK_ANYWHERE_RUNTIME_PASSWORD:-track_anywhere_runtime_test}"
RAW_API_KEY="ta_v2_local_e2e"
LEGACY_API_PATH='/api/'"v1"

# shellcheck source=scripts/lib/e2e-harness-common.sh
source "$ROOT_DIR/scripts/lib/e2e-harness-common.sh"

if [[ "$NO_BUILD" == "1" && "$EXISTING_STACK" != "1" ]]; then
  printf 'NO_BUILD requires TRACK_ANYWHERE_E2E_EXISTING_STACK=1\n' >&2
  exit 2
fi
if [[ "$EXISTING_STACK" == "1" ]]; then
  : "${TRACK_ANYWHERE_E2E_PROJECT:?existing stack requires TRACK_ANYWHERE_E2E_PROJECT}"
  : "${TRACK_ANYWHERE_E2E_API_PORT:?existing stack requires TRACK_ANYWHERE_E2E_API_PORT}"
  : "${TRACK_ANYWHERE_E2E_POSTGRES_PORT:?existing stack requires TRACK_ANYWHERE_E2E_POSTGRES_PORT}"
  : "${TRACK_ANYWHERE_E2E_API_IMAGE:?existing stack requires TRACK_ANYWHERE_E2E_API_IMAGE}"
fi
if [[ -n "$RESULT_FILE" && -e "$RESULT_FILE" ]]; then
  printf 'TRACK_ANYWHERE_E2E_RESULT_FILE must not already exist\n' >&2
  exit 2
fi

ta_require_postgres_identifier "$OWNER_ROLE" "owner role"
ta_require_postgres_identifier "$MIGRATOR_ROLE" "migrator role"
ta_require_postgres_identifier "$RUNTIME_ROLE" "runtime role"

export TRACK_ANYWHERE_E2E_API_BIND="${TRACK_ANYWHERE_E2E_API_BIND:-127.0.0.1}"
export TRACK_ANYWHERE_E2E_POSTGRES_BIND="${TRACK_ANYWHERE_E2E_POSTGRES_BIND:-127.0.0.1}"
export TRACK_ANYWHERE_E2E_API_PORT="${TRACK_ANYWHERE_E2E_API_PORT:-$(ta_pick_loopback_port)}"
export TRACK_ANYWHERE_E2E_POSTGRES_PORT="${TRACK_ANYWHERE_E2E_POSTGRES_PORT:-$(ta_pick_loopback_port)}"

E2E_PUBLIC_HOST="$TRACK_ANYWHERE_E2E_API_BIND"
if [[ "$E2E_PUBLIC_HOST" == "0.0.0.0" ]]; then
  E2E_PUBLIC_HOST="127.0.0.1"
elif [[ "$E2E_PUBLIC_HOST" == "::1" ]]; then
  E2E_PUBLIC_HOST="[::1]"
fi
export TRACK_ANYWHERE_E2E_PUBLIC_BASE_URL="http://${E2E_PUBLIC_HOST}:${TRACK_ANYWHERE_E2E_API_PORT}"

for bind in \
  "$TRACK_ANYWHERE_E2E_API_BIND" \
  "$TRACK_ANYWHERE_E2E_POSTGRES_BIND"; do
  if [[ "$bind" != "127.0.0.1" && "$bind" != "localhost" && "$bind" != "::1" ]]; then
    printf 'E2E ports must bind to loopback, got %s\n' "$bind" >&2
    exit 2
  fi
done

API_URL="http://${TRACK_ANYWHERE_E2E_API_BIND}:${TRACK_ANYWHERE_E2E_API_PORT}"
PUBLIC_URL="$TRACK_ANYWHERE_E2E_PUBLIC_BASE_URL"
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
WORK_DIR="$(mktemp -d)"

cleanup() {
  local exit_code=$?
  if [[ "$exit_code" -ne 0 ]]; then
    ta_run_with_timeout "$DOCKER_CLI_TIMEOUT_SECONDS" "${COMPOSE[@]}" logs --no-color api postgres || true
  fi
  if [[ "$EXISTING_STACK" == "1" ]]; then
    rm -rf "$WORK_DIR"
  elif [[ "${TRACK_ANYWHERE_E2E_KEEP:-0}" != "1" ]]; then
    ta_run_with_timeout "$DOCKER_CLI_TIMEOUT_SECONDS" "${COMPOSE[@]}" down -v --remove-orphans >/dev/null 2>&1 || true
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
    -H "X-API-Key: $RAW_API_KEY" \
    >"$output"
}

ta_run_with_timeout "$DOCKER_CLI_TIMEOUT_SECONDS" docker version --format '{{.Server.Version}}' >/dev/null
POSTGRES_IMAGE_REFERENCE="${TRACK_ANYWHERE_POSTGRES_IMAGE:-postgres:17-alpine@sha256:742f40ea20b9ff2ff31db5458d127452988a2164df9e17441e191f3b72252193}"
export TRACK_ANYWHERE_POSTGRES_IMAGE="$POSTGRES_IMAGE_REFERENCE"
if [[ "$EXISTING_STACK" == "1" ]]; then
  printf 'existing stack mode: refusing infrastructure mutation; running smoke checks only\n'
  ta_run_with_timeout "$DOCKER_CLI_TIMEOUT_SECONDS" \
    "${COMPOSE[@]}" ps --status running api postgres >/dev/null
  EXISTING_API_CONTAINER="$("${COMPOSE[@]}" ps -q api)"
  EXISTING_POSTGRES_CONTAINER="$("${COMPOSE[@]}" ps -q postgres)"
  if [[ -z "$EXISTING_API_CONTAINER" || -z "$EXISTING_POSTGRES_CONTAINER" ]]; then
    printf 'existing stack is missing a required running service\n' >&2
    exit 1
  fi
  EXPECTED_API_IMAGE_ID="$(ta_run_with_timeout "$DOCKER_CLI_TIMEOUT_SECONDS" \
    docker image inspect "$TRACK_ANYWHERE_E2E_API_IMAGE" --format '{{.Id}}')"
  EXPECTED_POSTGRES_IMAGE_ID="$(ta_run_with_timeout "$DOCKER_CLI_TIMEOUT_SECONDS" \
    docker image inspect "$POSTGRES_IMAGE_REFERENCE" --format '{{.Id}}')"
  if [[ "$(ta_run_with_timeout "$DOCKER_CLI_TIMEOUT_SECONDS" docker inspect "$EXISTING_API_CONTAINER" --format '{{.Image}}')" != "$EXPECTED_API_IMAGE_ID" ]]; then
    printf 'existing stack API image mismatch\n' >&2
    exit 1
  fi
  if [[ "$(ta_run_with_timeout "$DOCKER_CLI_TIMEOUT_SECONDS" docker inspect "$EXISTING_POSTGRES_CONTAINER" --format '{{.Image}}')" != "$EXPECTED_POSTGRES_IMAGE_ID" ]]; then
    printf 'existing stack PostgreSQL image mismatch\n' >&2
    exit 1
  fi
else
  printf 'Starting isolated PostgreSQL 17 on port %s\n' "$TRACK_ANYWHERE_E2E_POSTGRES_PORT"
  ta_run_with_timeout "$DOCKER_COMPOSE_TIMEOUT_SECONDS" \
    "${COMPOSE[@]}" up -d --wait postgres
  ta_initialize_database_owner \
    "$DOCKER_CLI_TIMEOUT_SECONDS" "$OWNER_ROLE" "${COMPOSE[@]}"

  printf 'Building the single application image before the one-shot migration service\n'
  ta_run_with_timeout "$DOCKER_COMPOSE_TIMEOUT_SECONDS" \
    "${COMPOSE[@]}" build api
  printf 'Migrating the disposable database with the dedicated migrator role\n'
  ta_run_with_timeout "$DOCKER_COMPOSE_TIMEOUT_SECONDS" \
    "${COMPOSE[@]}" run --rm --no-deps migrate

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

  printf 'Starting the local V2 application at %s\n' "$PUBLIC_URL"
  ta_run_with_timeout "$DOCKER_COMPOSE_TIMEOUT_SECONDS" \
    "${COMPOSE[@]}" up -d --no-build api
fi

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

curl --connect-timeout 3 --max-time "$HTTP_TIMEOUT_SECONDS" -fsS \
  -D "$WORK_DIR/home-headers.txt" -o "$WORK_DIR/home.html" "$PUBLIC_URL/"
grep -Fq '<title>Track Anywhere</title>' "$WORK_DIR/home.html"
grep -Eiq '^content-type: text/html' "$WORK_DIR/home-headers.txt"
grep -Eiq '^cache-control: no-cache' "$WORK_DIR/home-headers.txt"
curl --connect-timeout 3 --max-time "$HTTP_TIMEOUT_SECONDS" -fsS \
  -o "$WORK_DIR/login.html" "$PUBLIC_URL/auth/login/?next=%2F"
grep -Fq '<title>Track Anywhere</title>' "$WORK_DIR/login.html"
STATIC_ASSET="$(
  grep -oE 'href="/_next/static/[^"]+' "$WORK_DIR/home.html" \
    | sed -n '1{s/^href="//;p;}'
)"
if [[ -z "$STATIC_ASSET" ]]; then
  printf 'static export did not reference a Next.js asset\n' >&2
  exit 1
fi
curl --connect-timeout 3 --max-time "$HTTP_TIMEOUT_SECONDS" -fsS \
  -D "$WORK_DIR/static-headers.txt" -o "$WORK_DIR/static-asset" \
  "$PUBLIC_URL$STATIC_ASSET"
grep -Eiq '^cache-control: public, max-age=31536000, immutable' \
  "$WORK_DIR/static-headers.txt"
printf 'static_web_smoke=PASS\n'

curl --connect-timeout 3 --max-time "$HTTP_TIMEOUT_SECONDS" -fsS \
  "$PUBLIC_URL/.well-known/oauth-authorization-server" \
  >"$WORK_DIR/oauth-authorization-server.json"
curl --connect-timeout 3 --max-time "$HTTP_TIMEOUT_SECONDS" -fsS \
  "$PUBLIC_URL/.well-known/oauth-protected-resource/mcp" \
  >"$WORK_DIR/oauth-protected-resource-mcp.json"
assert_json_expr "$WORK_DIR/oauth-authorization-server.json" \
  "data['issuer'] == '$PUBLIC_URL/' and data['authorization_endpoint'] == '$PUBLIC_URL/api/v2/oauth/authorize' and data['code_challenge_methods_supported'] == ['S256']"
assert_json_expr "$WORK_DIR/oauth-protected-resource-mcp.json" \
  "data['resource'] == '$PUBLIC_URL/mcp' and data['authorization_servers'] == ['$PUBLIC_URL/'] and data['scopes_supported'] == ['book:read', 'book:write', 'ledger:read', 'ledger:write']"

MCP_STATUS="$(curl --connect-timeout 3 --max-time "$HTTP_TIMEOUT_SECONDS" -sS \
  -D "$WORK_DIR/mcp-headers.txt" -o "$WORK_DIR/mcp-response.json" \
  -w '%{http_code}' -X POST "$PUBLIC_URL/mcp" \
  -H 'Accept: application/json, text/event-stream' \
  -H 'Content-Type: application/json' \
  --data '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"e2e","version":"1"}}}')"
if [[ "$MCP_STATUS" != "401" ]] || ! grep -Fq \
  "resource_metadata=\"$PUBLIC_URL/.well-known/oauth-protected-resource/mcp\"" \
  "$WORK_DIR/mcp-headers.txt"; then
  printf 'expected public MCP endpoint to return an OAuth challenge, got HTTP %s\n' \
    "$MCP_STATUS" >&2
  exit 1
fi

V1_STATUS="$(curl --connect-timeout 3 --max-time "$HTTP_TIMEOUT_SECONDS" -sS \
  -o "$WORK_DIR/v1-response.txt" -w '%{http_code}' "$API_URL$LEGACY_API_PATH/health")"
if [[ "$V1_STATUS" != "404" ]]; then
  printf 'expected the legacy API health route to be absent, got HTTP %s\n' "$V1_STATUS" >&2
  exit 1
fi

ta_run_with_timeout "$HTTP_TIMEOUT_SECONDS" "${COMPOSE[@]}" exec -T api \
  python -m track_anywhere_cli.main \
  --base-url http://127.0.0.1:8000 --agent system health \
  >"$WORK_DIR/cli-health.json"

ids=()
while IFS= read -r generated_id; do
  ids+=("$generated_id")
done < <("${PY[@]}" - <<'PY'
from uuid import uuid4
for _ in range(23):
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
CARD_ACCOUNT_ID="${ids[14]}"
CARD_CHARGE_TRANSACTION_ID="${ids[15]}"
CARD_CHARGE_COMMAND_ID="${ids[16]}"
CARD_PAYMENT_TRANSACTION_ID="${ids[17]}"
CARD_PAYMENT_COMMAND_ID="${ids[18]}"
CARD_REFUND_TRANSACTION_ID="${ids[19]}"
CARD_REFUND_COMMAND_ID="${ids[20]}"
CARD_FEE_TRANSACTION_ID="${ids[21]}"
CARD_FEE_COMMAND_ID="${ids[22]}"

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
assert_json_expr "$WORK_DIR/balances.json" "data['as_of_book_position'] == 1 and sorted(item['raw_accounting_units'] for item in data['items']) == ['-1234', '1234'] and sorted(item['natural_units'] for item in data['items']) == ['-1234', '1234'] and {item['normal_side'] for item in data['items']} == {'debit'} and {item['account_type'] for item in data['items']} == {'asset', 'expense'} and {item['account_status'] for item in data['items']} == {'active'} and all(item['outstanding_units'] is None and item['overpayment_units'] is None for item in data['items'])"

"${PY[@]}" - "$RUNTIME_URL" "$BOOK_ID" "$RUNTIME_ROLE" <<'PY'
import sys

from sqlalchemy import create_engine, text

engine = create_engine(sys.argv[1], pool_pre_ping=True)
try:
    with engine.connect() as connection:
        identity = connection.execute(
            text(
                "select session_user, current_user, current_database(), "
                "current_setting('server_version_num')"
            )
        ).one()
        balances = connection.execute(
            text(
                "select balance_units from account_balances "
                "where book_id = cast(:book_id as uuid) order by balance_units"
            ),
            {"book_id": sys.argv[2]},
        ).scalars().all()
    assert tuple(identity[:3]) == (sys.argv[3], sys.argv[3], "track_anywhere")
    assert 170000 <= int(identity[3]) < 180000
    assert [int(value) for value in balances] == [-1234, 1234]
    print("fresh_connection_balance_visibility=PASS")
finally:
    engine.dispose()
PY

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

ta_run_with_timeout "$HTTP_TIMEOUT_SECONDS" "${COMPOSE[@]}" exec -T \
  -e TRACK_ANYWHERE_API_KEY="$RAW_API_KEY" api \
  python -m track_anywhere_cli.main \
  --base-url http://127.0.0.1:8000 --insecure-automation --agent \
  account create "$BOOK_ID" "$CARD_ACCOUNT_ID" \
  --asset-code USD --type liability --account-subtype credit_card \
  --name "Local E2E Card" \
  >"$WORK_DIR/cli-card-account.json"
assert_json_expr "$WORK_DIR/cli-card-account.json" "data['ok'] and data['status'] == 201 and data['data']['account_id'] == '$CARD_ACCOUNT_ID'"

ta_run_with_timeout "$HTTP_TIMEOUT_SECONDS" "${COMPOSE[@]}" exec -T \
  -e TRACK_ANYWHERE_API_KEY="$RAW_API_KEY" api \
  python -m track_anywhere_cli.main \
  --base-url http://127.0.0.1:8000 --insecure-automation --agent \
  card charge "$BOOK_ID" "$CARD_CHARGE_TRANSACTION_ID" \
  --command-id "$CARD_CHARGE_COMMAND_ID" \
  --card-account-id "$CARD_ACCOUNT_ID" \
  --expense-account-id "$CREDIT_ACCOUNT_ID" \
  --asset-code USD --amount 100.00 \
  --effective-at 2026-07-14T12:33:00Z \
  --idempotency-key "e2e-card-charge-$CARD_CHARGE_TRANSACTION_ID" \
  >"$WORK_DIR/cli-card-charge.json"
assert_json_expr "$WORK_DIR/cli-card-charge.json" "data['ok'] and data['status'] == 201 and data['data']['transaction_id'] == '$CARD_CHARGE_TRANSACTION_ID' and data['data']['intent'] == 'charge'"

ta_run_with_timeout "$HTTP_TIMEOUT_SECONDS" "${COMPOSE[@]}" exec -T \
  -e TRACK_ANYWHERE_API_KEY="$RAW_API_KEY" api \
  python -m track_anywhere_cli.main \
  --base-url http://127.0.0.1:8000 --insecure-automation --agent \
  card payment "$BOOK_ID" "$CARD_PAYMENT_TRANSACTION_ID" \
  --command-id "$CARD_PAYMENT_COMMAND_ID" \
  --card-account-id "$CARD_ACCOUNT_ID" \
  --source-account-id "$DEBIT_ACCOUNT_ID" \
  --asset-code USD --amount 30.00 \
  --effective-at 2026-07-14T12:34:00Z \
  --idempotency-key "e2e-card-payment-$CARD_PAYMENT_TRANSACTION_ID" \
  >"$WORK_DIR/cli-card-payment.json"
assert_json_expr "$WORK_DIR/cli-card-payment.json" "data['ok'] and data['status'] == 201 and data['data']['intent'] == 'payment'"

ta_run_with_timeout "$HTTP_TIMEOUT_SECONDS" "${COMPOSE[@]}" exec -T \
  -e TRACK_ANYWHERE_API_KEY="$RAW_API_KEY" api \
  python -m track_anywhere_cli.main \
  --base-url http://127.0.0.1:8000 --insecure-automation --agent \
  card refund "$BOOK_ID" "$CARD_REFUND_TRANSACTION_ID" \
  --command-id "$CARD_REFUND_COMMAND_ID" \
  --card-account-id "$CARD_ACCOUNT_ID" \
  --original-transaction-id "$CARD_CHARGE_TRANSACTION_ID" \
  --asset-code USD --amount 20.00 \
  --effective-at 2026-07-14T12:35:00Z \
  --idempotency-key "e2e-card-refund-$CARD_REFUND_TRANSACTION_ID" \
  >"$WORK_DIR/cli-card-refund.json"
assert_json_expr "$WORK_DIR/cli-card-refund.json" "data['ok'] and data['status'] == 201 and data['data']['intent'] == 'refund'"

ta_run_with_timeout "$HTTP_TIMEOUT_SECONDS" "${COMPOSE[@]}" exec -T \
  -e TRACK_ANYWHERE_API_KEY="$RAW_API_KEY" api \
  python -m track_anywhere_cli.main \
  --base-url http://127.0.0.1:8000 --insecure-automation --agent \
  card fee "$BOOK_ID" "$CARD_FEE_TRANSACTION_ID" \
  --command-id "$CARD_FEE_COMMAND_ID" \
  --card-account-id "$CARD_ACCOUNT_ID" \
  --expense-account-id "$CREDIT_ACCOUNT_ID" \
  --asset-code USD --amount 5.00 \
  --effective-at 2026-07-14T12:36:00Z \
  --idempotency-key "e2e-card-fee-$CARD_FEE_TRANSACTION_ID" \
  >"$WORK_DIR/cli-card-fee.json"
assert_json_expr "$WORK_DIR/cli-card-fee.json" "data['ok'] and data['status'] == 201 and data['data']['intent'] == 'fee'"

get_json "/api/v2/books/$BOOK_ID/balances?as_of_book_position=7" "$WORK_DIR/card-balances.json"
assert_json_expr "$WORK_DIR/card-balances.json" "data['as_of_book_position'] == 7 and any(item['account_id'] == '$CARD_ACCOUNT_ID' and item['account_type'] == 'liability' and item['account_subtype'] == 'credit_card' and item['account_status'] == 'active' and item['raw_accounting_units'] == '-5500' and item['natural_units'] == '5500' and item['normal_side'] == 'credit' and item['outstanding_units'] == '5500' and item['overpayment_units'] == '0' for item in data['items'])"

get_json "/api/v2/books/$BOOK_ID/journal?limit=10&as_of_book_position=7" "$WORK_DIR/card-journal.json"
assert_json_expr "$WORK_DIR/card-journal.json" "data['as_of_book_position'] == 7 and {'credit_card_charge', 'credit_card_payment', 'credit_card_refund', 'credit_card_fee'} <= {item['transaction_kind'] for item in data['items']} and {item['credit_card_relation']['intent'] for item in data['items'] if item['credit_card_relation'] is not None} == {'charge', 'payment', 'refund', 'fee'} and any(item['transaction_id'] == '$CARD_REFUND_TRANSACTION_ID' and item['credit_card_relation']['original_transaction_id'] == '$CARD_CHARGE_TRANSACTION_ID' for item in data['items'])"

"${PY[@]}" - "$RUNTIME_URL" "$BOOK_ID" <<'PY'
import sys
import time

from sqlalchemy import create_engine, text

engine = create_engine(sys.argv[1], pool_pre_ping=True)
try:
    for _ in range(120):
        with engine.connect() as connection:
            state = connection.execute(
                text(
                    "select head.last_position, checkpoint.last_book_position "
                    "from book_event_heads head "
                    "left join projection_checkpoints checkpoint "
                    "on checkpoint.book_id = head.book_id "
                    "and checkpoint.projection_name = 'monthly_category_summary' "
                    "and checkpoint.projector_version = 1 "
                    "where head.book_id = cast(:book_id as uuid)"
                ),
                {"book_id": sys.argv[2]},
            ).one()
        if state[1] is not None and int(state[0]) == int(state[1]):
            print("embedded_projection_convergence=PASS")
            break
        time.sleep(0.25)
    else:
        raise SystemExit("embedded async projection runtime did not converge")
finally:
    engine.dispose()
PY

if [[ "$EXISTING_STACK" != "1" ]]; then
  printf 'Exercising ACL-preserving backup and fresh-database restore\n'
  POSTGRES_CONTAINER="$("${COMPOSE[@]}" ps -q postgres)"
  RESTORE_DATABASE="track_anywhere_restore_e2e"
  RESTORE_MIGRATOR_URL="postgresql+psycopg://${MIGRATOR_ROLE}:${MIGRATOR_PASSWORD}@postgres:5432/${RESTORE_DATABASE}?connect_timeout=5"
  RESTORE_RUNTIME_URL="postgresql+psycopg://${RUNTIME_ROLE}:${RUNTIME_PASSWORD}@${TRACK_ANYWHERE_E2E_POSTGRES_BIND}:${TRACK_ANYWHERE_E2E_POSTGRES_PORT}/${RESTORE_DATABASE}?connect_timeout=5"
  export TRACK_ANYWHERE_FAKE_RCLONE_ROOT="$WORK_DIR/fake-rclone"
  PATH="$ROOT_DIR/backend/tests/v2/fixtures/fake-bin:$PATH"

  # Keep the dump comfortably larger than an OS pipe buffer. pg_restore --list
  # stops after the TOC, so the validation path must continue draining stdin
  # instead of delivering SIGPIPE to gunzip on production-sized archives.
  ta_run_with_timeout "$DOCKER_CLI_TIMEOUT_SECONDS" docker exec -i \
    "$POSTGRES_CONTAINER" psql -U track_anywhere -d track_anywhere \
    --no-psqlrc --set ON_ERROR_STOP=1 --set=owner_role="$OWNER_ROLE" \
    --file=- <<'SQL'
      set role :"owner_role";
      create table public.backup_stream_regression (
        id integer primary key,
        payload text not null
      );
      insert into public.backup_stream_regression (id, payload)
      select value, repeat(md5(value::text), 512)
        from generate_series(1, 1024) value;
SQL

  BACKUP_OBJECT="$(
    TRACK_ANYWHERE_BACKUP_CONTAINER="$POSTGRES_CONTAINER" \
    TRACK_ANYWHERE_BACKUP_USER=track_anywhere \
    TRACK_ANYWHERE_BACKUP_DATABASE=track_anywhere \
    TRACK_ANYWHERE_BACKUP_S3_REMOTE=local-test: \
    TRACK_ANYWHERE_BACKUP_PREFIX=track-anywhere/postgres/e2e \
    TRACK_ANYWHERE_BACKUP_KEEP_LATEST=2 \
    "$ROOT_DIR/scripts/backup-postgres-s3.sh"
  )"

  ta_run_with_timeout "$DOCKER_CLI_TIMEOUT_SECONDS" docker exec \
    "$POSTGRES_CONTAINER" psql -U track_anywhere -d track_anywhere \
    --no-psqlrc --set ON_ERROR_STOP=1 \
    --command 'drop table public.backup_stream_regression'

  ta_run_with_timeout "$DOCKER_CLI_TIMEOUT_SECONDS" docker exec \
    "$POSTGRES_CONTAINER" createdb -U track_anywhere "$RESTORE_DATABASE"
  ta_run_with_timeout "$DOCKER_CLI_TIMEOUT_SECONDS" docker exec \
    -e POSTGRES_USER=track_anywhere \
    -e POSTGRES_DB="$RESTORE_DATABASE" \
    -e TRACK_ANYWHERE_OWNER_ROLE="$OWNER_ROLE" \
    -e TRACK_ANYWHERE_MIGRATOR_ROLE="$MIGRATOR_ROLE" \
    -e TRACK_ANYWHERE_MIGRATOR_PASSWORD="$MIGRATOR_PASSWORD" \
    -e TRACK_ANYWHERE_RUNTIME_ROLE="$RUNTIME_ROLE" \
    -e TRACK_ANYWHERE_RUNTIME_PASSWORD="$RUNTIME_PASSWORD" \
    "$POSTGRES_CONTAINER" \
    bash /docker-entrypoint-initdb.d/001-v2-roles.sh

  TRACK_ANYWHERE_RESTORE_CONTAINER="$POSTGRES_CONTAINER" \
  TRACK_ANYWHERE_RESTORE_USER=track_anywhere \
  TRACK_ANYWHERE_RESTORE_DATABASE="$RESTORE_DATABASE" \
  TRACK_ANYWHERE_RESTORE_S3_OBJECT="$BACKUP_OBJECT" \
  TRACK_ANYWHERE_RESTORE_CONFIRM="$RESTORE_DATABASE" \
  TRACK_ANYWHERE_RESTORE_ISOLATED_TARGET=1 \
    "$ROOT_DIR/scripts/restore-postgres-s3.sh" \
    >"$WORK_DIR/restore-result.txt"

  ta_run_with_timeout "$DOCKER_COMPOSE_TIMEOUT_SECONDS" \
    "${COMPOSE[@]}" run --rm --no-deps \
    -e TRACK_ANYWHERE_DATABASE_URL="$RESTORE_MIGRATOR_URL" \
    migrate
  "${PY[@]}" - "$RESTORE_RUNTIME_URL" <<'PY'
import sys

from sqlalchemy import create_engine, text

from track_anywhere.verification import verify_v2_ledger

report = verify_v2_ledger(sys.argv[1])
if report.status != "PASS":
    raise SystemExit(f"restored ledger verifier failed: {report.to_dict()}")
engine = create_engine(sys.argv[1], pool_pre_ping=True)
try:
    with engine.connect() as connection:
        connection.execute(text("select count(*) from oauth_clients")).scalar_one()
        fixture = connection.execute(
            text(
                "select count(*), min(length(payload)), max(length(payload)) "
                "from backup_stream_regression"
            )
        ).one()
        if fixture != (1024, 16384, 16384):
            raise SystemExit(f"large streaming backup fixture was not restored: {fixture}")
finally:
    engine.dispose()
PY
  ta_run_with_timeout "$DOCKER_CLI_TIMEOUT_SECONDS" docker exec \
    "$POSTGRES_CONTAINER" dropdb -U track_anywhere --force "$RESTORE_DATABASE"
  printf 'backup_restore_roundtrip=PASS\n'
fi

printf 'Exercising public OAuth PKCE and authenticated MCP through the application origin\n'
curl --connect-timeout 3 --max-time "$HTTP_TIMEOUT_SECONDS" -fsS \
  -X POST "$PUBLIC_URL/api/v2/oauth/register" \
  -H 'Content-Type: application/json' \
  --data '{"client_name":"E2E MCP client","redirect_uris":["http://127.0.0.1/callback"],"scope":"book:read ledger:read","grant_types":["authorization_code","refresh_token"],"response_types":["code"],"token_endpoint_auth_method":"none"}' \
  >"$WORK_DIR/mcp-register.json"
MCP_CLIENT_ID="$("${PY[@]}" - "$WORK_DIR/mcp-register.json" <<'PY'
import json
import sys
print(json.load(open(sys.argv[1], encoding="utf-8"))["client_id"])
PY
)"

curl --connect-timeout 3 --max-time "$HTTP_TIMEOUT_SECONDS" -fsS \
  -c "$WORK_DIR/browser-cookies.txt" \
  -X POST "$PUBLIC_URL/api/v2/auth/session/api-key" \
  -H 'Content-Type: application/json' \
  --data "{\"api_key\":\"$RAW_API_KEY\"}" \
  >"$WORK_DIR/browser-login.json"
CSRF_TOKEN="$("${PY[@]}" - "$WORK_DIR/browser-login.json" <<'PY'
import json
import sys
print(json.load(open(sys.argv[1], encoding="utf-8"))["csrf_token"])
PY
)"
CODE_VERIFIER="$("${PY[@]}" - <<'PY'
print("v" * 64)
PY
)"
CODE_CHALLENGE="$("${PY[@]}" - "$CODE_VERIFIER" <<'PY'
import base64
from hashlib import sha256
import sys
print(base64.urlsafe_b64encode(sha256(sys.argv[1].encode()).digest()).rstrip(b"=").decode())
PY
)"

curl --connect-timeout 3 --max-time "$HTTP_TIMEOUT_SECONDS" -fsS \
  -b "$WORK_DIR/browser-cookies.txt" \
  -X POST "$PUBLIC_URL/api/v2/oauth/authorize" \
  -H 'Content-Type: application/json' \
  -H "Origin: $PUBLIC_URL" \
  -H "X-CSRF-Token: $CSRF_TOKEN" \
  --data "{\"response_type\":\"code\",\"client_id\":\"$MCP_CLIENT_ID\",\"redirect_uri\":\"http://127.0.0.1/callback\",\"scope\":\"book:read ledger:read\",\"state\":\"e2e-state\",\"code_challenge\":\"$CODE_CHALLENGE\",\"code_challenge_method\":\"S256\",\"resource\":\"$PUBLIC_URL/mcp\",\"action\":\"approve\"}" \
  >"$WORK_DIR/mcp-authorization.json"
AUTHORIZATION_CODE="$("${PY[@]}" - "$WORK_DIR/mcp-authorization.json" <<'PY'
import json
import sys
from urllib.parse import parse_qs, urlparse
redirect = json.load(open(sys.argv[1], encoding="utf-8"))["redirect_uri"]
print(parse_qs(urlparse(redirect).query)["code"][0])
PY
)"

curl --connect-timeout 3 --max-time "$HTTP_TIMEOUT_SECONDS" -fsS \
  -X POST "$PUBLIC_URL/api/v2/oauth/token" \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  --data-urlencode 'grant_type=authorization_code' \
  --data-urlencode "code=$AUTHORIZATION_CODE" \
  --data-urlencode "client_id=$MCP_CLIENT_ID" \
  --data-urlencode 'redirect_uri=http://127.0.0.1/callback' \
  --data-urlencode "code_verifier=$CODE_VERIFIER" \
  --data-urlencode "resource=$PUBLIC_URL/mcp" \
  >"$WORK_DIR/mcp-token.json"
MCP_ACCESS_TOKEN="$("${PY[@]}" - "$WORK_DIR/mcp-token.json" <<'PY'
import json
import sys
payload = json.load(open(sys.argv[1], encoding="utf-8"))
assert payload["token_type"] == "Bearer"
assert payload["scope"] == "book:read ledger:read"
assert payload["refresh_token"].startswith("rt_")
print(payload["access_token"])
PY
)"

REST_REPLAY_STATUS="$(curl --connect-timeout 3 --max-time "$HTTP_TIMEOUT_SECONDS" -sS \
  -o "$WORK_DIR/mcp-token-rest-replay.json" -w '%{http_code}' \
  "$PUBLIC_URL/api/v2/auth/token-status" \
  -H "Authorization: Bearer $MCP_ACCESS_TOKEN")"
if [[ "$REST_REPLAY_STATUS" != "401" ]]; then
  printf 'expected MCP audience token to be rejected by REST, got HTTP %s\n' \
    "$REST_REPLAY_STATUS" >&2
  exit 1
fi

MCP_HEADERS=(
  -H "Authorization: Bearer $MCP_ACCESS_TOKEN"
  -H 'Accept: application/json, text/event-stream'
  -H 'Content-Type: application/json'
  -H 'MCP-Protocol-Version: 2025-11-25'
)
curl --connect-timeout 3 --max-time "$HTTP_TIMEOUT_SECONDS" -fsS \
  -X POST "$PUBLIC_URL/mcp" "${MCP_HEADERS[@]}" \
  --data '{"jsonrpc":"2.0","id":10,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"e2e","version":"1"}}}' \
  >"$WORK_DIR/mcp-initialize.json"
curl --connect-timeout 3 --max-time "$HTTP_TIMEOUT_SECONDS" -fsS \
  -X POST "$PUBLIC_URL/mcp" "${MCP_HEADERS[@]}" \
  --data '{"jsonrpc":"2.0","id":11,"method":"tools/list"}' \
  >"$WORK_DIR/mcp-tools.json"
curl --connect-timeout 3 --max-time "$HTTP_TIMEOUT_SECONDS" -fsS \
  -X POST "$PUBLIC_URL/mcp" "${MCP_HEADERS[@]}" \
  --data '{"jsonrpc":"2.0","id":12,"method":"tools/call","params":{"name":"ledger_list_books","arguments":{}}}' \
  >"$WORK_DIR/mcp-books.json"
assert_json_expr "$WORK_DIR/mcp-initialize.json" \
  "data['result']['protocolVersion'] == '2025-11-25'"
"${PY[@]}" - "$WORK_DIR/mcp-tools.json" <<'PY'
import json
import sys

tools = json.load(open(sys.argv[1], encoding="utf-8"))["result"]["tools"]
tools_by_name = {tool["name"]: tool for tool in tools}
read_tools = {
    "ledger_get_account",
    "ledger_get_balances",
    "ledger_get_transaction",
    "ledger_list_accounts",
    "ledger_list_assets",
    "ledger_list_books",
    "ledger_list_categories",
    "ledger_list_transactions",
}
ledger_write_tools = {
    "ledger_record_adjustment",
    "ledger_record_credit_card_charge",
    "ledger_record_credit_card_payment",
    "ledger_record_expense",
    "ledger_record_transfer",
}
catalog_write_tools = {
    "ledger_create_account",
    "ledger_create_asset",
    "ledger_create_book",
}
assert set(tools_by_name) == read_tools | ledger_write_tools | catalog_write_tools
for name, tool in tools_by_name.items():
    if name in ledger_write_tools:
        scopes = ["ledger:read", "ledger:write"]
    elif name in catalog_write_tools:
        scopes = ["book:read", "book:write", "ledger:read"]
    elif name == "ledger_list_books":
        scopes = ["book:read", "ledger:read"]
    else:
        scopes = ["ledger:read"]
    expected_security = [{"type": "oauth2", "scopes": scopes}]
    assert tool["securitySchemes"] == tool["_meta"]["securitySchemes"]
    assert tool["securitySchemes"] == expected_security
    assert tool["annotations"]["readOnlyHint"] is (name in read_tools)
print("mcp_tool_descriptors=PASS")
PY
assert_json_expr "$WORK_DIR/mcp-books.json" \
  "data['result']['structuredContent']['items'] == [{'book_id': '$BOOK_ID', 'current_name': 'Local E2E Book', 'base_asset_code': None, 'write_state': 'active'}]"

if [[ -n "$RESULT_FILE" ]]; then
  "${PY[@]}" - "$RESULT_FILE" "$BOOK_ID" "$TRANSACTION_ID" <<'PY'
import json
import sys

payload = {
    "book_id": sys.argv[2],
    "fresh_connection_balance_visibility": True,
    "transaction_id": sys.argv[3],
}
with open(sys.argv[1], "x", encoding="utf-8") as output:
    json.dump(payload, output, sort_keys=True, separators=(",", ":"))
    output.write("\n")
PY
fi

printf 'Track Anywhere local V2 E2E passed: api=%s book=%s tx=%s\n' \
  "$API_URL" "$BOOK_ID" "$TRANSACTION_ID"
