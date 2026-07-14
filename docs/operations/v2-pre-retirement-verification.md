# V2 local verification evidence

## Gate decision

**LOCAL IMPLEMENTATION COMPLETE; RELEASE NOT APPROVED.** The greenfield V2
runtime, V1 source deletion, deterministic backfill implementation, aggregate
local gate, and disposable Docker E2E are complete. The real fixed-dump
two-target rehearsal and Task 35 exact-image isolated-staging run were not
executed, so this record does not authorize a production cutover or deployment.

Task 33 deletion was performed under the user's explicit greenfield direction
that V1 is no longer used; it was not a claim that every release gate had run.
No Railway, cloud, production database, stable backend, or production service
was accessed or mutated while collecting this evidence.

## Evidence record

| Field | Recorded value |
| --- | --- |
| Commit | `3d9e41e28f5f4a23b83477251e0e9b7c5e630fda`; worktree clean and equal to `origin/codex/v2-event-ledger` for both final commands. |
| Aggregate local gate | `bash scripts/verify-v2.sh` exited `0` at the recorded commit, completed by `2026-07-14T15:54:39Z`. It passed unit, PG17, concurrency, replay, synthetic backfill, contracts/CLI, frontend lint/build, migration, and role-separation lanes. |
| PostgreSQL version | Disposable Docker E2E used `postgres:17-alpine`; local image readback reported PostgreSQL `17.10`. The aggregate lane also enforces major version 17. |
| Runtime identity | Docker E2E proved `session_user = current_user = track_anywhere_runtime`, database `track_anywhere`, and a fresh runtime connection observed committed balances. |
| Migrator identity | One-shot migration ran as `track_anywhere_migrator`; the runtime role was distinct from migrator and non-login owner `track_anywhere_owner`. |
| Alembic head | Exactly `v2_0008_backfill_control`; upgrade from empty succeeded and `alembic check` reported `No new upgrade operations detected.` |
| Terminal hashes | Synthetic event-chain, mutation, replay, and deterministic backfill hash gates passed. Real fixed-dump terminal hashes are intentionally **PENDING**. |
| Projection hashes | Online/cold replay and synthetic backfill projection parity gates passed. Real fixed-dump A/B projection hashes are intentionally **PENDING**. |
| Quarantine count | Synthetic backfill seal and quarantine gates passed with zero accepted loss. Real fixed-dump quarantine count is intentionally **PENDING**. |
| Docker E2E | `bash scripts/e2e-docker-postgres.sh` exited `0` at the recorded commit using local Docker context `desktop-linux`, loopback-only ports, a fresh volume, dedicated roles, V2 API/CLI journal/classification/reversal flow, V1 route `404`, and strict cleanup. |
| Frontend runtime note | The aggregate host had Node `24.0.1` while the project declares Node `22.x`; npm emitted `EBADENGINE`, but TypeScript and the production Next.js build passed. The committed Docker web stages use Node 22. |

## Commands executed

The final evidence was collected from the repository root with distinct local
admin, migrator, and runtime PG17 factory URLs:

```bash
bash scripts/verify-v2.sh
bash scripts/e2e-docker-postgres.sh
```

The first Docker attempt exposed and led to fixes for PostgreSQL readiness and
unlocked image dependencies. Only the final clean-commit runs above count as
PASS evidence. Failure stacks and their disposable volumes were cleaned before
the final run.

The frozen dump was not executed, imported, copied, or restored in this local
implementation pass. Earlier read-only metadata/schema/aggregate-shape
inspection is not a rehearsal and is not counted as PASS evidence.

## Gate checklist

- [x] Worktree was clean and the recorded commit exactly identified tested code.
- [x] `bash scripts/verify-v2.sh` passed every committed aggregate lane.
- [x] `bash scripts/e2e-docker-postgres.sh` passed on isolated PostgreSQL 17.
- [x] Runtime, migrator, and owner identities were distinct; runtime was non-owner.
- [x] Alembic had exactly one V2 head and `alembic check` was clean.
- [x] Synthetic online, replayed, and rebuilt projections agreed.
- [x] Reachable V1 runtime and SQLite persistence were removed.
- [ ] Run the fixed dump twice into fresh targets and compare independent reports.
- [ ] Prove fixed-dump quarantine count is zero and record terminal/projection hashes.
- [ ] Run Task 35 exact-image isolated staging and accept its independent report.
- [ ] Obtain new explicit approval before any production or stable cutover.

The unchecked items are release/cutover gates, not unfinished local
implementation. Until they pass, the correct external status remains **release
not approved**.
