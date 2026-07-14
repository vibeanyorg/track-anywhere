# V2 backfill rehearsal verification

This document records secret-free evidence from one completed local rehearsal.
It is not production-cutover approval.

## Run identity

- UTC started/completed:
- Source commit:
- Harness commit:
- Rehearsal run ID:
- Output directory basename (do not include an absolute path):
- Operator/reviewer:

## Frozen source contract

- Dump SHA-256 (hash only):
- Manifest content/snapshot hash:
- V1 schema revision:
- Source transaction-read-only check: PASS / FAIL
- Source counts:
  - accounts:
  - transactions:
  - postings:
  - transaction_lines:
- Historical USDT 8-decimal identity contract: PASS / FAIL

Do not include the dump path, dump content, a database URL, credentials, or memo
text.

## Local PostgreSQL identities

- PostgreSQL server major version: 17
- `psql` client major version: 17
- `pg_restore` client major version: 17
- `pg_dump` client major version: 17
- Owner role name:
- Migrator role name:
- Runtime role name:
- Roles are distinct, non-superuser, and least-privileged: PASS / FAIL

## Independent run A

- Environment: `TZ=UTC`, `LC_ALL=C`, batch 37, workers 1, seed 0
- Independent verifier status:
- Receipt/event/projection counts:
- Quarantine count:
- Event evidence hash:
- Book terminal hashes:
- Projection hashes:
- Independent report SHA-256:

## Independent run B

- Environment: `TZ=Pacific/Auckland`, `LC_ALL=en_US.UTF-8`, batch 13, workers 4, seed 731
- Independent verifier status:
- Receipt/event/projection counts:
- Quarantine count:
- Event evidence hash:
- Book terminal hashes:
- Projection hashes:
- Independent report SHA-256:

## Determinism and cleanup gates

- Independent report comparison status:
- Differences (must be empty):
- Event IDs/order/payload parity: PASS / FAIL
- Book position/terminal hash parity: PASS / FAIL
- Projection hash parity: PASS / FAIL
- Quarantine is zero: PASS / FAIL
- Restored source absent after strict cleanup: PASS / FAIL
- Target A absent after strict cleanup: PASS / FAIL
- Target B absent after strict cleanup: PASS / FAIL
- PASS summary installed atomically after cleanup: PASS / FAIL

## Stop condition

- Production/stable database untouched: YES / NO
- Production/stable runtime untouched: YES / NO
- Production DSNs/configuration unchanged: YES / NO
- No dump, database, DSN, password, memo, or generated output committed: YES / NO
- Separate production-cutover authorization received: NO

## Verdict

- Overall: PASS / FAIL
- Blocking issue codes:
- Reviewer notes (secret-free):
