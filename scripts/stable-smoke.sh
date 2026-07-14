#!/usr/bin/env sh
set -eu

BASE_URL=${TRACK_ANYWHERE_STABLE_BASE_URL:-http://127.0.0.1:12306}
HTTP_TIMEOUT_SECONDS=${TRACK_ANYWHERE_HTTP_TIMEOUT_SECONDS:-5}
TMP_DIR=$(mktemp -d)
trap 'rm -rf "$TMP_DIR"' EXIT

run_with_timeout() {
  timeout_seconds=$1
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

run_http() {
  label=$1
  path=$2
  run_with_timeout "$HTTP_TIMEOUT_SECONDS" curl -fsS "$BASE_URL$path" \
    >"$TMP_DIR/$label.json"
  printf 'ok http %-18s\n' "$label"
}

python3 - "$BASE_URL" <<'PY'
import sys
from urllib.parse import urlparse

parsed = urlparse(sys.argv[1])
if parsed.scheme not in {"http", "https"} or not parsed.netloc:
    raise SystemExit("TRACK_ANYWHERE_STABLE_BASE_URL must be an absolute HTTP URL")
PY

run_http health /api/v2/health
run_http ready /api/v2/ready

python3 - "$TMP_DIR/health.json" "$TMP_DIR/ready.json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    health = json.load(handle)
with open(sys.argv[2], encoding="utf-8") as handle:
    ready = json.load(handle)

if health != {"status": "ok", "api_version": "v2"}:
    raise SystemExit(f"unexpected V2 health response: {health}")
if (
    ready.get("status") != "ok"
    or ready.get("api_version") != "v2"
    or ready.get("checks") != {"database": "ok", "schema": "ok"}
):
    raise SystemExit(f"unexpected V2 readiness response: {ready}")
PY

printf 'Stable V2 health/readiness smoke passed for %s\n' "$BASE_URL"
printf 'Ledger post/query/classify/reverse coverage: scripts/e2e-docker-postgres.sh\n'
