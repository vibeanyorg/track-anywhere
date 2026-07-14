# V2 final verification evidence

## Current status: NOT RUN

Task 35's isolated staging harness is implemented, but the exact-image Docker
staging run was **not executed** in this implementation task. No PASS report,
accepted-run pointer, image ID, runtime identity, terminal hash, replay hash, or
projection-lag result is claimed here.

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

The real frozen dump and manifest metadata, schema, and aggregate shapes were
inspected read-only locally during cross-review.
No full import ran; no full rehearsal ran. That inspection was not used to
produce a staging PASS or cutover claim. The deterministic two-target rehearsal
and zero-quarantine proof
remain a separate manual gate requiring the fixed authorized local input.
Synthetic isolated staging must not be reported as that rehearsal. No
production, stable-runtime, or cloud access occurred.

## Commands to collect future evidence

Follow `docs/operations/v2-isolated-staging-checklist.md`: build both images
from a clean committed `git archive`, run `scripts/staging-v2-smoke.sh` with a
new UUID and nonexistent run directory, independently validate
`verification.json`, then atomically update the source-specific pointer.

Copy only secret-free values from that validated report into this document:
source commit, run ID, PostgreSQL/Alembic versions, role names, image content
digests and revision labels, event terminal hashes, projection hashes, replay
status, lag, and exact commands. Keep the staging source commit distinct from
the later documentation-only evidence commit.

## Stop condition

Even after a future local PASS, stop at isolated staging. Do not access cloud or
stable environments, push a production tag, replace a service, change a
production DSN, or deploy without new explicit user authorization.
