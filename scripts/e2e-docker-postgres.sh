#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT_NAME="${TRACK_ANYWHERE_E2E_PROJECT:-track-anywhere-e2e-$$}"
COMPOSE_FILE="$ROOT_DIR/compose.e2e.yaml"
WORK_DIR="$(mktemp -d)"
TOKEN_FILE="$WORK_DIR/machine-token"
DOCKER_CLI_TIMEOUT_SECONDS="${TRACK_ANYWHERE_DOCKER_CLI_TIMEOUT_SECONDS:-20}"
DOCKER_COMPOSE_TIMEOUT_SECONDS="${TRACK_ANYWHERE_DOCKER_COMPOSE_TIMEOUT_SECONDS:-900}"

pick_port() {
  python - <<'PY'
import socket
with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
    sock.bind(("127.0.0.1", 0))
    print(sock.getsockname()[1])
PY
}

export TRACK_ANYWHERE_E2E_API_BIND="${TRACK_ANYWHERE_E2E_API_BIND:-127.0.0.1}"
export TRACK_ANYWHERE_E2E_POSTGRES_BIND="${TRACK_ANYWHERE_E2E_POSTGRES_BIND:-127.0.0.1}"
export TRACK_ANYWHERE_E2E_API_PORT="${TRACK_ANYWHERE_E2E_API_PORT:-$(pick_port)}"
export TRACK_ANYWHERE_E2E_POSTGRES_PORT="${TRACK_ANYWHERE_E2E_POSTGRES_PORT:-$(pick_port)}"

API_URL="http://${TRACK_ANYWHERE_E2E_API_BIND}:${TRACK_ANYWHERE_E2E_API_PORT}"
POSTGRES_URL="postgresql+psycopg://track_anywhere:track_anywhere@${TRACK_ANYWHERE_E2E_POSTGRES_BIND}:${TRACK_ANYWHERE_E2E_POSTGRES_PORT}/track_anywhere"
COMPOSE=(docker compose -p "$PROJECT_NAME" -f "$COMPOSE_FILE")
TA=(uv run --extra postgres python -m track_anywhere_cli.main)
PY=(uv run --extra postgres python)
export PYTHONPATH="$ROOT_DIR/backend/app:$ROOT_DIR/cli${PYTHONPATH:+:$PYTHONPATH}"

