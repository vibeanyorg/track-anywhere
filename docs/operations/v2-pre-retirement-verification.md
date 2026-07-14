# V2 pre-retirement verification

## Gate decision

**NOT APPROVED.** The Task 32 gate definition and static contract are present,
but the shared worktree contains concurrent Task 31–34 changes. The aggregate
gate, Docker E2E, and fixed-dump rehearsal must be run from one committed,
stable tree before any V1 deletion. This record deliberately does not turn
partial or synthetic evidence into a release approval.

This is a local-only gate. It must not deploy, contact Railway or another cloud
control plane, mutate the stable backend, or read a production database.

## Evidence record

| Field | Recorded value |
| --- | --- |
| Commit | Baseline inspected: `4c986d86b41b7846ff2be69db80e9fe8b28b8f85`; final gate commit is **PENDING** because the worktree is dirty. |
| PostgreSQL version | **PENDING** aggregate run; `test_postgres_runtime.py` requires exactly major 17. |
| Runtime identity | **PENDING** aggregate run; record `session_user` and `current_user` from the derived runtime-role database without credentials. |
| Migrator identity | **PENDING** migration check; record `session_user` and `current_user` from the factory-emitted migrator URL without credentials. |
| Alembic head | Code head observed before the aggregate run: `v2_0008_backfill_control`; re-read and record the committed final head. |
| Terminal hashes | **PENDING** synthetic rehearsal and separate authorized fixed-dump rehearsal. Record per-Book hashes only, never source financial rows. |
| Projection hashes | **PENDING** online versus cold replay and shadow-generation comparison. |
| Quarantine count | **PENDING** fixed-dump rehearsal; approval requires `0`. |

## Required command evidence

Run these commands from the repository root with the three loopback PG17 base
URLs set to distinct admin, migrator, and runtime identities:

```bash
bash scripts/verify-v2.sh
bash scripts/e2e-docker-postgres.sh
```

Record for each command: UTC start/end time, exit status, commit, PostgreSQL
version, database identities, and retained log artifact. The aggregate script
already excludes the local-only marker with `-m 'not frozen_dump'`.

The fixed production dump is a separate manual release gate. In this Task 32
implementation run, the frozen dump was not executed, opened, copied, restored,
or queried. Its two-import independent-verifier block may be run only when the
user separately authorizes that local data operation; then record both terminal
hash sets, both projection hash sets, and quarantine count here.

## Approval checklist

- [ ] Worktree is clean and the recorded commit exactly identifies all tested code.
- [ ] `bash scripts/verify-v2.sh` passes every unit, PG17, concurrency, replay,
  synthetic-backfill, contract/CLI, frontend, migration, and role-separation lane.
- [ ] `bash scripts/e2e-docker-postgres.sh` passes against isolated PostgreSQL 17.
- [ ] Runtime and migrator identities are distinct and runtime is not an owner.
- [ ] Alembic has exactly one V2 head and `alembic check` is clean.
- [ ] Online, replayed, and rebuilt projection hashes agree.
- [ ] The separately authorized two-import fixed-dump rehearsal is deterministic.
- [ ] Quarantine count is zero.
- [ ] Gate decision above is changed to `APPROVED` with real evidence.

Until every item is checked, `docs/operations/v2-retirement-manifest.md` is an
inventory only and Task 33 deletion is blocked.
