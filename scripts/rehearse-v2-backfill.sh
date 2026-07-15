#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FACTORY=(uv run python "$ROOT_DIR/backend/tests/v2/postgres_factory.py")
PG17_CLIENT="$ROOT_DIR/scripts/pg17-client.sh"
COMPOSE=(docker compose -p track-anywhere-v2-test -f "$ROOT_DIR/compose.e2e.yaml")

DUMP_PATH=""
MANIFEST_PATH=""
OUTPUT_ROOT=""
CREDIT_CARD_REVIEW_PATH=""

usage() {
  echo "usage: rehearse-v2-backfill.sh --dump PATH --manifest PATH --credit-card-review PATH --output-root DIR" >&2
  exit 2
}

if (($# != 8)); then
  usage
fi
while (($#)); do
  case "$1" in
    --dump)
      [[ -z "$DUMP_PATH" ]] || usage
      DUMP_PATH="$2"
      shift 2
      ;;
    --manifest)
      [[ -z "$MANIFEST_PATH" ]] || usage
      MANIFEST_PATH="$2"
      shift 2
      ;;
    --output-root)
      [[ -z "$OUTPUT_ROOT" ]] || usage
      OUTPUT_ROOT="$2"
      shift 2
      ;;
    --credit-card-review)
      [[ -z "$CREDIT_CARD_REVIEW_PATH" ]] || usage
      CREDIT_CARD_REVIEW_PATH="$2"
      shift 2
      ;;
    *) usage ;;
  esac
done

[[ -f "$DUMP_PATH" ]] || { echo "frozen dump is not a regular file" >&2; exit 2; }
[[ -f "$MANIFEST_PATH" ]] || { echo "frozen manifest is not a regular file" >&2; exit 2; }
[[ -f "$CREDIT_CARD_REVIEW_PATH" ]] || { echo "credit-card review is not a regular file" >&2; exit 2; }
[[ ! -e "$OUTPUT_ROOT" ]] || { echo "output root already exists" >&2; exit 2; }

SOURCE_URL=""
SOURCE_READ_ONLY_URL=""
TARGET_A_URL=""
TARGET_B_URL=""

cleanup_best_effort() {
  if [[ -n "$SOURCE_URL" ]]; then
    "${FACTORY[@]}" drop --url "$SOURCE_URL" >/dev/null 2>&1 || true
  fi
  if [[ -n "$TARGET_A_URL" ]]; then
    "${FACTORY[@]}" drop --url "$TARGET_A_URL" >/dev/null 2>&1 || true
  fi
  if [[ -n "$TARGET_B_URL" ]]; then
    "${FACTORY[@]}" drop --url "$TARGET_B_URL" >/dev/null 2>&1 || true
  fi
}

cleanup_on_exit() {
  local status=$?
  trap - EXIT
  cleanup_best_effort
  exit "$status"
}

# The trap is installed before this process creates any directory or database.
trap cleanup_on_exit EXIT
mkdir "$OUTPUT_ROOT"

TRACK_ANYWHERE_FROZEN_V1_DUMP="$DUMP_PATH" \
TRACK_ANYWHERE_FROZEN_V1_MANIFEST="$MANIFEST_PATH" \
uv run python - <<'PY'
import os
from pathlib import Path

from backend.tools.backfill_v1.manifest import read_manifest, sha256_file

dump = Path(os.environ["TRACK_ANYWHERE_FROZEN_V1_DUMP"])
manifest = read_manifest(Path(os.environ["TRACK_ANYWHERE_FROZEN_V1_MANIFEST"]))
if sha256_file(dump) != manifest.dump_sha256:
    raise SystemExit("frozen dump SHA-256 does not match its manifest")
if not manifest.source_revision:
    raise SystemExit("frozen manifest has no source revision")
PY

"${COMPOSE[@]}" up -d --wait postgres
for client in psql pg_restore pg_dump; do
  client_version="$("$PG17_CLIENT" "$client" --version)"
  [[ "$client_version" =~ \(PostgreSQL\)[[:space:]]17([.]|$) ]] || {
    echo "pinned PostgreSQL client did not report major version 17" >&2
    exit 1
  }
done

