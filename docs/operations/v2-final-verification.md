# V2 final verification evidence

## Current status

- Source boundary: **V2 ONLY**
- Exact-image isolated staging: **NOT RUN**
- Production deploy/cutover: **NOT PERFORMED**

Current HEAD contains no V1 import path, compatibility runtime, or historical
data-conversion release gate. Git history remains the recovery record for the
removed implementation. Deterministic replay and the independent V2 ledger
verifier remain part of the current release surface.

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

## Stop condition

Even after a future local PASS, stop at isolated staging. Do not access cloud or
stable environments, push a production tag, replace a service, change a
production DSN, or deploy without new explicit user authorization.
