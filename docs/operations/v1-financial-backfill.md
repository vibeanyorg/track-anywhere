# Frozen V1 financial-history backfill

This runbook is the only approved path for the one-time frozen V1 financial
history import. It is not a V1 compatibility layer, API, CLI command, or
repeatable migration framework. It targets exactly one Book and one reviewed
source. Any changed hash, Book ID, plan, count, or image is a stop condition
that requires a new code review and rehearsal.

Completing rehearsal does not authorize production. Record evidence in
[the verification template](v1-financial-backfill-verification-template.md)
and obtain the separate **Production authorization** checkbox before touching
the production database.

## Fixed contract

| Input or result | Required value |
| --- | --- |
| Target Book | `a682ddd2-f26a-5ad1-8f0a-f2fc1f75fa3d` |
| Source dump SHA-256 | `a125b857a317e8c017d7028a26f78cf664ff6d57f6c0e698b3c229acf5d6cf9e` |
| Source manifest SHA-256 | `f1cb565c646dc759b203efad3a5584492f3c824a16abb728bbce83150413597f` |
| Approved card-review SHA-256 | `237f964a990018eb7fc91b9e45ba001ebb319456577f14263e3a444dc4d54430` |
| Canonical plan SHA-256 | `c93ed8aed78918d71caab6e7178a2c4347d72b55a08dfeace1d95eeb81604ec8` |
| Expected terminal event hash | `bcc2828422fda617df93fb2fc92e41599f0c694f9f1d502f1dcd22f4d85186fc` |

The canonical plan contains 176 events, 138 journal transactions, 290
postings, 121 accounts, 20 assets, 37 categories and category versions, 38
reporting assignments, eight reversals, 138 protected descriptions, one
protected archive, and zero quarantine records. Do not waive a mismatch.

Before apply, the target Book must match the reviewed preseeded catalog exactly:
64 accounts, 16 assets, zero ledger events, a Book head at position zero with
the all-zero terminal hash, no frozen-import receipt, and no protected import
archive. The first receipt must report 57 accounts, four assets, 37 categories
and category versions, 176 events, 138 journal transactions, 290 postings, 38
reporting lines, eight reversals, 138 protected descriptions, one archive, zero
credit-card projection rows, and zero quarantine rows inserted. The second
receipt must report zero inserted rows everywhere.

## Runtime and secret boundary

Production remains one public FastAPI service plus PostgreSQL 17. The
`frozen-v1-backfill` Compose profile is a private one-shot process: it uses the
same immutable API image, the same non-owner runtime database role, no host
port, and no restart policy. It is not a second public service.

Both API and runner read the protected-content keyring from the fixed container
path `/run/secrets/track-anywhere-protected-content-keyring.json`. Compose bind
mounts `/etc/track-anywhere/protected-content-keyring.json` there read-only.
The host file must be a regular, non-symlink file readable only by the numeric
runtime UID (`0400` or `0600`) and must be backed up separately. Put only the
file path in `TRACK_ANYWHERE_PROTECTED_CONTENT_KEYRING_FILE`; a raw or
base64-encoded master key must never enter an environment variable, Compose
file, shell history, log, report, or repository.

Never print or copy the keyring into evidence. Validate only its owner, mode,
mount target, read-only flag, and a separately controlled recovery procedure.

## Pre-production stop gates

### 1. Prove a clean SHA and immutable image

- [ ] Work from a clean committed SHA: `git status --porcelain` is empty and
      `git rev-parse HEAD` is recorded.
- [ ] Build from `git archive <SHA>` in CI or on the approved remote builder.
- [ ] Record the immutable image digest and confirm its revision label equals
      the clean committed SHA. Do not use `latest`, a mutable tag, or a locally
      dirty filesystem.
- [ ] Run the normal isolated V2 staging checklist with that exact digest.

Inspect labels and digests without printing environment variables:

```bash
test -z "$(git status --porcelain)"
git rev-parse HEAD
docker image inspect --format '{{json .RepoDigests}} {{json .Config.Labels}}' "$IMAGE"
```

### 2. Prove the frozen source without persisting it remotely

