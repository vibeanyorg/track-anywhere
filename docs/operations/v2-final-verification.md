# V2 final verification evidence

## Current status

- Runtime source boundary: **V2 ONLY; private frozen-import exception only**
- Exact-image isolated staging: **NOT RUN**
- Frozen V1 financial-history rehearsal/import: **NOT RUN**
- Production deploy/cutover: **NOT PERFORMED**

Current HEAD contains no V1 route, compatibility runtime, or general migration
framework. It does contain one hash-pinned, private, offline importer for the
single approved frozen financial history. That one-shot path is not reachable
from HTTP, MCP, or CLI and has not been run against production. Follow the
[frozen V1 backfill runbook](v1-financial-backfill.md) and its verification
template; deterministic replay and independent V2 ledger verification remain
mandatory release gates.

The exact-image staging harness is implemented but has not been executed for
this source revision. No staging PASS report, accepted-run pointer, image ID,
runtime identity, projection-lag result, or cutover approval is claimed here.

| Field | Value |
| --- | --- |
| Staging source commit | **PENDING exact committed source** |
| Run ID | **PENDING caller-supplied UUID** |
| PostgreSQL version | **PENDING isolated run; must be 17** |
| Alembic head | **PENDING database/image comparison** |
| Runtime identity | **PENDING; must be non-owner runtime role** |
| Migrator identity | **PENDING; must differ from runtime and owner** |
| API/web image content digests | **PENDING exact-image inspection** |
| Image revision labels | **PENDING; must equal source commit** |
| Fresh-connection visibility | **PENDING** |
| Hash/head verification | **PENDING independent V2 verifier** |
| Async projection lag | **PENDING; must converge to zero** |
| Replay/projection hashes | **PENDING** |
| Legacy route absence | **PENDING runtime HTTP probe** |
| Frozen source/review/plan hashes | **PENDING exact rehearsal** |
| Frozen import A/B parity and cold replay | **PENDING exact rehearsal** |
| Production authorization | **NOT GRANTED** |
| Production deploy | **NOT PERFORMED — production untouched** |

## Commands to collect future evidence

Follow `docs/operations/v2-isolated-staging-checklist.md`: build both images
from a clean committed `git archive`, run `scripts/staging-v2-smoke.sh` with a
new UUID and nonexistent run directory, independently validate
`verification.json`, then atomically update the source-specific pointer.

Copy only secret-free values from that future validated report into this
document: source commit, run ID, PostgreSQL/Alembic versions, role names, image
content digests and revision labels, event terminal hashes, projection hashes,
replay status, lag, and exact commands. Keep the staging source commit distinct
from the later documentation-only evidence commit.

For the historical data gate, also complete
`docs/operations/v1-financial-backfill-verification-template.md`. It requires
the fixed source/review hashes and target Book, clean SHA and immutable image
proof, backup/fresh-restore proof, two-target rehearsal, idempotent replay,
projection catch-up, authorized protected-content aggregate, archive seal,
CLI/OAuth/MCP smoke, and a separately checked Production authorization.

## Stop condition

Even after a future local PASS, stop at isolated staging. Do not access cloud or
stable environments, push a production tag, replace a service, change a
production DSN, or deploy without new explicit user authorization.
Passing rehearsal does not grant Production authorization for the one-shot
backfill; that approval is a separate checkbox tied to the exact evidence.
