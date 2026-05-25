#!/usr/bin/env sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
BASE_URL=${TRACK_ANYWHERE_STABLE_BASE_URL:-http://127.0.0.1:12306}
STABLE_DIR=${TRACK_ANYWHERE_STABLE_DIR:-/Users/xuyanyue/Documents/track-anywhere-stable-backend}
TOKEN_FILE=${TRACK_ANYWHERE_TOKEN_FILE:-$STABLE_DIR/secrets/ta-token}
CLI_BUDGET_SECONDS=${TRACK_ANYWHERE_CLI_BUDGET_SECONDS:-2.0}
TMP_DIR=$(mktemp -d)
trap 'rm -rf "$TMP_DIR"' EXIT

export TRACK_ANYWHERE_API="$BASE_URL"
export TRACK_ANYWHERE_SERVICE_URL="$BASE_URL"
export TRACK_ANYWHERE_TOKEN_FILE="$TOKEN_FILE"

run_ta() {
  if [ -n "${TRACK_ANYWHERE_TA_BIN:-}" ]; then
    "$TRACK_ANYWHERE_TA_BIN" "$@"
  elif command -v uv >/dev/null 2>&1 && [ -f "$ROOT/pyproject.toml" ]; then
    (cd "$ROOT" && uv run ta "$@")
  else
    ta "$@"
  fi
}

elapsed_seconds() {
  python3 - "$1" <<'PY'
import sys
import time

print(f"{time.monotonic() - float(sys.argv[1]):.3f}")
PY
}

assert_budget() {
  python3 - "$1" "$CLI_BUDGET_SECONDS" <<'PY'
import sys

elapsed = float(sys.argv[1])
budget = float(sys.argv[2])
raise SystemExit(0 if elapsed <= budget else 1)
PY
}

check_cli_json() {
  python3 - "$1" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    payload = json.load(handle)
if not payload.get("ok"):
    raise SystemExit(f"CLI command failed: {payload}")
PY
}

run_http() {
  label=$1
  path=$2
  start=$(python3 -c 'import time; print(time.monotonic())')
  curl -fsS "$BASE_URL$path" > "$TMP_DIR/$label.json"
  elapsed=$(elapsed_seconds "$start")
  printf 'ok http %-18s %ss\n' "$label" "$elapsed"
}

run_cli() {
  label=$1
  shift
  start=$(python3 -c 'import time; print(time.monotonic())')
  run_ta --base-url "$BASE_URL" "$@" --json > "$TMP_DIR/$label.json"
  elapsed=$(elapsed_seconds "$start")
  check_cli_json "$TMP_DIR/$label.json"
  if ! assert_budget "$elapsed"; then
    printf 'slow cli %-18s %ss > %ss\n' "$label" "$elapsed" "$CLI_BUDGET_SECONDS" >&2
    exit 1
  fi
  printf 'ok cli  %-18s %ss\n' "$label" "$elapsed"
}

if [ ! -s "$TOKEN_FILE" ]; then
  printf 'Stable token file is missing or empty: %s\n' "$TOKEN_FILE" >&2
  exit 1
fi

run_http health /api/v1/health
run_http ready /api/v1/ready

run_cli auth-status auth status
run_cli account-list account list
run_cli category-list category list
run_cli tx-list tx list --limit 5
run_cli summary-accounts summary accounts
run_cli summary-categories summary categories
run_cli user-list user list
run_cli credit-card-list credit-card list
run_cli recurring-list recurring list

printf 'Stable smoke passed for %s\n' "$BASE_URL"