SOURCE_URL="$("${FACTORY[@]}" create --purpose backfill_source --schema empty --emit-role migrator)"
SOURCE_OWNER="$("${FACTORY[@]}" role-name --kind owner)"
MIGRATOR_ROLE="$("${FACTORY[@]}" role-name --kind migrator)"
SOURCE_LIBPQ_URL="$("${FACTORY[@]}" libpq-url --url "$SOURCE_URL" --host postgres --port 5432)"
TRACK_ANYWHERE_LIBPQ_URL="$SOURCE_LIBPQ_URL" uv run python - <<'PY'
import os
from sqlalchemy.engine import make_url

url = make_url(os.environ["TRACK_ANYWHERE_LIBPQ_URL"])
if (url.drivername, url.host, url.port) != ("postgresql", "postgres", 5432):
    raise SystemExit("libpq URL is not bound to postgresql://postgres:5432")
PY

"$PG17_CLIENT" pg_restore \
  --dbname "$SOURCE_LIBPQ_URL" \
  --exit-on-error \
  --no-owner \
  --no-acl \
  --role "$SOURCE_OWNER" < "$DUMP_PATH"
"$PG17_CLIENT" psql \
  --dbname "$SOURCE_LIBPQ_URL" \
  --set ON_ERROR_STOP=1 \
  --command "set role \"$SOURCE_OWNER\"; grant usage on schema public to \"$MIGRATOR_ROLE\"; grant select on all tables in schema public to \"$MIGRATOR_ROLE\";"
unset SOURCE_LIBPQ_URL

SOURCE_READ_ONLY_URL="$("${FACTORY[@]}" read-only-url --url "$SOURCE_URL")"
TARGET_A_URL="$("${FACTORY[@]}" create --purpose backfill_target_a --schema v2 --emit-role runtime)"
TARGET_B_URL="$("${FACTORY[@]}" create --purpose backfill_target_b --schema v2 --emit-role runtime)"
RUNTIME_ROLE="$("${FACTORY[@]}" role-name --kind runtime)"

for target_variable in TARGET_A_URL TARGET_B_URL; do
  TRACK_ANYWHERE_TARGET_URL="${!target_variable}" uv run python - <<'PY'
import os

from backend.tools.backfill_v1.config import current_v2_head
from backend.tools.backfill_v1.manifest import assert_target_ready

assert_target_ready(
    os.environ["TRACK_ANYWHERE_TARGET_URL"],
    expected_revision=current_v2_head(),
)
PY
done

TRACK_ANYWHERE_FROZEN_V1_DUMP="$DUMP_PATH" \
TRACK_ANYWHERE_FROZEN_V1_MANIFEST="$MANIFEST_PATH" \
TRACK_ANYWHERE_FROZEN_V1_SOURCE_URL="$SOURCE_READ_ONLY_URL" \
uv run pytest \
  "$ROOT_DIR/backend/tests/v2/backfill/test_frozen_dump_contract.py" \
  -m frozen_dump -q

TRACK_ANYWHERE_FROZEN_V1_SOURCE_URL="$SOURCE_READ_ONLY_URL" \
TRACK_ANYWHERE_SOURCE_COUNTS_OUTPUT="$OUTPUT_ROOT/source-counts.json" \
uv run python - <<'PY'
import json
import os
from pathlib import Path

from sqlalchemy import create_engine, text

tables = ("accounts", "postings", "transaction_lines", "transactions")
engine = create_engine(os.environ["TRACK_ANYWHERE_FROZEN_V1_SOURCE_URL"])
try:
    with engine.connect() as connection:
        counts = {
            table: int(
                connection.execute(
                    text(f'SELECT count(*) FROM public."{table}"')
                ).scalar_one()
            )
            for table in tables
        }
finally:
    engine.dispose()
output = Path(os.environ["TRACK_ANYWHERE_SOURCE_COUNTS_OUTPUT"])
with output.open("x", encoding="utf-8") as stream:
    stream.write(json.dumps(counts, separators=(",", ":"), sort_keys=True) + "\n")
PY

TZ=UTC LC_ALL=C PYTHONHASHSEED=0 \
uv run python -m backend.tools.backfill_v1 run \
  --source-url "$SOURCE_READ_ONLY_URL" \
  --target-url "$TARGET_A_URL" \
  --dump "$DUMP_PATH" \
  --manifest "$MANIFEST_PATH" \
  --credit-card-review "$CREDIT_CARD_REVIEW_PATH" \
  --batch-size 37 \
  --workers 1 \
  --shuffle-seed 0 \
  --output-dir "$OUTPUT_ROOT/run-a"

