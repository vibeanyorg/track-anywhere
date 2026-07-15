# V2 isolated staging checklist

This gate is local and disposable. It must not contact the production database,
the stable backend, or a cloud control plane. The no-registry guarantee applies
from the staging harness invocation onward. Separate image build preparation may
pull base images from an image registry when they are absent locally; that build
is not claimed to be offline, must finish before the harness starts, and must not
push images. The terminal condition is **no production deploy**. A production
cutover needs separate user authorization after this gate.

## Preconditions

- Work from a clean tracked tree whose `HEAD` is the source commit being tested.
- The staging script, nested E2E script, compose file, PostgreSQL role-bootstrap
  script, checklist, harness test, and `.dockerignore` must all be tracked by
  that source commit and byte-identical to the checked-out files. The harness
  rejects an untracked replacement.
- Build API and web images from `git archive <source-commit>`, label each image
  `org.opencontainers.image.revision=<source-commit>`, and do not push them.
- Supply both `TRACK_ANYWHERE_E2E_API_IMAGE` and
  `TRACK_ANYWHERE_E2E_WEB_IMAGE`; the harness refuses image substitution.
- Generate a caller-supplied UUID for every attempt. Pass a matching,
  nonexistent report directory named
  `output/v2-staging-<source-commit>-<run-id>`. A failed run directory remains
  diagnostic evidence and never blocks a retry with a fresh UUID.
- All published PostgreSQL, API, and web ports must bind to loopback.
- Use only a local Docker daemon reached through a Unix or Windows named-pipe
  endpoint. The harness rejects TCP, SSH, HTTP, and other remote Docker contexts
  before contacting the daemon or creating a resource.
- API, web, and `postgres:17-alpine` images must already exist locally. Every
  staging `compose up`/`compose run` uses `--pull never`; a registry is never a
  fallback.

Example preparation (shown for a future authorized local run; not executed by
this implementation task):

```bash
set -euo pipefail
SOURCE_COMMIT="$(git rev-parse HEAD)"
RUN_ID="$(uv run python -c 'import uuid; print(uuid.uuid4())')"
REPORT_DIR="output/v2-staging-$SOURCE_COMMIT-$RUN_ID"
test -z "$(git status --porcelain --untracked-files=no)"
test ! -e "$REPORT_DIR"

git archive --format=tar "$SOURCE_COMMIT" |
  docker build --label "org.opencontainers.image.revision=$SOURCE_COMMIT" \
    --target api-runtime -t track-anywhere-api:v2-staging -
git archive --format=tar "$SOURCE_COMMIT" |
  docker build --label "org.opencontainers.image.revision=$SOURCE_COMMIT" \
    --target web-runtime -t track-anywhere-web:v2-staging -

TRACK_ANYWHERE_E2E_API_IMAGE=track-anywhere-api:v2-staging \
TRACK_ANYWHERE_E2E_WEB_IMAGE=track-anywhere-web:v2-staging \
bash scripts/staging-v2-smoke.sh \
  --source-commit "$SOURCE_COMMIT" \
  --run-id "$RUN_ID" \
  --report-dir "$REPORT_DIR"
```

## Fail-closed checks

The harness emits `status=PASS` only after every item below succeeds **and** the
named migration container, compose containers, and volume have been removed.
Its EXIT trap writes a secret-free failure report, retains redacted diagnostics,
retries best-effort cleanup, and preserves the original nonzero status. A
teardown failure is itself a hard failure and can never leave a PASS report.

- A clean migration reaches the single Alembic head through the migrator DSN.
- PostgreSQL reports major version PostgreSQL 17.
- Database owner, migrator, and runtime are three distinct identities. This
  proves the distinct migrator and runtime boundary; API and
  web smoke use the non-owner runtime DSN only.
- Runtime cannot update immutable ledger events or disable database triggers.
- Readiness fails closed and reports both database and schema checks healthy.
- V2 API and CLI smoke create, query, classify, and reverse a transaction.
- The resulting balances are visible from a fresh connection under the runtime
  role, proving there is no process-local source of truth.
- The legacy surface has no `/api/v1` route.
- Stored event positions, previous hashes, terminal hash chain, and Book head
  agree under the independent reducer.
- Async projection lag converges to zero; independent replay agrees with online
  synchronous projections.
- Database Alembic head equals the head read from the exact API image.
- API, migration, and web containers use the expected content-addressed image
  IDs. The exact running-container image IDs and revision labels must match the
  two prevalidated images and source SHA.
- Local image IDs are validated as `sha256:` content digests. Registry
  `RepoDigests` are recorded when present but are not manufactured by pushing.
- Web-to-API proxy health succeeds on a loopback-only published port, returns
  HTTP 200 without a redirect, normalizes to the `application/json` media type,
  and has the exact V2 health JSON body
  `{"api_version":"v2","status":"ok"}`. A wrong MIME type, HTML, or arbitrary
  JSON fails closed.

`TRACK_ANYWHERE_E2E_NO_BUILD=1` is accepted only with
`TRACK_ANYWHERE_E2E_EXISTING_STACK=1`. That flow performs HTTP/CLI and fresh
connection checks only: it cannot call a build, recreate the database, run a
migration, bring services up/down, or silently choose another image/database.

## Independent acceptance

The smoke harness never updates the accepted-run pointer. The outer caller
independently validates the report status, source SHA, and caller UUID, then
atomically replaces a source-commit-specific one-line pointer:

```bash
set -euo pipefail
REPORT_JSON="$REPORT_DIR/verification.json"
uv run python - "$REPORT_JSON" "$SOURCE_COMMIT" "$RUN_ID" <<'PY'
import json
import sys

report = json.load(open(sys.argv[1], encoding="utf-8"))
assert report["status"] == "PASS"
assert report["source_commit"] == sys.argv[2]
assert report["run_id"] == sys.argv[3]
assert report["production_deploy"] == "NOT_PERFORMED"
PY

POINTER="output/v2-staging-$SOURCE_COMMIT-accepted"
TEMPORARY="$POINTER.tmp-$RUN_ID"
printf '%s\n' "$(basename "$REPORT_DIR")" >"$TEMPORARY"
mv -f "$TEMPORARY" "$POINTER"
test "$(wc -l <"$POINTER" | tr -d ' ')" = 1
```

Before trusting the pointer later, re-read its single basename, require the
source-commit prefix, open that directory's `verification.json`, and repeat all
three report assertions. Never infer acceptance merely from directory presence.

## Stop condition

Record the source commit and run UUID in the evidence document, then stop. Do
not tag, push images, change production DSNs, replace the stable runtime, or
deploy. This checklist validates only a clean V2 database and exact committed
images; current HEAD contains no historical import gate.
