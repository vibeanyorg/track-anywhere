# V2 final verification evidence

## Current status

- Fixed-backup local two-target backfill rehearsal: **PASS**
- Exact-image isolated staging: **NOT RUN**
- Production deploy/cutover: **NOT PERFORMED**

The completed fixed-backup rehearsal is recorded in
[`v2-backfill-verification-2026-07-15.md`](v2-backfill-verification-2026-07-15.md).
It proves deterministic local import of the authorized frozen dump, including
the reviewed credit-card semantics contract, two independent verifier passes,
zero quarantine, identical replay/projection hashes, and strict cleanup of all
temporary databases.

That PASS does **not** substitute for Task 35's exact-image Docker staging gate.
The staging harness is implemented, but the exact-image staging run has not
been executed. No staging PASS report, accepted-run pointer, image ID, runtime
identity, projection-lag result, or cutover approval is claimed here.

This is intentionally honest local evidence:

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
| Hash/head verification | **PENDING independent verifier** |
| Async projection lag | **PENDING; must converge to zero** |
| Independent replay/projection hashes | **PENDING** |
| Legacy route absence | **PENDING runtime HTTP probe** |
| Production deploy | **NOT PERFORMED — production untouched** |

The authorized frozen dump was restored only into disposable local PostgreSQL
17 databases. The deterministic two-target rehearsal completed with two
independent verifier PASS results, zero quarantine, and no determinism
differences. Its final PASS summary was written only after strict cleanup and
absence read-back for the restored source and both targets.

The rehearsal ran from a working tree rather than a clean, committed release
source. It is therefore implementation/backfill evidence, not an exact-image
staging or release attestation. No production, stable-runtime, or cloud access
occurred.

## Commands to collect future evidence

Follow `docs/operations/v2-isolated-staging-checklist.md`: build both images
from a clean committed `git archive`, run `scripts/staging-v2-smoke.sh` with a
new UUID and nonexistent run directory, independently validate
`verification.json`, then atomically update the source-specific pointer.

Copy only secret-free values from that future validated report into this document:
source commit, run ID, PostgreSQL/Alembic versions, role names, image content
digests and revision labels, event terminal hashes, projection hashes, replay
status, lag, and exact commands. Keep the staging source commit distinct from
the later documentation-only evidence commit.

## Stop condition

Even after a future local PASS, stop at isolated staging. Do not access cloud or
stable environments, push a production tag, replace a service, change a
production DSN, or deploy without new explicit user authorization.