RUN_A_MANIFEST="$OUTPUT_ROOT/run-a/extraction/manifest.json"
[[ -f "$RUN_A_MANIFEST" ]] || {
  echo "first backfill run omitted its canonical extraction manifest" >&2
  exit 1
}
TRACK_ANYWHERE_FROZEN_MANIFEST="$MANIFEST_PATH" \
TRACK_ANYWHERE_RUN_A_MANIFEST="$RUN_A_MANIFEST" \
uv run python - <<'PY'
import os
from pathlib import Path

from backend.tools.backfill_v1.manifest import read_manifest

frozen = read_manifest(Path(os.environ["TRACK_ANYWHERE_FROZEN_MANIFEST"]))
run_a = read_manifest(Path(os.environ["TRACK_ANYWHERE_RUN_A_MANIFEST"]))
if not run_a.tables:
    raise SystemExit("canonical extraction manifest has no source tables")
if (
    run_a.dump_sha256 != frozen.dump_sha256
    or run_a.source_revision != frozen.source_revision
):
    raise SystemExit("canonical extraction is not bound to the frozen source")
if frozen.tables and run_a.to_dict() != frozen.to_dict():
    raise SystemExit("canonical extraction does not match the fixed full manifest")
PY

TZ=Pacific/Auckland LC_ALL=en_US.UTF-8 PYTHONHASHSEED=731 \
uv run python -m backend.tools.backfill_v1 run \
  --source-url "$SOURCE_READ_ONLY_URL" \
  --target-url "$TARGET_B_URL" \
  --dump "$DUMP_PATH" \
  --manifest "$RUN_A_MANIFEST" \
  --credit-card-review "$CREDIT_CARD_REVIEW_PATH" \
  --batch-size 13 \
  --workers 4 \
  --shuffle-seed 731 \
  --output-dir "$OUTPUT_ROOT/run-b"

RUN_B_MANIFEST="$OUTPUT_ROOT/run-b/extraction/manifest.json"
[[ -f "$RUN_B_MANIFEST" ]] || {
  echo "second backfill run omitted its canonical extraction manifest" >&2
  exit 1
}
cmp -s "$RUN_A_MANIFEST" "$RUN_B_MANIFEST" || {
  echo "canonical extraction manifests differ between rehearsals" >&2
  exit 1
}
TRACK_ANYWHERE_FROZEN_MANIFEST="$MANIFEST_PATH" \
TRACK_ANYWHERE_RUN_A_MANIFEST="$RUN_A_MANIFEST" \
TRACK_ANYWHERE_RUN_B_MANIFEST="$RUN_B_MANIFEST" \
uv run python - <<'PY'
import os
from pathlib import Path

from backend.tools.backfill_v1.manifest import read_manifest

frozen = read_manifest(Path(os.environ["TRACK_ANYWHERE_FROZEN_MANIFEST"]))
run_a = read_manifest(Path(os.environ["TRACK_ANYWHERE_RUN_A_MANIFEST"]))
run_b = read_manifest(Path(os.environ["TRACK_ANYWHERE_RUN_B_MANIFEST"]))
if run_a.to_dict() != run_b.to_dict():
    raise SystemExit("canonical extraction manifests are not identical")
if not run_a.tables:
    raise SystemExit("canonical extraction manifest has no source tables")
if (
    run_a.dump_sha256 != frozen.dump_sha256
    or run_a.source_revision != frozen.source_revision
):
    raise SystemExit("canonical extraction is not bound to the frozen source")
if frozen.tables and run_a.to_dict() != frozen.to_dict():
    raise SystemExit("canonical extraction does not match the fixed full manifest")
PY

uv run python -m backend.tools.backfill_v1.verify \
  --source-url "$SOURCE_READ_ONLY_URL" \
  --target-url "$TARGET_A_URL" \
  --manifest "$RUN_A_MANIFEST" \
  --credit-card-review "$CREDIT_CARD_REVIEW_PATH" \
  --output "$OUTPUT_ROOT/run-a/independent-verification.json"
uv run python -m backend.tools.backfill_v1.verify \
  --source-url "$SOURCE_READ_ONLY_URL" \
  --target-url "$TARGET_B_URL" \
  --manifest "$RUN_B_MANIFEST" \
  --credit-card-review "$CREDIT_CARD_REVIEW_PATH" \
  --output "$OUTPUT_ROOT/run-b/independent-verification.json"
