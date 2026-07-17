# Frozen V1 financial-history verification record

Copy this template for one candidate run. Store only secret-free evidence. Do
not paste DSNs, credentials, key material, source rows, names, memos,
descriptions, ciphertext, nonces, OAuth tokens, API keys, or archive plaintext.

## Fixed scope

| Field | Required value / recorded evidence |
| --- | --- |
| Target Book | `a682ddd2-f26a-5ad1-8f0a-f2fc1f75fa3d` |
| Source dump SHA-256 | `a125b857a317e8c017d7028a26f78cf664ff6d57f6c0e698b3c229acf5d6cf9e` |
| Source manifest SHA-256 | `f1cb565c646dc759b203efad3a5584492f3c824a16abb728bbce83150413597f` |
| Approved card-review SHA-256 | `237f964a990018eb7fc91b9e45ba001ebb319456577f14263e3a444dc4d54430` |
| Canonical plan SHA-256 | `c93ed8aed78918d71caab6e7178a2c4347d72b55a08dfeace1d95eeb81604ec8` |
| Expected terminal hash | `bcc2828422fda617df93fb2fc92e41599f0c694f9f1d502f1dcd22f4d85186fc` |
| Operator / UTC window | **PENDING** |

## Source and image provenance

- [ ] Git status was clean; committed SHA: **PENDING**
- [ ] Immutable image digest: **PENDING**
- [ ] Image revision label equals committed SHA.
- [ ] PostgreSQL image is pinned by digest and reports major version 17.
- [ ] Normal isolated V2 staging report/run ID: **PENDING**
- [ ] Keyring mount target and read-only mode verified without reading content.
- [ ] No raw protected-content key exists in environment or report output.

## Backup and recovery proof

- [ ] Final pre-import backup object/checksum/time: **PENDING**
- [ ] Backup was restored into an empty, isolated PostgreSQL 17 target.
- [ ] Role ownership, ACLs, Alembic head, readiness, authenticated read, and
      independent verifier all passed on the restored target.
- [ ] Restore proof/report ID: **PENDING**
- [ ] Fresh-database restore-and-switch recovery owner and command reviewed.

## Rehearsal proof

- [ ] `scripts/stream-v1-dump-to-postgres.py` received the dump on stdin and
      produced no regular dump file on the remote host.
- [ ] `scripts/rehearse-frozen-v1-history.sh` target A report/run ID: **PENDING**
- [ ] Independent target B report/run ID: **PENDING**
- [ ] A/B counts, deterministic identifiers, event hashes, terminal hash,
      balances, projections, authorized decrypt aggregate, archive commitment,
      and archive seal matched.
- [ ] Both exact receipt replays inserted zero rows.
- [ ] Quarantine and unresolved projection failures were zero.
- [ ] All rehearsal containers, networks, volumes, and temporary key files were
      removed before PASS.

## Production authorization

- [ ] Production authorization — owner approved the exact SHA, image digest,
      target Book, validated backup, evidence above, and maintenance window.

Approval identity / time / reference: **PENDING**

## Production execution

- [ ] Maintenance mode active and all ordinary writers blocked.
- [ ] Final pre-apply Book head / terminal hash / projection checkpoint:
      **PENDING secret-free values**
- [ ] Target preflight matched 64 accounts, 16 assets, zero ledger events, a
      position-zero/all-zero-hash Book head, no frozen-import receipt, and no
      protected import archive.
- [ ] Canonical plan went from planner stdout to runner stdin only.
- [ ] Atomic apply exit status and receipt: **PENDING**
- [ ] First apply inserted 57 accounts, four assets, 37 categories and category
      versions, 176 events, 138 journal transactions, 290 postings, 38
      reporting lines, eight reversals, 138 protected descriptions, one
      archive, and zero credit-card projection/quarantine rows.
- [ ] Exact second replay inserted zero rows and left the head unchanged.

## Post-apply verification

- [ ] Independent verification status/report: **PENDING**
- [ ] Terminal position is 176 and terminal hash equals the fixed value.
- [ ] Projection catch-up reached position 176 with zero unresolved failures.
- [ ] Cold replay into a fresh PostgreSQL 17 target reproduced the terminal and
      projection hashes.
- [ ] Authorized decrypt aggregate matched; no plaintext or per-row hash was
      retained.
- [ ] Archive metadata hashes, content commitment, seal, and protected export
      aggregate matched; plaintext export was removed.
- [ ] CLI/OAuth/MCP smoke passed with read-only operations.
- [ ] Fresh-connection readback matched the recorded result.
- [ ] Post-import backup was uploaded, validated, and fresh-restored.

Secret-free report paths / digests: **PENDING**

## Release or recovery decision

- [ ] PASS — traffic reopened only after every required check passed.
- [ ] RECOVER — traffic stayed blocked; a validated pre-import backup was
      restored to a fresh database and the Application switched to it.

Decision, approver, UTC time, and rollback-window end: **PENDING**

No event, hash, head, projection, or receipt was repaired or rewritten directly:
**PENDING confirmation**