Verify the local dump before any streaming operation:

```bash
test "$(shasum -a 256 "$FIXED_DUMP" | awk '{print $1}')" = \
  a125b857a317e8c017d7028a26f78cf664ff6d57f6c0e698b3c229acf5d6cf9e
```

Use `scripts/stream-v1-dump-to-postgres.py` to hash the stream while feeding
`pg_restore` into an isolated PostgreSQL 17 source. The helper must receive the
dump on stdin and must not create a remote dump file. The restored source role
is read-only and is never the production database.

### 3. Prove backup and isolated restore

- [ ] Run `scripts/backup-postgres-s3.sh` and record the verified object key,
      checksum, PostgreSQL version, and completion time.
- [ ] Bootstrap a fresh disposable PostgreSQL 17 database with the versioned
      owner/migrator/runtime roles.
- [ ] Run `scripts/restore-postgres-s3.sh` against that empty target with
      `TRACK_ANYWHERE_RESTORE_ISOLATED_TARGET=1`.
- [ ] Record an isolated PostgreSQL 17 restore PASS: archive validation,
      ownership/ACL validation, Alembic check, `/api/v2/ready`, authenticated
      read, and independent ledger verification.

An upload without this fresh-target proof is not a usable backup. Never test a
restore by overwriting the live database.

### 4. Complete the two-target rehearsal

Run `scripts/rehearse-frozen-v1-history.sh` with a unique run ID and the fixed
dump on stdin. A single harness run must use the exact candidate image and
pinned PostgreSQL 17 digest, isolated internal networks, no published ports,
and two independent targets A and B:

```bash
TRACK_ANYWHERE_CANDIDATE_IMAGE="$IMAGE" \
TRACK_ANYWHERE_POSTGRES_IMAGE="$PINNED_PG17_IMAGE" \
bash scripts/rehearse-frozen-v1-history.sh \
  --source-commit "$SHA" \
  --run-id "$REHEARSAL_RUN_ID" \
  --book-id a682ddd2-f26a-5ad1-8f0a-f2fc1f75fa3d \
  --report-dir "output/v1-backfill-$SHA-$REHEARSAL_RUN_ID" \
  --dump-stdin < "$FIXED_DUMP"
```

Require:

- identical canonical plan, IDs, event payloads/order, terminal hash,
  balances, projections, authorized decrypted-description aggregate, archive
  commitment, and archive seal on targets A and B;
- zero quarantine and zero unresolved projection failures;
- an exact second receipt replay with zero inserted rows and no changed head;
- secret-free allowlisted reports and complete resource cleanup.

Do not continue if either rehearsal or its independent verification fails.

## Production authorization

This gate is deliberately separate from technical rehearsal and deployment
approval. It must name the exact SHA, image digest, Book, backup object, and
maintenance window.

- [ ] Production authorization — the owner explicitly approved this exact
      one-time backfill after reviewing the completed verification template.

Without that checked approval, stop here. Do not start maintenance mode, open a
production connection, or run the one-shot profile.

## Production execution

### 1. Enter maintenance mode and block writes

1. Put the public origin in maintenance mode and stop the FastAPI Application.
2. Confirm there are no other API replicas, CLI writers, MCP writers,
   schedulers, migration jobs, projection jobs, or active non-operator write
   sessions. PostgreSQL remains private.
3. Take and validate a final pre-import backup after the write block is active.
4. Record the Book head, terminal hash, projection checkpoint, and runtime role
   from a fresh connection.
5. Prove the fixed target catalog and empty-history/receipt/archive preconditions
   above before opening the runner transaction.

Keep maintenance mode active until every verification and smoke step below has
passed. The one-shot runner is the sole authorized writer during this window.

### 2. Compile and apply through one stdin pipeline

The plan is secret-bearing operational material. It must travel via stdin only:
never write it to a regular file, object store, command argument, environment
variable, report, or shell trace. Run from the exact clean checkout with
`pipefail` enabled. The planner reads only the isolated restored source; its
stdout goes directly to the fixed Compose runner:

```bash
set -o pipefail
uv run python -m backend.tools.frozen_v1_history |
  TRACK_ANYWHERE_IMAGE="$IMAGE" docker compose \
    --env-file deploy/env/prod.env \
    -f compose.prod.yaml \
    --profile frozen-v1-backfill \
    run --rm -T frozen-v1-backfill
```

That command is for a Compose-managed target. On a Dokploy host, keep the same
stdin pipeline but run the immutable image as a disposable container on
`dokploy-network`; pass the non-owner runtime DSN from a mode-`0600` temporary
env file and mount the same fixed keyring read-only. Use the exact module,
target Book, and plan SHA from `compose.prod.yaml`, publish no port, and remove
the temporary runtime env file after the receipt is captured. Do not start a
second Dokploy Application.

Supply `TRACK_ANYWHERE_FROZEN_SOURCE_URL`,
`TRACK_ANYWHERE_FROZEN_MANIFEST_PATH`, and
`TRACK_ANYWHERE_CREDIT_CARD_REVIEW_PATH` through the planner's restricted
operator environment. Do not pass the production DSN to the planner. Inspect
both pipeline exit codes and the runner's allowlisted JSON receipt. The runner
independently checks the fixed Book and plan SHA before it opens the database.

The importer applies catalog rows, journal events, synchronous projections,
protected descriptions, archive metadata, and its idempotency receipt in one
atomic database transaction. Any exception must roll back the entire attempt.
Do not manually resume at an intermediate event or table.

Run the identical stdin pipeline once more after the first success. The second
receipt must report replay with zero inserted rows and an unchanged Book head
and terminal hash.

### 3. Verify before reopening traffic

Keep all ordinary writes blocked and attach evidence for each independent
check:

1. **Independent verification:** compare database read-back against the
   independently reduced frozen source, including counts, deterministic IDs,
   event order and payload hashes, terminal hash, balances, reversals,
   reporting lines, and zero quarantine.
2. **Projection catch-up:** wait for the asynchronous checkpoint to equal Book
   position 176; require zero unresolved projection failures and stable
   projection hashes across fresh connections.
3. **Cold replay:** replay immutable events into a fresh PostgreSQL 17 database
   through the supported committer/projector boundary. Require the same
   terminal hash and projection digests. The target must be empty before replay.
4. **Authorized decrypt:** using an owner-authorized, short-lived session and
   the mounted keyring, decrypt descriptions only in memory and compare the
   aggregate SHA. Do not persist descriptions or per-description hashes.
5. **Archive seal and export:** use `ta archive list <BOOK_ID>` to verify the
   fixed source/manifest/review/plan hashes and seal, then use
   `ta archive export <BOOK_ID> <ARCHIVE_ID>` as the Book owner. Hash and verify
   the canonical NDJSON in protected temporary storage, then securely remove
   the plaintext export; evidence contains only the aggregate commitment and
   seal.
6. **CLI/OAuth/MCP smoke:** start the exact API image, require health and
   readiness, perform owner CLI reads, OAuth discovery/login/refresh, and an
   OAuth-only MCP initialize/tools/list-books read. Use no ledger write in the
   smoke and confirm the fixed Book is readable.

Complete every field in the verification template. Restart public traffic only
after fresh-connection reads match, the post-import backup is validated, and
the named approver accepts the result.

## Failure and recovery

If the apply command fails before commit, confirm the Book head is unchanged
and rerun only the identical, hash-pinned command. If commit outcome is unknown,
query the idempotency receipt and Book head; the exact replay is the only safe
retry.

If any post-commit verification fails, keep maintenance mode active and preserve
the failed database for investigation. Restore the last validated pre-import
archive into a **fresh PostgreSQL 17 database**, bootstrap roles, run migrations
and all restore verification, then switch the Application DSN to that validated
replacement. Do not restore over the failed database and do not point traffic
at a partially checked target.

Never repair, rewrite, or delete ledger events, hashes, heads, projections, or
receipts to make verification pass. Recovery is restore-and-switch to a fresh
database, or a newly reviewed compensating ledger command after the service is
safe; direct SQL repair is forbidden.
