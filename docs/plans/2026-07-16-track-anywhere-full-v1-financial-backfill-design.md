# Track Anywhere Full V1 Financial Backfill Design

**Status:** Approved on 2026-07-16

**Target Book:** `a682ddd2-f26a-5ad1-8f0a-f2fc1f75fa3d`

## Context

The target V2 Book already contains a catalog-only bootstrap of 64 user financial
accounts and 16 dependency assets. It has no journal transactions and its Book
position is zero. The next migration must restore the complete financial history
from the one approved V1 snapshot without reintroducing a V1 runtime or a public
bulk-import API.

The only approved source artifacts are:

- dump SHA-256:
  `a125b857a317e8c017d7028a26f78cf664ff6d57f6c0e698b3c229acf5d6cf9e`;
- canonical manifest/snapshot hash:
  `f1cb565c646dc759b203efad3a5584492f3c824a16abb728bbce83150413597f`;
- reviewed credit-card semantics hash:
  `237f964a990018eb7fc91b9e45ba001ebb319456577f14263e3a444dc4d54430`;
- source Alembic revision: `0019_posting_constraints`.

The fixed source inventory is:

| Entity | Count |
| --- | ---: |
| Assets | 20 |
| Accounts | 121 |
| Categories | 37 |
| Category versions | 37 |
| Transactions | 135 |
| Postings | 284 |
| Transaction lines | 43 |
| Classification audit events | 43 |
| Investment activities | 6 |
| Investment valuations | 0 |
| Attachments | 0 |

The previous two-target PostgreSQL 17 rehearsal proved the reviewed financial
mapping, including three deterministic inverse corrections for legacy card
direction errors. Its target baseline was 138 journal transactions, 290
postings, 8 reversals, 38 current reporting lines, and zero quarantine rows.
Because this migration uses a different target Book ID and the current V2 event
contract, its terminal hash must be regenerated and independently verified.

## Goals

- Restore all catalog facts required by historical postings.
- Restore the reviewed, exact financial journal and current classifications.
- Preserve historical 8-decimal USDT values without rounding.
- Preserve all transaction purposes and memos as encrypted, owner-readable
  description sidecars.
- Preserve V1-only classification audit, incomplete investment activity, and
  other unsupported metadata in a hash-sealed encrypted archive.
- Keep online credit-card behavior typed and strict while allowing only this
  reviewed frozen history to use generic journal events.
- Make the production mutation atomic, deterministic, replay-safe, and
  recoverable from a verified pre-import backup.

## Non-goals

- No V1 compatibility layer, `/api/v1` route, startup import, or Alembic data
  migration.
- No public bulk-import API or MCP import tool.
- No invented investment lots, cost bases, FX rates, refund relations,
  merchants, counterparties, or categories.
- No conversion of ambiguous historical card activity into typed charge,
  payment, refund, or fee events.
- No direct writes to the event store, Book heads, command receipts, journal
  projections, balances, reporting lines, lot projections, async projections,
  or outbox tables.
- No production import merely because a build or rehearsal passed; production
  apply remains a separate final authorization gate.

## Options Considered

### 1. Atomic offline application import into the existing Book — selected

A one-shot command in the same immutable production image uses the runtime
database role, one Book lock, one PostgreSQL transaction, the current
application layer, `LedgerCommitter`, and synchronous projectors. The target
Book and existing catalog remain stable. Any failure rolls back the entire
mutation.

### 2. Resumable command-by-command import

This reuses ordinary application commands but can leave a valid partial event
prefix after interruption. It also cannot transport historical USDT precision
or generic reviewed card history through the current public command contracts.

### 3. Build a fresh database and switch production to it

This offers strong isolation but requires moving current users, OAuth clients,
sessions, memberships, setup state, and deployment configuration. That cutover
risk is unnecessary while the existing Book has no financial events.

REST/CLI-only replay is rejected because online money parsing enforces
`input_scale=6` for USDT and the generic online journal correctly rejects
credit-card accounts.

## Architecture

The import has three separated surfaces:

1. **Frozen-source planner** restores the approved dump into an isolated,
   network-disabled PostgreSQL 17 source, verifies the source contract, and
   produces a canonical sanitized plan.
2. **Offline application importer** consumes only an approved plan and performs
   the target mutation inside one transaction. It is callable from a one-shot
   process but is not registered with FastAPI, CLI networking, MCP, Alembic, or
   application startup.