uv run python -m backend.tools.backfill_v1.verify_determinism \
  --run-a "$OUTPUT_ROOT/run-a/independent-verification.json" \
  --run-b "$OUTPUT_ROOT/run-b/independent-verification.json" \
  --output "$OUTPUT_ROOT/determinism.json"

cleanup_strict() {
  local database_url
  for database_url in "$SOURCE_URL" "$TARGET_A_URL" "$TARGET_B_URL"; do
    "${FACTORY[@]}" drop --url "$database_url"
  done
  for database_url in "$SOURCE_URL" "$TARGET_A_URL" "$TARGET_B_URL"; do
    "${FACTORY[@]}" assert-absent --url "$database_url"
  done
}

# Success requires strict cleanup and independent absence readback.  The EXIT
# trap remains armed until the final PASS report has been atomically installed.
cleanup_strict

TRACK_ANYWHERE_REPORT_ROOT="$OUTPUT_ROOT" \
TRACK_ANYWHERE_RUNTIME_ROLE="$RUNTIME_ROLE" \
TRACK_ANYWHERE_MIGRATOR_ROLE="$MIGRATOR_ROLE" \
uv run python - <<'PY'
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import uuid


root = Path(os.environ["TRACK_ANYWHERE_REPORT_ROOT"])
report_paths = (
    root / "run-a" / "independent-verification.json",
    root / "run-b" / "independent-verification.json",
)
reports = tuple(json.loads(path.read_text(encoding="utf-8")) for path in report_paths)
determinism = json.loads((root / "determinism.json").read_text(encoding="utf-8"))
source_counts = json.loads((root / "source-counts.json").read_text(encoding="utf-8"))
if any(report.get("status") != "PASS" for report in reports):
    raise SystemExit("independent verification did not pass")
if determinism.get("status") != "PASS":
    raise SystemExit("independent determinism comparison did not pass")
if reports[0].get("quarantine_count") != reports[1].get("quarantine_count"):
    raise SystemExit("quarantine counts differ between rehearsals")

event_evidence = {
    "book_terminal_hashes": reports[0].get("book_terminal_hashes"),
    "counts": reports[0].get("counts"),
    "receipt_count": reports[0].get("receipt_count"),
    "snapshot_id": reports[0].get("snapshot_id"),
    "credit_card_review_hash": reports[0].get("credit_card_review_hash"),
}
event_hash = hashlib.sha256(
    json.dumps(
        event_evidence,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
).hexdigest()
independent_hashes = {
    label: hashlib.sha256(path.read_bytes()).hexdigest()
    for label, path in zip(("run_a", "run_b"), report_paths, strict=True)
}
summary = {
    "book_terminal_hashes": reports[0].get("book_terminal_hashes"),
    "event_hash": event_hash,
    "independent_report_hashes": independent_hashes,
    "manifest_hash": reports[0].get("manifest_hash"),
    "credit_card_review_hash": reports[0].get("credit_card_review_hash"),
    "projection_hashes": reports[0].get("projection_hashes"),
    "quarantine_count": reports[0].get("quarantine_count"),
    "receipt_count": reports[0].get("receipt_count"),
    "roles": {
        "migrator": os.environ["TRACK_ANYWHERE_MIGRATOR_ROLE"],
        "runtime": os.environ["TRACK_ANYWHERE_RUNTIME_ROLE"],
    },
    "run_id": str(uuid.uuid4()),
    "snapshot_id": reports[0].get("snapshot_id"),
    "source_counts": source_counts,
    "status": "PASS",
}
for required in (
    "book_terminal_hashes",
    "manifest_hash",
    "credit_card_review_hash",
    "projection_hashes",
    "quarantine_count",
    "receipt_count",
    "snapshot_id",
):
    if summary[required] is None:
        raise SystemExit(f"independent report omitted {required}")

temporary = root / ".summary.json.tmp"
destination = root / "summary.json"
if destination.exists():
    raise SystemExit("summary destination unexpectedly already exists")
with temporary.open("x", encoding="utf-8") as stream:
    stream.write(
        json.dumps(summary, allow_nan=False, separators=(",", ":"), sort_keys=True)
        + "\n"
    )
os.replace(temporary, destination)
PY

trap - EXIT
