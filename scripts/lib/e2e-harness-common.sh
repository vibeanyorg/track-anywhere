# Shared mechanics for the local E2E and isolated staging harnesses.
# Keep policy decisions in the calling harnesses; this file only owns identical
# command execution and disposable PostgreSQL bootstrap behavior.

ta_pick_loopback_port() {
  python3 - <<'PY'
import socket
with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
    sock.bind(("127.0.0.1", 0))
    print(sock.getsockname()[1])
PY
}

ta_require_postgres_identifier() {
  local value="$1"
  local label="$2"
  if [[ ! "$value" =~ ^[a-z_][a-z0-9_]*$ ]] || (( ${#value} > 63 )); then
    printf '%s must be a safe PostgreSQL identifier\n' "$label" >&2
    return 2
  fi
}

ta_run_with_timeout() {
  local timeout_seconds="$1"
  shift
  python3 -c '
import subprocess
import sys

timeout_seconds = float(sys.argv[1])
command = sys.argv[2:]
try:
    raise SystemExit(subprocess.run(command, timeout=timeout_seconds).returncode)
except subprocess.TimeoutExpired:
    print(f"command timed out after {timeout_seconds:g}s", file=sys.stderr)
    raise SystemExit(124) from None
' "$timeout_seconds" "$@"
}

ta_initialize_database_owner() {
  local timeout_seconds="$1"
  local owner_role="$2"
  shift 2
  ta_run_with_timeout "$timeout_seconds" \
    "$@" exec -T postgres \
    psql --username track_anywhere --dbname postgres --set ON_ERROR_STOP=1 \
    --command "ALTER DATABASE track_anywhere OWNER TO \"$owner_role\""
}