3. **Independent verifier** computes expected facts from the frozen source and
   read-back facts from V2 without calling loader normalization code.

The importer executes one `ImportFrozenFinancialHistory` application command.
Its request hash and receipt bind the source dump hash, full manifest hash,
credit-card review hash, target Book ID, canonical plan hash, operation count,
and expected terminal event hash. The handler acquires the standard Book lock,
creates or exactly verifies catalog and privacy records through repositories,
appends the ordered event batch through `LedgerCommitter`, runs synchronous
projectors, closes the reviewed retired alias, seals the archive, completes the
receipt, and commits.

All public application writes are disabled for the maintenance window. The
one-shot process runs from the same immutable commit and image that passed the
rehearsal, using the non-owner runtime role and the normal production database
network.

## Deterministic Data Mapping

The fixed UUIDv5 namespace remains
`3f021172-6aa9-5b36-9208-f238bc35c596`. Existing account IDs continue to use
the frozen protocol based on the source Book ID and legacy account ID; the
target Book ID is never mixed into those account IDs. Transaction, posting,
command, description sidecar, reporting-line, and archive IDs use explicit
kind-specific UUIDv5 protocols bound to the canonical snapshot and source
identity.

The plan is ordered by a stable dependency-aware topology:

1. assets;
2. all accounts required by postings;
3. parent categories, child categories, and immutable versions;
4. original journal transactions ordered by effective time and raw source ID;
5. source reversals and three reviewed exact inverse corrections after their
   originals;
6. final current reporting assignments after their journal transactions;
7. retired alias closure and archive seal.

Catalog application is create-or-exact-verify. The existing 64 accounts and 16
assets must match every immutable accounting field. The importer adds only the
remaining 57 accounts, 4 dependency assets, and 37 categories and versions.
Any unexpected target row or metadata drift blocks the transaction before event
append.

Legacy signed amounts are normalized into positive integer accounting units and
an explicit debit/credit side. Every transaction must contain at least two
postings and balance independently per asset. Historical parsing uses each
asset's `ledger_scale`; it never passes through online decimal parsing or rounds
to `input_scale`. USDT remains `ledger_scale=8`, `input_scale=6`, and
`display_scale=6`.

The reviewed 22 card-touching source transactions and 23 card postings remain
generic immutable historical journal facts. The imported journal may use a
private frozen-history admission path bound to the approved hashes; the online
generic journal continues rejecting card accounts. The three review-authorized
inverse corrections are deterministic new transactions. Historical typed
`credit_card_transactions` therefore remains zero by design, while all future
card activity continues through typed commands.

Each imported source transaction receives a typed V2 external reference to its
opaque V1 source identity. Final reporting assignments use current V2 category
versions. The 43 category audit records are not emitted as V1 event types.

## Encrypted Descriptions

Each of the 135 source transactions gets one deterministic description sidecar
containing canonical JSON with its purpose, transaction memo, and ordered line
memos. The three generated card corrections receive deterministic,
non-sensitive reviewed-correction descriptions. The event contains only the
sidecar UUID in `description_ref`.

Sidecars use AES-256-GCM with a random 96-bit nonce. A versioned master key is
provided to the one-shot process and application through a mounted Dokploy
secret file. A Book-specific encryption key is derived with HKDF. AAD binds the
Book ID, sidecar ID, kind, key version, and plaintext content hash. The database
stores only ciphertext, nonce, `key_ref`, algorithm, content hash, lifecycle
status, and timestamps.

Random ciphertext is intentionally excluded from deterministic comparison.
Rehearsals compare deterministic sidecar IDs, key references, plaintext hashes,
and successfully decrypted canonical content. Replay finds the exact existing
content hash and does not re-encrypt it.

Authenticated REST and CLI reads may return decrypted descriptions only for a
Book-scoped actor with `ledger:read` and an explicit include-description flag.
Ordinary list and MCP responses omit plaintext by default. Plaintext never
enters events, command receipts, audit logs, metrics, exceptions, or importer
reports. Crypto-erased sidecars cannot be recovered or recreated in place.

## Hash-sealed Import Archive

V1-only financial context that cannot be mapped without inventing facts is
stored as canonical NDJSON inside a generic encrypted `import_archive` sidecar.
It includes:

- 43 classification audit records;
- 6 incomplete investment activities and zero valuations;
- 5 uncategorized FX reporting facts;
- source account institution metadata and opaque counterparty references;
- the mapping manifest, source counts, row hashes, and omission reasons.

