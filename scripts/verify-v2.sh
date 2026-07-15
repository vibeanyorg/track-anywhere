#!/usr/bin/env bash
set -euo pipefail

: "${TRACK_ANYWHERE_TEST_POSTGRES_ADMIN_URL:?required isolated PG17 admin URL}"
: "${TRACK_ANYWHERE_TEST_POSTGRES_MIGRATOR_BASE_URL:?required isolated PG17 migrator base URL}"
: "${TRACK_ANYWHERE_TEST_POSTGRES_RUNTIME_BASE_URL:?required isolated PG17 runtime base URL}"

# postgres_factory rejects non-loopback clusters and non-distinct identities.
export TRACK_ANYWHERE_ALLOW_EXTERNAL_TEST_DATABASE=1
unset TRACK_ANYWHERE_TEST_POSTGRES_URL TRACK_ANYWHERE_DATABASE_URL

uv sync --locked --extra postgres

# Each lane is explicit so CI and release evidence can identify the failing gate.
uv run --extra postgres pytest backend/tests/v2/unit -q
uv run --extra postgres pytest backend/tests/v2/postgres backend/tests/v2/concurrency -q
uv run --extra postgres pytest backend/tests/v2/replay backend/tests/v2/backfill -m 'not frozen_dump' -q
uv run --extra postgres pytest backend/tests/v2/contract cli/tests contract_tests -q

npm --prefix frontend ci
npm --prefix frontend run test:proxy
npm --prefix frontend run lint
npm --prefix frontend run build

V2_ALEMBIC_CHECK_URL=
cleanup_verify_v2() {
  if [ -n "$V2_ALEMBIC_CHECK_URL" ]; then
    uv run --extra postgres python backend/tests/v2/postgres_factory.py drop \
      --url "$V2_ALEMBIC_CHECK_URL" || true
  fi
}
trap cleanup_verify_v2 EXIT

V2_ALEMBIC_CHECK_URL="$(
  uv run --extra postgres python backend/tests/v2/postgres_factory.py create \
    --purpose verify-v2 --schema empty --emit-role migrator
)"
export TRACK_ANYWHERE_DB_RUNTIME_ROLE="$(
  uv run --extra postgres python backend/tests/v2/postgres_factory.py \
    role-name --kind runtime
)"

TRACK_ANYWHERE_DATABASE_URL="$V2_ALEMBIC_CHECK_URL" \
  uv run --extra postgres alembic upgrade head
TRACK_ANYWHERE_DATABASE_URL="$V2_ALEMBIC_CHECK_URL" \
  uv run --extra postgres alembic check

cleanup_verify_v2
V2_ALEMBIC_CHECK_URL=
trap - EXIT
