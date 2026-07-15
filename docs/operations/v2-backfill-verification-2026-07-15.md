# V2 fixed-backup backfill verification — 2026-07-15

This document records secret-free evidence from the completed local,
disposable PostgreSQL 17 rehearsal. It is not a staging PASS, release
attestation, production deployment, or cutover approval.

## Run identity

- Completed: `2026-07-15T01:49:50Z`
- Baseline HEAD: `a642359612a867de2bc279f768204598c2f2e125`
- Source identity: reviewed uncommitted worktree based on the baseline above;
  no release commit is claimed
- Rehearsal run ID: `5a90b05c-3399-415e-9e3e-9b9e9e0fedf7`
- Output directory basename:
  `v2-backfill-credit-card-final-pinned-20260715T0300Z`

## Frozen source and semantic-review contract

- Dump SHA-256:
  `a125b857a317e8c017d7028a26f78cf664ff6d57f6c0e698b3c229acf5d6cf9e`
- V1 schema revision: `0019_posting_constraints`
- Canonical snapshot/manifest hash:
  `f1cb565c646dc759b203efad3a5584492f3c824a16abb728bbce83150413597f`
- Credit-card review contract hash:
  `237f964a990018eb7fc91b9e45ba001ebb319456577f14263e3a444dc4d54430`
- Source read-only/frozen-dump contract: **PASS**
- Source counts: 121 accounts, 135 transactions, 284 postings, 43
  transaction lines

The fail-closed review contract explicitly covers every source transaction and
posting that touches a card account. It fixes each target account/side decision,
records five expected card natural balances, redirects the retired alias where
required, closes that alias at zero, and prescribes exact inverse events for
three reviewed legacy corrections. No memo/sign heuristic is used at import
time.

## Local database boundary

- PostgreSQL server image: `postgres:17-alpine`
- `psql`, `pg_restore`, and `pg_dump` client major version: 17
- Migrator role: `track_anywhere_migrator`
- Runtime role: `track_anywhere_runtime`
- Owner, migrator, and runtime identities are distinct and non-superuser:
  **PASS**

## Independent runs

Run A used `TZ=UTC`, `LC_ALL=C`, batch size 37, one worker, and shuffle seed 0.
Run B used `TZ=Pacific/Auckland`, `LC_ALL=en_US.UTF-8`, batch size 13, four
workers, and shuffle seed 731.

Both independent verifiers returned **PASS** with:

- 729 source receipts and zero quarantine records;
- 121 accounts, 138 journal transactions, 290 journal postings, 238 ledger
  events, 8 transaction reversals, and 38 reporting lines;
- the same credit-card review contract hash, single-book terminal hash, event
  evidence hash, and all projection hashes;
- no verifier issues.

The three-transaction and six-posting target delta is the expected result of
the three review-authorized exact inverse events. Historical card activity
remains generic immutable journal history by design, so the zero historical
`credit_card_transactions` count is expected rather than a missing import.

## Determinism evidence

- Determinism comparison: **PASS**
- Differences: `[]`
- Event evidence hash:
  `4a2601c361619b99fe2eff4dcb1fa9ba20370e923e242921f85f46f5ef142edb`
- Single-book terminal hash:
  `f43958c552b6e641295402a13a0dc53335d2e1468030e1c6838069dcc981b090`
- Run A independent report SHA-256:
  `1b970c73455223a4886314086dd56a5fb2c2b630d093bee1d527048ad88fdd70`
- Run B independent report SHA-256:
  `1b970c73455223a4886314086dd56a5fb2c2b630d093bee1d527048ad88fdd70`
- Projection hashes:
  - credit cards:
    `84c0193537ddeaafd60fbae3787a6f5ce8bd1d1851d0a8111e9f99a6a4669617`
  - events:
    `4a7f7af069acf15df8e8cb81766ce47e304b51334cced0cd136655d6ecb893f4`
  - investments:
    `0602e559f4b355c3ed45c9de2b3633831e099e55d6ad2d96082523804b0c2f24`
  - journal:
    `2bf942c12072193be46a4c75663dd0c07373edbe36d7b363319b9dfd942512db`
  - reporting:
    `9b11d2134e961ac92c4a7dec3d84c6351b554b3e42d0eeddeda03f44b47f2dbc`

## Cleanup and stop conditions

- Restored source absent after strict cleanup: **PASS**
- Target A absent after strict cleanup: **PASS**
- Target B absent after strict cleanup: **PASS**
- PASS summary installed atomically after cleanup: **PASS**
- Production/stable database or runtime accessed or changed: **NO**
- Cloud/staging environment accessed or changed: **NO**
- Production DSN/configuration changed: **NO**
- Separate production-cutover authorization received: **NO**
- Owner-sealed generic card-event admission and DB-level active-account guard:
  **NOT COMPLETE — production-cutover blocker**

## Verdict

The reviewed fixed-backup two-target rehearsal is **PASS**. The credit-card
historical semantic mapping is deterministic and independently verified for
this snapshot. Exact-image isolated staging and any production/cutover action
remain separate, unexecuted gates. The database trust-boundary hardening listed
above must also be complete before cutover.