run_with_timeout() {
  local timeout_seconds="$1"
  shift
  python - "$timeout_seconds" "$@" <<'PY'
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

json_get() {
  local file="$1"
  local expr="$2"
  "${PY[@]}" - "$file" "$expr" <<'PY'
import json
import sys

data = json.loads(open(sys.argv[1], encoding="utf-8").read())
value = data
for part in sys.argv[2].split("."):
    if part.endswith("]"):
        key, _, index = part[:-1].partition("[")
        if key:
            value = value[key]
        value = value[int(index)]
    else:
        value = value[part]
print(value)
PY
}

assert_json_expr() {
  local file="$1"
  local expr="$2"
  "${PY[@]}" - "$file" "$expr" <<'PY'
import json
import sys

data = json.loads(open(sys.argv[1], encoding="utf-8").read())
safe_globals = {"__builtins__": {}, "any": any, "all": all, "isinstance": isinstance, "int": int, "str": str, "len": len}
if not eval(sys.argv[2], safe_globals, {"data": data}):
    raise SystemExit(f"assertion failed: {sys.argv[2]}")
PY
}

printf 'Starting Track Anywhere E2E stack on %s with Postgres port %s\n' "$API_URL" "$TRACK_ANYWHERE_E2E_POSTGRES_PORT"
run_with_timeout "$DOCKER_CLI_TIMEOUT_SECONDS" docker version --format '{{.Server.Version}}' >/dev/null
run_with_timeout "$DOCKER_COMPOSE_TIMEOUT_SECONDS" "${COMPOSE[@]}" up -d --build postgres api

for _ in {1..90}; do
  if curl -fsS "$API_URL/api/v1/ready" >"$WORK_DIR/ready.json"; then
    break
  fi
  sleep 2
done
curl -fsS "$API_URL/api/v1/health" >"$WORK_DIR/health.json"
curl -fsS "$API_URL/api/v1/auth/login" >"$WORK_DIR/login.html"
assert_json_expr "$WORK_DIR/health.json" "data['status'] == 'ok'"
assert_json_expr "$WORK_DIR/ready.json" "data['status'] == 'ok' and data['checks']['migrations'] == 'ok'"

curl -fsS -X POST "$API_URL/api/v1/auth/dev-token" >"$WORK_DIR/dev-token.json"
OWNER_TOKEN="$(json_get "$WORK_DIR/dev-token.json" token)"
curl -fsS \
  -X POST "$API_URL/api/v1/credentials/machine" \
  -H "Authorization: Bearer $OWNER_TOKEN" \
  -H "Content-Type: application/json" \
  -H "X-Idempotency-Key: e2e-machine-token" \
  --data '{"name":"Release E2E machine token","ttl_minutes":5256000,"credential_type":"ci","scopes":["account:read","account:write","category:read","category:write","ledger:read","ledger:confirm","ledger:reverse","book:read"]}' \
  >"$WORK_DIR/machine-token.json"
json_get "$WORK_DIR/machine-token.json" credential.token >"$TOKEN_FILE"
chmod 600 "$TOKEN_FILE"

export TRACK_ANYWHERE_API="$API_URL"
export TRACK_ANYWHERE_SERVICE_URL="$API_URL"
export TRACK_ANYWHERE_TOKEN_FILE="$TOKEN_FILE"
export TRACK_ANYWHERE_AGENT=1

"${TA[@]}" auth status --json >"$WORK_DIR/auth-status.json"
assert_json_expr "$WORK_DIR/auth-status.json" "data['ok'] and data['data']['authenticated'] and data['data']['actor_type'] == 'machine'"

"${TA[@]}" system status --include-counts --json >"$WORK_DIR/system-status.json"
assert_json_expr "$WORK_DIR/system-status.json" "data['ok'] and data['data']['status'] == 'ok' and isinstance(data['data']['counts']['accounts'], int)"

"${TA[@]}" account create "E2E Cash" --type asset --currency CNY --opening-balance 1000 --idempotency-key e2e-cash --json >"$WORK_DIR/cash.json"
CASH_ID="$(json_get "$WORK_DIR/cash.json" data.account.account_id)"

"${TA[@]}" category ensure --kind expense --path "食品 / 外出吃饭" --idempotency-key e2e-category-dineout --json >"$WORK_DIR/category-dineout.json"
DINEOUT_CATEGORY_ID="$(json_get "$WORK_DIR/category-dineout.json" data.category.category_id)"
"${TA[@]}" category find --kind expense --path "食品 / 外出吃饭" --json >"$WORK_DIR/category-find.json"
assert_json_expr "$WORK_DIR/category-find.json" "data['data']['category']['category_id'] == '$DINEOUT_CATEGORY_ID'"

"${TA[@]}" expense record --amount 73 --from-account-id "$CASH_ID" --category-id "$DINEOUT_CATEGORY_ID" --purpose "release e2e lunch" --idempotency-key e2e-expense --json >"$WORK_DIR/expense.json"
TX_ID="$(json_get "$WORK_DIR/expense.json" data.transaction.transaction_id)"
LINE_ID="$(json_get "$WORK_DIR/expense.json" data.transaction.lines[0].line_id)"

"${TA[@]}" tx list --limit 3 --json >"$WORK_DIR/tx-list.json"
assert_json_expr "$WORK_DIR/tx-list.json" "data['ok'] and any(tx['transaction_id'] == '$TX_ID' for tx in data['data']['transactions'])"
"${TA[@]}" summary accounts --group-by currency --json >"$WORK_DIR/summary-accounts.json"
assert_json_expr "$WORK_DIR/summary-accounts.json" "data['ok'] and any(group['currency'] == 'CNY' for group in data['data']['groups'])"

"${TA[@]}" tx snapshot "$TX_ID" --output "$WORK_DIR/snapshot-before.json" --json >"$WORK_DIR/snapshot-command.json"
test -s "$WORK_DIR/snapshot-before.json"

"${TA[@]}" category ensure --kind expense --path "食品 / 饮料" --idempotency-key e2e-category-drinks --json >"$WORK_DIR/category-drinks.json"
DRINK_CATEGORY_ID="$(json_get "$WORK_DIR/category-drinks.json" data.category.category_id)"

"${TA[@]}" tx reclassify "$TX_ID" --line-id "$LINE_ID" --category-id "$DRINK_CATEGORY_ID" --backup-before --backup-dir "$WORK_DIR/backups" --backup-label e2e-reclassify --idempotency-key e2e-reclassify --json >"$WORK_DIR/reclassify.json"
BACKUP_PATH="$(json_get "$WORK_DIR/reclassify.json" data.backup.backup_path)"
test -s "$BACKUP_PATH"

"${TA[@]}" tx show "$TX_ID" --json >"$WORK_DIR/tx-show-after.json"
assert_json_expr "$WORK_DIR/tx-show-after.json" "data['data']['transaction']['lines'][0]['category_path_snapshot']['path'] == '食品 / 饮料'"

"${TA[@]}" data backup --database-url "$POSTGRES_URL" --transaction-id "$TX_ID" --output-dir "$WORK_DIR/backups" --label e2e-postgres --json >"$WORK_DIR/data-backup.json"
DATA_BACKUP_PATH="$(json_get "$WORK_DIR/data-backup.json" data.backup.backup_path)"
test -s "$DATA_BACKUP_PATH"

printf 'Track Anywhere Docker Postgres E2E passed: api=%s tx=%s\n' "$API_URL" "$TX_ID"