The archive seal binds the source, manifest, card-review, plan, and NDJSON
content hashes. It does not participate in event replay or balances and does not
register legacy event types. Owner-authorized metadata inspection and explicit
archive export are supported; MCP does not expose archive plaintext by default.

## Production Execution

Before any production mutation:

1. Build the branch from a clean checkout on the designated DigitalOcean host.
2. Run the full PostgreSQL 17, Docker, API/CLI, replay, and security gates there.
3. Run two disposable full rehearsals with the exact target Book ID and a target
   pre-seeded with the existing 64-account/16-asset catalog.
4. Vary timezone, locale, extraction scheduling, and batch boundaries. Require
   identical canonical plans, IDs, event order, payloads, balances, projection
   hashes, and terminal hash.
5. Generate the canonical plan independently on local and DigitalOcean
   environments and require the same plan hash.

Local development uses a Python virtual environment, unit tests, formatting,
and static checks only. Local Docker image builds are intentionally excluded.
The branch is pushed to the remote; the immutable image is built and verified
on DigitalOcean.

For production apply:

1. Verify the live commit/image, health, readiness, schema, Book, membership,
   catalog, zero position, and zero transactions.
2. Create a fresh server-side production backup and restore it into an isolated
   PostgreSQL 17 target. Do not proceed without restore proof.
3. Put the public service into maintenance mode and block all writes.
4. Stream the sanitized canonical plan over SSH stdin to the one-shot importer;
   do not persist plaintext plan files on the production server.
5. Re-run all target preconditions inside the import transaction.
6. Commit the single application command.
7. Keep maintenance mode enabled while the independent verifier, cold replay,
   projection parity, description decrypt, archive export, and async checkpoint
   catch-up gates run.
8. Restore public service only after every gate passes, then recheck health,
   readiness, login, CLI, OAuth, MCP, authorized transaction reads, and Book
   balances.

## Failure and Recovery Semantics

- A source, review, plan, target, balance, or catalog mismatch blocks before the
  first target write.
- Any catalog, encryption, append, projection, closure, archive, or receipt
  failure before commit rolls back the entire transaction.
- A lost response after commit is recovered by replaying the same command and
  plan. The completed receipt returns the original result without new events.
- A different plan with the same import identity is an idempotency conflict.
- Nonzero quarantine blocks plan sealing and production apply.
- If any independent post-commit gate fails before reopening traffic, operators
  do not append repair events. They restore the verified pre-import backup into
  a fresh PostgreSQL 17 database and switch the application back to that target.
- The plaintext canonical plan exists only in the planner's memory and the SSH
  stream. Reports contain counts, IDs, and hashes, never private source text or
  credentials.

## Verification and Acceptance

The implementation follows test-first development and must cover source
contracts, deterministic IDs and ordering, exact units, reviewed card mapping,
atomicity, unknown commit outcomes, encryption tamper detection, authorization,
redaction, cold replay, and independent semantic parity.

Expected target projections are:

| Entity | Expected |
| --- | ---: |
| Assets | 20 |
| Accounts | 121 |
| Journal transactions | 138 |
| Journal postings | 290 |
| Reversals | 8 |
| Current reporting lines | 38 |
| Historical typed credit-card rows | 0 |
| Transaction description sidecars | 138 |
| Encrypted import archives | 1 |
| Quarantine rows | 0 |

Acceptance also requires:

- exact per-account source-reducer versus target balance parity;
- exact reviewed natural balances for all five card accounts;
- the retired card alias closed at zero;
- every historical 8-decimal USDT posting preserved exactly;
- all 138 transaction descriptions decrypting to their expected content hash;
- archive decrypt/export matching its row counts and content seal;
- Book hash chain, stream heads, synchronous applied markers, command receipt,
  and projection hashes passing independent verification;
- async projection checkpoints reaching the Book head with zero unresolved
  failures;
- a second identical import creating nothing and leaving Book position and
  terminal hash unchanged;
- `scripts/verify-v2.sh`, PostgreSQL 17 integration/concurrency/replay gates,
  CLI/API contracts, OpenAPI snapshot, frontend tests, and production canaries
  all passing from the immutable DigitalOcean-built image.

Production data import is complete only after fresh production read-back proves
these facts. A successful build, deployment, or importer exit code alone is not
completion evidence.
