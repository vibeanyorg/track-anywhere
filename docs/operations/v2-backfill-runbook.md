# V2 frozen-dump backfill rehearsal

This runbook is a local, disposable PostgreSQL 17 rehearsal. It does not deploy,
change a production DSN, contact the stable runtime, or cut traffic over to V2.
Production cutover requires a separate authorization after the complete V2 gate.

## Safety boundary

- Use only the one frozen custom-format dump and its matching manifest.
- Restore the dump only into a factory database named `ta_v2_*` on the configured
  loopback PostgreSQL 17 cluster.
- Do not restore over an existing database and do not point any factory variable
  at a remote host. The factory rejects non-loopback URLs.
- The rehearsal output root must not exist. A prior report is evidence, not a
  workspace to reuse or delete.
- Never paste a database URL, password, memo, dump content, or restored row into
  a report or issue. Generated reports contain counts, identifiers, and hashes.
- The source connection is made transaction-read-only after restore. Both V2
  targets use the non-superuser runtime role.

## Prerequisites

1. Docker with Compose v2 is available locally.
2. The fixed dump and manifest are readable local files.
3. These local-only variables identify one disposable PostgreSQL 17 cluster:

   ```bash
   export TRACK_ANYWHERE_ALLOW_EXTERNAL_TEST_DATABASE=1
   export TRACK_ANYWHERE_TEST_POSTGRES_ADMIN_URL='postgresql+psycopg://ADMIN:REDACTED@127.0.0.1:15543/postgres'
   export TRACK_ANYWHERE_TEST_POSTGRES_MIGRATOR_BASE_URL='postgresql+psycopg://MIGRATOR:REDACTED@127.0.0.1:15543/postgres'
   export TRACK_ANYWHERE_TEST_POSTGRES_RUNTIME_BASE_URL='postgresql+psycopg://RUNTIME:REDACTED@127.0.0.1:15543/postgres'
   ```

Keep the real values in the local secret store or shell environment. Do not
record them in this repository or in the verification template.

The harness starts the Compose `postgres` service and runs `psql`, `pg_restore`,
and `pg_dump` only through the pinned `postgres:17-alpine` client service. A host
PostgreSQL installation is neither required nor used:

```bash
scripts/pg17-client.sh psql --version
scripts/pg17-client.sh pg_restore --version
scripts/pg17-client.sh pg_dump --version
```

All three commands must report major version 17.

## Fixed-source semantic mapping

This is a greenfield V2 import, not a V1 compatibility layer. The frozen source
is mapped without inventing domain facts:

- 38 categorized transaction lines become V2 category reporting lines; the 5
  uncategorized FX lines become typed historical reporting events (4 exchange,
  1 fee). No fake category or memo is introduced.
- All 6 investment activities become typed historical investment events. They
  create no V2 lot or valuation because V1 does not contain the two-asset facts
  required by that kernel.
- All 43 classification audits are retained as typed history. The 8
  reclassifications also replay real reporting revisions, with chain and current
  category/version/path parity checked before the first target write.
- A pure four-posting, two-asset FX transaction marks its source system accounts
  `fx_trading` and posts with V2 kind `fx`; the mixed six-posting FX-plus-fee
  shape remains `standard`.
- Transactions, classification events, and investment events share one
  deterministic per-Book schedule. It is ordered by effective time and raw
  source identity, with original-before-reversal and transaction-before-reclass
  dependencies; journal children stay adjacent to their parent aggregate.
- V1 deferred reporting fields are accepted only at their real defaults:
  null counterparty/project, `unknown` necessity, and `none` reimbursement.
  Any meaningful value blocks the run rather than being silently discarded.

## Run the rehearsal

Choose a new output name. Do not pre-create it.

```bash
set -euo pipefail
RUN_ROOT="output/v2-backfill-run-$(date -u +%Y%m%dT%H%M%SZ)-$$"
bash scripts/rehearse-v2-backfill.sh \
  --dump /absolute/path/to/frozen-v1.dump \
  --manifest /absolute/path/to/frozen-v1.manifest.txt \
  --output-root "$RUN_ROOT"
```

One shell process owns the entire lifecycle. In order it:

1. binds the dump SHA-256 to the manifest and checks all three client versions;
2. requires both runs to emit byte-identical full extraction manifests bound to
   the fixed dump and source revision; each independent verifier consumes its
   run's full manifest rather than the initial dump-only binding;
3. creates and restores one source, then makes its connections read-only;
4. creates two empty targets at the exact V2 Alembic head;
5. checks the frozen source contract before loading;
6. imports run A with UTC/C, batch 37, one worker, seed 0;
7. imports run B with Pacific/Auckland/en_US.UTF-8, batch 13, four workers, seed 731;
8. independently verifies each target and compares those verifier reports;
9. strictly drops all three databases and proves each is absent; and
10. atomically writes `summary.json`, then disarms the failure cleanup trap.

Any failed command, assertion, cleanup, or absence check exits nonzero. Failure
can retain diagnostics, but cannot produce a PASS summary. The cleanup trap
attempts each database independently so one failed drop does not mask the other
two.

## Accept the evidence

Only accept a run when the command exited zero and the summary satisfies:

```bash
uv run python - "$RUN_ROOT/summary.json" <<'PY'
import json
import sys

report = json.load(open(sys.argv[1], encoding="utf-8"))
assert report["status"] == "PASS"
assert report["quarantine_count"] == 0
assert report["source_counts"] == {
    "accounts": 121,
    "postings": 284,
    "transaction_lines": 43,
    "transactions": 135,
}
assert report["run_id"]
assert report["event_hash"]
assert report["book_terminal_hashes"]
assert report["projection_hashes"]
assert set(report["independent_report_hashes"]) == {"run_a", "run_b"}
PY
```

Also inspect `determinism.json`: it must be PASS with no differences, and both
`run-*/independent-verification.json` files must be PASS. Copy only secret-free
facts into `v2-backfill-verification-template.md`. Generated `output/` reports,
the dump, restored databases, and DSNs are not committed.

## Failure handling

- Keep the failed output directory intact for diagnosis. Use a new output name
  for the next attempt.
- A normal shell failure or interrupt runs best-effort cleanup. Never reinterpret
  a retained diagnostic directory as success.
- After an untrappable process kill or machine crash, inspect the local cluster
  as an administrator for databases beginning with `ta_v2_`. Confirm they belong
  to this rehearsal before dropping them. Do not broaden cleanup beyond that
  namespace.
- Re-run only from the beginning. Runs A and B are an indivisible gate; an
  individually successful target is not reusable evidence.

Stop after recording the local result. Do not push a production tag, deploy,
replace the stable backend, or change any production configuration.
