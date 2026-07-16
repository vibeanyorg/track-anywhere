# Track Anywhere Full V1 Financial Backfill Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build, validate, and push a private one-shot importer that atomically restores the approved V1 financial history into Book `a682ddd2-f26a-5ad1-8f0a-f2fc1f75fa3d`, while preserving descriptions and unsupported V1 context as encrypted sidecars.

**Architecture:** A read-only frozen-source planner compiles the approved PostgreSQL 17 snapshot into one canonical current-V2 plan. A production-image offline runner submits one idempotent financial command that verifies the empty target, creates or exactly verifies catalog and encrypted privacy records, and appends all current-native ledger events through `LedgerCommitter` in one PostgreSQL transaction. An independent reducer and two isolated DigitalOcean rehearsals verify semantics, replay, hashes, cleanup, and determinism; production apply remains outside this plan until separately approved.

**Tech Stack:** Python 3.12, Pydantic 2, SQLAlchemy 2, PostgreSQL 17, Alembic, `cryptography` AES-256-GCM/HKDF-SHA256, FastAPI, Click, pytest, uv, Docker on DigitalOcean.

---

## Fixed contract and execution boundaries

- Work only in the global worktree
  `/Users/xuyanyue/.config/superpowers/worktrees/track-anywhere/full-v1-history-backfill`
  on branch `codex/full-v1-history-backfill`.
- Do not copy the unrelated dirty files from the main checkout. If pure legacy
  mapping logic is useful, inspect it with `git show 3ae4209:<path>` and port only
  the independently tested pure algorithm.
- The only source is the fixed PG17 dump with SHA-256
  `a125b857a317e8c017d7028a26f78cf664ff6d57f6c0e698b3c229acf5d6cf9e`.
  The importer must also bind manifest hash
  `f1cb565c646dc759b203efad3a5584492f3c824a16abb728bbce83150413597f`
  and card-review hash
  `237f964a990018eb7fc91b9e45ba001ebb319456577f14263e3a444dc4d54430`.
- Keep the UUIDv5 namespace
  `3f021172-6aa9-5b36-9208-f238bc35c596` and preserve the frozen account-ID
  protocol. Never mix the target Book ID into a legacy account ID.
- Local execution is limited to `.venv`, pure/unit/contract tests, formatting,
  linting, and static checks. **Never run `docker build` locally.**
- Build the application image and run complete PG17/Docker/replay/rehearsal gates
  only on SSH alias `do-sfo3`, from a clean clone of the pushed commit.
- This branch may be pushed. Do not push an image to GHCR, modify Dokploy, enter
  production maintenance, stream a plan to production, or execute the production
  command without a new explicit approval.
- Apply @test-driven-development and @testing-anti-patterns to every production
  behavior: write one failing behavior test, observe the expected failure, add
  the minimum code, observe green, refactor, re-run the focused suite, then
  commit. Never assert on mocks when a real pure object or PG17 integration test
  is available.

### Expected canonical outcome

The compiler and independent verifier must derive and then pin these facts:

| Fact | Expected |
| --- | ---: |
| Assets | 20 |
| Accounts | 121 |
| Categories / versions | 37 / 37 |
| Journal transactions | 138 |
| Postings | 290 |
| Reversal relations | 8 |
| Current reporting lines | 38 |
| Current-native ledger events | 176 |
| Historical typed credit-card rows | 0 |
| Description sidecars | 138 |
| Import archives | 1 |
| Quarantine | 0 |

The 176 events are 138 current-native journal facts, including the eight
reversal facts, followed by 38 final reporting assignments. Treat a count
mismatch as a source-contract failure, not as permission to adjust the fixture.

## Task 1: Protected-content contracts, keyring, and cryptography

**Files:**

- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Create: `backend/app/track_anywhere/application/privacy/__init__.py`
- Create: `backend/app/track_anywhere/application/privacy/protected_content.py`
- Create: `backend/app/track_anywhere/infrastructure/crypto/__init__.py`
- Create: `backend/app/track_anywhere/infrastructure/crypto/protected_content.py`
- Modify: `backend/app/track_anywhere/observability/audit.py`
- Modify: `backend/app/track_anywhere/observability/metrics.py`
- Create: `backend/tests/v2/unit/test_protected_content_crypto.py`
- Modify: `backend/tests/v2/unit/test_sensitive_log_redaction.py`

**Step 1: Write the failing contract and crypto tests**

Define the wished-for API in tests before implementation:

```python
keyring = ProtectedContentKeyring.from_mapping(
    active_key_ref="v1",
    keys={"v1": bytes(range(32))},
)
cipher = ProtectedContentCipher(keyring, nonce_source=lambda size: b"n" * size)
sealed = cipher.encrypt(
    book_id=BOOK_ID,
    sidecar_id=SIDECAR_ID,
    kind="transaction_description",
    plaintext=b'{"purpose":"coffee"}',
)
assert sealed.algorithm == "AES-256-GCM+HKDF-SHA256"
assert sealed.nonce == b"n" * 12
assert cipher.decrypt(..., sealed=sealed) == b'{"purpose":"coffee"}'
```

Add separate tests for random nonce/ciphertext variation, deterministic plaintext
hash, wrong key version, every AAD field mutation, invalid/non-0400 key file,
wrong key length, tampered ciphertext, and error/log representations that never
contain plaintext, ciphertext, nonce, purpose, or memo.

Use strict frozen contracts such as:

```python
class TransactionDescription(FrozenContract):
    purpose: str | None
    transaction_memo: str | None
    line_memos: tuple[str | None, ...]

class ProtectedContentEnvelope(FrozenContract):
    kind: Literal["transaction_description", "import_archive"]
    canonical_plaintext: bytes = Field(repr=False)
```

These are application privacy contracts, never event contracts.

**Step 2: Run the focused tests and observe RED**

Run:

```bash
uv run pytest backend/tests/v2/unit/test_protected_content_crypto.py \
  backend/tests/v2/unit/test_sensitive_log_redaction.py -q
```

Expected: FAIL because protected-content modules and the new redaction vocabulary
do not exist.

**Step 3: Add the direct dependency and minimum implementation**

- Add `cryptography>=48` to project dependencies and run `uv lock`.
- Load only a secret file referenced by
  `TRACK_ANYWHERE_PROTECTED_CONTENT_KEYRING_FILE`; do not support a raw master-key
  environment variable.
- Parse exact base64-encoded 32-byte master keys and a versioned active `key_ref`.
- Derive a Book-specific 32-byte key with HKDF-SHA256.
- Encrypt with AES-256-GCM and a random 96-bit nonce.
- Use canonical JSON AAD containing `book_id`, `sidecar_id`, `kind`, `key_ref`,
  and hexadecimal plaintext `content_hash`.
- Make decryption fail closed with a stable non-sensitive exception.
- Extend sensitive-field guards with at least `description`, `purpose`,
  `plaintext`, `line_memo`, `ciphertext`, and `nonce`.

**Step 4: Run GREEN and dependency consistency checks**

Run:

```bash
uv sync --locked --extra postgres
uv run pytest backend/tests/v2/unit/test_protected_content_crypto.py \
  backend/tests/v2/unit/test_sensitive_log_redaction.py -q
git diff --check
```

Expected: all selected tests PASS; `uv sync --locked` and `git diff --check`
exit 0.

**Step 5: Commit**

```bash
git add pyproject.toml uv.lock backend/app/track_anywhere/application/privacy \
  backend/app/track_anywhere/infrastructure/crypto \
  backend/app/track_anywhere/observability \
  backend/tests/v2/unit/test_protected_content_crypto.py \
  backend/tests/v2/unit/test_sensitive_log_redaction.py
git commit -m "feat: add protected content encryption"
```

## Task 2: Immutable sidecar persistence and archive manifests

**Files:**

- Create: `alembic/versions/v2_0012_protected_content.py`
- Modify: `backend/app/track_anywhere/infrastructure/db/models/privacy.py`
- Modify: `backend/app/track_anywhere/infrastructure/db/models/__init__.py`
- Create: `backend/app/track_anywhere/infrastructure/db/repositories/privacy.py`
- Create: `backend/app/track_anywhere/application/privacy/service.py`
- Create: `backend/tests/v2/postgres/test_protected_content.py`
- Modify: `backend/tests/v2/postgres/test_catalog_constraints.py`
- Modify: `backend/tests/v2/postgres/test_v2_schema_guard.py`

**Step 1: Write failing PG17 tests**

Test real PostgreSQL behavior for:

- `create_or_exact_verify()` inserts once and replays without re-encrypting;
- same ID plus different `kind` or `content_hash` is a conflict;
- erased content is never recreated in place;
- active sidecars require a 12-byte nonce, the approved algorithm, and shaped
  `key_ref`;
- active ciphertext/key/nonce/content hash cannot be updated or deleted;
- only one-way `active -> erased` crypto erasure is legal;
- archive manifests are append-only and reference an active
  `kind='import_archive'` sidecar in the same Book;
- runtime permissions allow `SELECT`/`INSERT` but block arbitrary
  `UPDATE`/`DELETE`.

The repository API should be explicit:

```python
repository.create_or_exact_verify(session, proposed_sidecar)
repository.insert_archive_manifest(session, manifest)
repository.get_active_batch(session, book_id, sidecar_ids)
repository.list_archive_manifests(session, book_id)
```

**Step 2: Observe RED on a disposable PG17 test database**

Run with the existing isolated test URLs:

```bash
uv run --extra postgres pytest \
  backend/tests/v2/postgres/test_protected_content.py \
  backend/tests/v2/postgres/test_catalog_constraints.py \
  backend/tests/v2/postgres/test_v2_schema_guard.py -q
```

Expected: FAIL because the repository/manifest table and stronger immutability
rules do not exist.

**Step 3: Implement the migration and repository**

Migration `v2_0012_protected_content` must:

- have `down_revision = "v2_0011_oauth_resource_binding"`;
- strengthen `protected_description_sidecars` nonce/algorithm/key-ref checks;
- replace `v2_guard_description_sidecar_identity` with a trigger that permits
  only exact no-op writes or the existing active-to-erased transition and rejects
  deletion;
- create `import_archive_manifests` with `(book_id, archive_id)` composite FK to
  the sidecar, six 32-byte hashes (`source`, `manifest`, `card_review`, `plan`,
  `ndjson`, `seal`), JSONB record counts, and `created_at`;
- install insert/select-only runtime grants and an update/delete rejection trigger.

The application service canonicalizes, hashes, encrypts, and persists through
the caller's `Session`; it never opens another UoW and never logs protected
bytes.

**Step 4: Run GREEN and Alembic checks**

Run:

```bash
uv run --extra postgres pytest \
  backend/tests/v2/postgres/test_protected_content.py \
  backend/tests/v2/postgres/test_catalog_constraints.py \
  backend/tests/v2/postgres/test_v2_schema_guard.py -q
uv run --extra postgres alembic check
git diff --check
```

Expected: selected tests PASS; Alembic reports no new operations; diff check
exits 0.

**Step 5: Commit**

```bash
git add alembic/versions/v2_0012_protected_content.py \
  backend/app/track_anywhere/application/privacy \
  backend/app/track_anywhere/infrastructure/db/models \
  backend/app/track_anywhere/infrastructure/db/repositories/privacy.py \
  backend/tests/v2/postgres/test_protected_content.py \
  backend/tests/v2/postgres/test_catalog_constraints.py \
  backend/tests/v2/postgres/test_v2_schema_guard.py
git commit -m "feat: persist immutable protected content"
```

## Task 3: Explicit description and import-archive read surfaces

**Files:**

- Modify: `backend/app/track_anywhere/queries/journal.py`
- Create: `backend/app/track_anywhere/queries/protected_content.py`
- Modify: `backend/app/track_anywhere/api/dependencies.py`
- Modify: `backend/app/track_anywhere/api/app.py`
- Modify: `backend/app/track_anywhere/api/v2/query_routes/authorization.py`
- Modify: `backend/app/track_anywhere/api/v2/query_routes/journal.py`
- Create: `backend/app/track_anywhere/api/v2/query_routes/protected_content.py`
- Modify: `backend/app/track_anywhere/api/v2/query_routes/router.py`
- Modify: `backend/app/track_anywhere/api/v2/queries.py`
- Create: `backend/tests/v2/contract/test_v2_protected_content_api.py`
- Modify: `backend/tests/v2/contract/test_v2_query_api.py`
- Modify: `backend/tests/v2/contract/test_public_api_v2_snapshot.py`
- Modify: `backend/tests/snapshots/public-api-v2.json`

**Step 1: Write failing REST and query tests**

Cover these behaviors independently:

- normal journal list/show omit the `description` key entirely;
- `include_description=true` decrypts descriptions only for an active Book
  owner with `ledger:read` and returns typed purpose/transaction memo/line memos;
- the query performs one batch sidecar read per page, not N+1 reads;
- viewer, other-Book member, absent keyring, erased sidecar, and tampered content
  fail closed without plaintext in the response or logs;
- `GET /api/v2/books/{book_id}/import-archives` exposes only hashes/counts;
- `GET /api/v2/books/{book_id}/import-archives/{archive_id}/export` is an
  explicit owner-only decrypted NDJSON export;
- cross-Book archive access is indistinguishable from absent access.

Use `response_model_exclude_unset=True`; default response shape must remain
compatible with current clients.

**Step 2: Observe RED**

```bash
uv run pytest backend/tests/v2/contract/test_v2_query_api.py \
  backend/tests/v2/contract/test_v2_protected_content_api.py \
  backend/tests/v2/contract/test_public_api_v2_snapshot.py -q
```

Expected: FAIL because explicit protected-content reads are not wired.

**Step 3: Implement the minimum read path**

- Add internal `description_ref` to `JournalItem` and populate it from the
  journal projection; do not add plaintext to the base serializer.
- Add a batched protected-content query/decrypt service.
- Add a reusable Book-owner authorization dependency that still requires
  `ledger:read` and active membership.
- Inject an optional secret-file-backed cipher through `RuntimeDependencies`.
  Explicit reads fail closed when it is absent; startup and ordinary reads do
  not fail merely because no key file is configured.
- Keep archive export as a JSON transport envelope with `content_type`, SHA-256,
  and `ndjson`, so the generic HTTP client need not learn a streaming format.

**Step 4: Run GREEN and snapshot verification**

```bash
uv run pytest backend/tests/v2/contract/test_v2_query_api.py \
  backend/tests/v2/contract/test_v2_protected_content_api.py \
  backend/tests/v2/contract/test_public_api_v2_snapshot.py -q
git diff --check
```

Expected: all selected tests PASS; the reviewed snapshot contains only the new
explicit query schemas/routes.

**Step 5: Commit**

```bash
git add backend/app/track_anywhere/queries \
  backend/app/track_anywhere/api \
  backend/tests/v2/contract/test_v2_query_api.py \
  backend/tests/v2/contract/test_v2_protected_content_api.py \
  backend/tests/v2/contract/test_public_api_v2_snapshot.py \
  backend/tests/snapshots/public-api-v2.json
git commit -m "feat: add explicit protected content reads"
```

## Task 4: CLI support and MCP non-exposure

**Files:**

- Modify: `cli/track_anywhere_cli/click_ledger.py`
- Modify: `cli/track_anywhere_cli/command_ledger.py`
- Create: `cli/track_anywhere_cli/click_archive.py`
- Create: `cli/track_anywhere_cli/command_archive.py`
- Modify: `cli/track_anywhere_cli/click_app.py`
- Modify: `cli/track_anywhere_cli/commands.py`
- Modify: `cli/track_anywhere_cli/output.py`
- Modify: `cli/tests/test_cli_v2_ledger.py`
- Create: `cli/tests/test_cli_v2_archives.py`
- Modify: `cli/tests/test_cli_v2_boundaries.py`
- Modify: `contract_tests/test_cli_conformance.py`
- Modify: `backend/tests/v2/contract/test_oauth_mcp_protocol.py`

**Step 1: Write failing CLI and MCP boundary tests**

Tests must prove:

- `ta tx list/show ... --include-description` sends
  `include_description=true` and omission sends no truthy flag;
- `ta archive list BOOK_ID` and `ta archive export BOOK_ID ARCHIVE_ID` call only
  the explicit owner query routes;
- archive output uses the existing JSON presenter and never goes to stderr;
- MCP list/get tool schemas contain no `include_description` input;
- MCP structured output recursively contains no `description`, `purpose`,
  `memo`, `line_memos`, `ndjson`, ciphertext, or key metadata;
- the MCP tool set does not gain an archive/import tool.

**Step 2: Observe RED**

```bash
uv run pytest cli/tests/test_cli_v2_ledger.py \
  cli/tests/test_cli_v2_archives.py \
  cli/tests/test_cli_v2_boundaries.py \
  contract_tests/test_cli_conformance.py \
  backend/tests/v2/contract/test_oauth_mcp_protocol.py -q
```

Expected: FAIL because the CLI flags/commands do not exist.

**Step 3: Implement the minimum CLI surface**

- Add the explicit flag to `tx list` and `tx show` only.
- Add read-only `archive list/export` definitions and Click registration.
- Bump `CLI_SCHEMA_VERSION` only if the existing contract requires it.
- Do not edit `backend/app/track_anywhere/mcp/tools.py` to expose protected
  content. If its serializer sees the new internal `description_ref`, explicitly
  retain the existing public fields only.

**Step 4: Run GREEN**

```bash
uv run pytest cli/tests/test_cli_v2_ledger.py \
  cli/tests/test_cli_v2_archives.py \
  cli/tests/test_cli_v2_boundaries.py \
  contract_tests/test_cli_conformance.py \
  backend/tests/v2/contract/test_oauth_mcp_protocol.py -q
git diff --check
```

Expected: all selected tests PASS; MCP schemas remain plaintext-free.

**Step 5: Commit**

```bash
git add cli/track_anywhere_cli cli/tests contract_tests/test_cli_conformance.py \
  backend/tests/v2/contract/test_oauth_mcp_protocol.py
git commit -m "feat: expose protected content through explicit CLI reads"
```

## Task 5: Frozen V1 source contract and exact extraction

**Files:**

- Create: `backend/tools/frozen_v1_history/__init__.py`
- Create: `backend/tools/frozen_v1_history/constants.py`
- Create: `backend/tools/frozen_v1_history/manifest.py`
- Create: `backend/tools/frozen_v1_history/extract.py`
- Create: `backend/tools/frozen_v1_history/inventory.py`
- Create: `backend/tools/frozen_v1_history/namespaces.py`
- Create: `backend/tools/frozen_v1_history/normalize.py`
- Create: `backend/tools/frozen_v1_history/reversal_links.py`
- Create: `backend/tools/frozen_v1_history/credit_card_review.py`
- Create: `backend/tools/frozen_v1_history/sql/*.sql`
- Create: `backend/tests/v2/imports/test_frozen_source_contract.py`
- Create: `backend/tests/v2/imports/test_extract_determinism.py`
- Create: `backend/tests/v2/imports/test_uuid_protocol.py`
- Create: `backend/tests/v2/imports/test_exact_units.py`
- Create: `backend/tests/v2/imports/test_reversal_links.py`
- Create: `backend/tests/v2/imports/test_credit_card_review.py`

**Step 1: Write failing pure/source tests one behavior at a time**

Start with constants and manifest validation, then add one RED/GREEN cycle for:

- source Alembic revision `0019_posting_constraints`;
- exact inventory of 20 assets, 121 accounts, 37 categories/versions, 135
  transactions, 284 postings, 43 lines/classification events, six investment
  events, zero valuations, zero attachments, two counterparties, and 729 source
  receipts;
- read-only `REPEATABLE READ` extraction with a canonical row digest independent
  of query scheduling;
- UUIDv5 golden IDs, including known account fixtures;
- signed legacy amount to positive integer units plus debit/credit side;
- exact eight-decimal USDT preservation and rejection of inexact scale;
- five source reversal links, with ambiguity blocking rather than guessing;
- the approved 22 card-touching transactions, 23 card postings, three inverse
  corrections, five natural-balance expectations, and one retired alias.

**Step 2: Observe RED**

```bash
uv run --extra postgres pytest backend/tests/v2/imports/test_frozen_source_contract.py \
  backend/tests/v2/imports/test_extract_determinism.py \
  backend/tests/v2/imports/test_uuid_protocol.py \
  backend/tests/v2/imports/test_exact_units.py \
  backend/tests/v2/imports/test_reversal_links.py \
  backend/tests/v2/imports/test_credit_card_review.py -q
```

Expected: FAIL because the frozen-source package does not exist.

**Step 3: Port only pure, current-relevant logic**

- Use parameterized read-only SQL files and explicit column lists.
- Bind the full manifest and review document hashes before normalization.
- Use strict/frozen models; reject extra fields, duplicate IDs, missing parents,
  invalid timestamps, invalid scales, unbalanced transactions, and ambiguous
  reversals.
- Never import the old pipeline/load/checkpoint/quarantine code, old backfill
  tables, old migrations, or removed historical event types.

**Step 4: Run GREEN**

```bash
uv run --extra postgres pytest backend/tests/v2/imports/test_frozen_source_contract.py \
  backend/tests/v2/imports/test_extract_determinism.py \
  backend/tests/v2/imports/test_uuid_protocol.py \
  backend/tests/v2/imports/test_exact_units.py \
  backend/tests/v2/imports/test_reversal_links.py \
  backend/tests/v2/imports/test_credit_card_review.py -q
git diff --check
```

Expected: selected source tests PASS with no dump content committed to Git.

**Step 5: Commit**

```bash
git add backend/tools/frozen_v1_history backend/tests/v2/imports
git commit -m "feat: define the frozen V1 source contract"
```

## Task 6: Canonical current-V2 plan compiler

**Files:**

- Create: `backend/app/track_anywhere/application/imports/__init__.py`
- Create: `backend/app/track_anywhere/application/imports/contracts.py`
- Create: `backend/app/track_anywhere/application/imports/event_compiler.py`
- Create: `backend/tools/frozen_v1_history/planner.py`
- Create: `backend/tools/frozen_v1_history/__main__.py`
- Create: `backend/tests/v2/imports/test_plan_contract.py`
- Create: `backend/tests/v2/imports/test_plan_determinism.py`
- Create: `backend/tests/v2/imports/test_plan_topology.py`
- Create: `backend/tests/v2/imports/test_plan_card_review.py`
- Create: `backend/tests/v2/imports/test_plan_reporting.py`
- Create: `backend/tests/v2/imports/test_plan_archive.py`
- Create: `backend/tests/v2/imports/test_plan_redaction.py`
- Create: `backend/tests/v2/imports/fixtures/frozen_plan_summary.json`

**Step 1: Write failing plan contract tests**

Define a strict frozen `FrozenFinancialHistoryPlan` with:

```python
class FrozenFinancialHistoryPlan(FrozenContract):
    contract_version: Literal[1]
    target_book_id: UUID
    source_dump_hash: HexSha256
    manifest_hash: HexSha256
    card_review_hash: HexSha256
    assets: tuple[PlannedAsset, ...]
    accounts: tuple[PlannedAccount, ...]
    categories: tuple[PlannedCategory, ...]
    descriptions: tuple[PlannedProtectedContent, ...] = Field(repr=False)
    archive: PlannedProtectedContent = Field(repr=False)
    events: tuple[PlannedLedgerEvent, ...]
    expected_terminal_hash: HexSha256
```

Tests must require byte-identical canonical plan output across source row shuffle,
`TZ`, locale, hash seed, extraction batch size, and worker scheduling. Assert the
expected counts and topology, legal opaque V2 external references, 138
description payloads, one archive, zero quarantine, and no plaintext in summary,
stderr, exceptions, or `repr(plan)`.

The archive canonical NDJSON must contain classification audit, incomplete
investment activity, five uncategorized FX reporting facts, institution and
opaque counterparty metadata, hashes, row counts, and explicit omission reasons.

**Step 2: Observe RED**

```bash
uv run --extra postgres pytest \
  backend/tests/v2/imports/test_plan_contract.py \
  backend/tests/v2/imports/test_plan_determinism.py \
  backend/tests/v2/imports/test_plan_topology.py \
  backend/tests/v2/imports/test_plan_card_review.py \
  backend/tests/v2/imports/test_plan_reporting.py \
  backend/tests/v2/imports/test_plan_archive.py \
  backend/tests/v2/imports/test_plan_redaction.py -q
```

Expected: FAIL because the compiler/contracts do not exist.

**Step 3: Implement canonical compilation**

- Order assets/accounts/categories by deterministic IDs and dependency order.
- Order original financial facts by effective time and opaque source ID, with
  reversal/correction facts after originals and final reporting after journal.
- Generate kind-specific UUIDv5 IDs for transactions, postings, command,
  sidecars, reporting lines, and archive.
- Compile 176 current event contracts and expected stream version 0 for each new
  stream. All events share the deterministic import `command_id` and offline
  actor.
- Compute reversal `original_event_id` and `original_event_hash` from the same
  canonical batch positions/stream versions that the event store will use.
- Compute `expected_terminal_hash` for the required empty target head without
  calling the target event store.
- Write plan bytes only to stdout; write only hashes/counts to stderr.

**Step 4: Run GREEN**

```bash
uv run --extra postgres pytest backend/tests/v2/imports/test_plan_*.py -q
git diff --check
```

Expected: all plan tests PASS and the committed fixture contains only counts and
hashes, never names, amounts, purpose, or memo.

**Step 5: Commit**

```bash
git add backend/app/track_anywhere/application/imports \
  backend/tools/frozen_v1_history \
  backend/tests/v2/imports
git commit -m "feat: compile canonical V1 financial history plans"
```

## Task 7: Import-specific catalog preflight and exact apply

**Files:**

- Create: `backend/app/track_anywhere/infrastructure/db/repositories/frozen_import.py`
- Create: `backend/tests/v2/postgres/test_frozen_import_catalog.py`
- Create: `backend/tests/v2/postgres/test_frozen_import_preconditions.py`

**Step 1: Write failing PG17 repository tests**

Test create-or-exact-verify against a target preseeded with the approved 16
assets and 64 accounts:

- exact rows replay as no-op;
- four missing assets, 57 missing accounts, and 37 categories/versions are
  inserted through ORM repositories in the caller's transaction;
- immutable field, account kind/normal side, precision, parent, or status drift
  aborts before any row remains;
- unexpected catalog rows, financial event head position other than zero,
  journal/reporting/card/sidecar/archive rows, or non-empty receipt conflict
  aborts;
- planned IDs must all belong to the target Book, while frozen account UUIDs
  remain independent of target Book ID;
- the retired alias is never referenced by a planned posting and has zero
  projected balance before closure.

**Step 2: Observe RED**

```bash
uv run --extra postgres pytest \
  backend/tests/v2/postgres/test_frozen_import_catalog.py \
  backend/tests/v2/postgres/test_frozen_import_preconditions.py -q
```

Expected: FAIL because the import repository does not exist.

**Step 3: Implement the minimum repository**

Expose only catalog/privacy operations and preflight queries. Do not import or
write event-store, head, receipt, projection, outbox, checkpoint, or balance
models from this repository. Return structured drift errors containing only
entity kind, opaque ID, and field name.

**Step 4: Run GREEN**

```bash
uv run --extra postgres pytest \
  backend/tests/v2/postgres/test_frozen_import_catalog.py \
  backend/tests/v2/postgres/test_frozen_import_preconditions.py -q
git diff --check
```

Expected: selected tests PASS and repository guardrail tests still prove no
direct ledger writes.

**Step 5: Commit**

```bash
git add backend/app/track_anywhere/infrastructure/db/repositories/frozen_import.py \
  backend/tests/v2/postgres/test_frozen_import_catalog.py \
  backend/tests/v2/postgres/test_frozen_import_preconditions.py
git commit -m "feat: add frozen import catalog preflight"
```

## Task 8: One-command atomic financial import

**Files:**

- Create: `backend/app/track_anywhere/application/imports/import_frozen_financial_history.py`
- Modify: `backend/app/track_anywhere/application/ledger_committer.py`
- Modify: `backend/app/track_anywhere/application/command_bus.py`
- Modify: `backend/tests/v2/postgres/test_command_bus_write_boundary.py`
- Create: `backend/tests/v2/postgres/test_import_frozen_financial_history.py`
- Create: `backend/tests/v2/postgres/test_import_frozen_financial_history_atomicity.py`
- Create: `backend/tests/v2/postgres/test_import_frozen_financial_history_idempotency.py`
- Create: `backend/tests/v2/postgres/test_import_frozen_financial_history_card_alias.py`

**Step 1: Write failing command-boundary and happy-path tests**

The command request hash must contain only target/source/review/plan/terminal
hashes and counts, not protected bytes. The handler must return one
`LedgerWritePlan` with all 176 events. Tests should first prove:

- one command receipt, one Book lock, one outer transaction, one
  `append_and_project` call;
- all events have the same import command and offline actor identity;
- all current contracts/projectors accept the ordered batch;
- 20/121/37/37 catalog, 138/290 journal, eight reversals, 38 reporting lines,
  zero typed historical card rows, 138 descriptions, one archive, zero
  quarantine, and expected terminal hash;
- exact eight-decimal USDT units and reviewed card natural balances;
- alias closure occurs only after the projected zero balance is verified.

**Step 2: Observe RED**

```bash
uv run --extra postgres pytest \
  backend/tests/v2/postgres/test_command_bus_write_boundary.py \
  backend/tests/v2/postgres/test_import_frozen_financial_history.py \
  backend/tests/v2/postgres/test_import_frozen_financial_history_card_alias.py -q
```

Expected: FAIL because the command and narrow post-projection finalizer do not
exist.

**Step 3: Add the narrow finalizer and command**

- Extend `LedgerWritePlan` with an optional non-repr/non-comparable
  post-projection finalizer protocol. Invoke it in `execute_financial` after
  `append_and_project` and before receipt completion. The finalizer receives only
  the existing `Session` and immutable `AppendBatchResult`.
- Add generic boundary tests proving no finalizer is the unchanged path and any
  finalizer exception rolls back events, projections, catalog, sidecars, alias,
  archive, and receipt together.
- Authorize only the fixed offline actor and exact target Book; do not register a
  public route.
- Inside the locked transaction: revalidate plan and target, apply exact catalog,
  persist sidecars/archive, return the single write plan, then post-project verify
  alias balance zero, close it, and verify archive seal.
- Keep `LedgerCommitter` as the sole caller of event-store append and synchronous
  projection. Do not call ordinary online post commands one by one.

**Step 4: Add atomicity and idempotency RED/GREEN cycles**

For each injected failure point—catalog, sidecar N, event N, synchronous
projector N, finalizer, and receipt completion—write a failing test, observe it,
then make the minimum transactional correction. After failure assert target head
and every imported table remain at baseline.

Then test:

- same receipt/plan replay returns the stored result and creates zero rows;
- altered plan with same idempotency key conflicts;
- simulated commit-response loss resolves by exact replay;
- partial-prefix resume is impossible.

Run:

```bash
uv run --extra postgres pytest \
  backend/tests/v2/postgres/test_import_frozen_financial_history.py \
  backend/tests/v2/postgres/test_import_frozen_financial_history_atomicity.py \
  backend/tests/v2/postgres/test_import_frozen_financial_history_idempotency.py \
  backend/tests/v2/postgres/test_import_frozen_financial_history_card_alias.py \
  backend/tests/v2/postgres/test_command_bus_write_boundary.py -q
```

Expected: all selected tests PASS.

**Step 5: Commit**

```bash
git add backend/app/track_anywhere/application/imports \
  backend/app/track_anywhere/application/ledger_committer.py \
  backend/app/track_anywhere/application/command_bus.py \
  backend/tests/v2/postgres/test_command_bus_write_boundary.py \
  backend/tests/v2/postgres/test_import_frozen_financial_history*.py
git commit -m "feat: import frozen financial history atomically"
```

## Task 9: Production-image offline runner

**Files:**

- Create: `backend/app/track_anywhere/offline/__init__.py`
- Create: `backend/app/track_anywhere/offline/import_frozen_financial_history.py`
- Create: `backend/tests/v2/unit/test_offline_frozen_import_runner.py`
- Modify: `backend/tests/v2/unit/test_v1_runtime_removed.py`
- Modify: `backend/tests/v2/unit/test_single_service_deployment.py`

**Step 1: Write failing runner and negative-registration tests**

Require a bounded canonical plan on stdin, fixed hash/Book arguments, runtime
database URL, and keyring file. Assert:

- stdin larger than the fixed bound is rejected before parsing;
- target/hash mismatch exits nonzero before DB mutation;
- stdout/stderr contain only allowlisted receipt/count/hash fields;
- no plaintext plan file is opened or written;
- the runner module is present in the installed `track_anywhere` package/image;
- no FastAPI route, MCP tool, startup hook, Alembic data migration, `/api/v1`, or
  recurring job imports the runner.

**Step 2: Observe RED**

```bash
uv run pytest backend/tests/v2/unit/test_offline_frozen_import_runner.py \
  backend/tests/v2/unit/test_v1_runtime_removed.py \
  backend/tests/v2/unit/test_single_service_deployment.py -q
```

Expected: FAIL because the offline runner does not exist.

**Step 3: Implement the runner**

Expose a module entrypoint:

```bash
python -m track_anywhere.offline.import_frozen_financial_history \
  --target-book-id ... --plan-sha256 ... --stdin
```

Parse once, recompute the canonical plan hash, build the offline command/actor,
execute through the standard command bus, print a sanitized JSON summary, and
zero references to protected buffers as soon as practical.

**Step 4: Run GREEN**

```bash
uv run pytest backend/tests/v2/unit/test_offline_frozen_import_runner.py \
  backend/tests/v2/unit/test_v1_runtime_removed.py \
  backend/tests/v2/unit/test_single_service_deployment.py -q
git diff --check
```

Expected: selected tests PASS and the runtime remains V2-only.

**Step 5: Commit**

```bash
git add backend/app/track_anywhere/offline \
  backend/tests/v2/unit/test_offline_frozen_import_runner.py \
  backend/tests/v2/unit/test_v1_runtime_removed.py \
  backend/tests/v2/unit/test_single_service_deployment.py
git commit -m "feat: add private frozen history runner"
```

## Task 10: Independent reducer, cold replay, and mutation detection

**Files:**

- Create: `backend/tools/frozen_v1_history/reference_reducer.py`
- Create: `backend/tools/frozen_v1_history/verify.py`
- Create: `backend/tests/v2/imports/test_independent_semantic_parity.py`
- Create: `backend/tests/v2/imports/test_verifier_mutations.py`
- Create: `backend/tests/v2/replay/test_frozen_history_cold_replay.py`
- Modify: `backend/app/track_anywhere/verification.py`

**Step 1: Write failing independent-verifier tests**

The reference reducer must not import planner normalization, target
repositories, synchronous projector appliers, or online query code. Tests should
prove it detects independent mutations to:

- one posting side/unit/account/asset;
- event order, stream version, event hash, and terminal hash;
- reversal source ID/hash;
- reporting line/category/version;
- card natural balance and retired alias state;
- an eight-decimal USDT posting;
- description plaintext hash and archive seal;
- async checkpoint or projection digest.

Cold replay must copy stored events into a fresh target through the supported
ledger replay path and reproduce journal, balance, reversal, reporting, and
terminal digests.

**Step 2: Observe RED**

```bash
uv run --extra postgres pytest \
  backend/tests/v2/imports/test_independent_semantic_parity.py \
  backend/tests/v2/imports/test_verifier_mutations.py \
  backend/tests/v2/replay/test_frozen_history_cold_replay.py -q
```

Expected: FAIL because the independent reducer/verifier is absent.

**Step 3: Implement without shared normalization**

Use raw canonical source rows and explicit accounting equations in the reference
reducer. Extend the generic verifier only for reusable read-back facts; keep
source expected-value generation separate. Return a secret-free verification
object with counts and hashes only.

**Step 4: Run GREEN**

```bash
uv run --extra postgres pytest \
  backend/tests/v2/imports/test_independent_semantic_parity.py \
  backend/tests/v2/imports/test_verifier_mutations.py \
  backend/tests/v2/replay/test_frozen_history_cold_replay.py -q
git diff --check
```

Expected: all tests PASS and each mutation is rejected for the intended reason.

**Step 5: Commit**

```bash
git add backend/tools/frozen_v1_history \
  backend/app/track_anywhere/verification.py \
  backend/tests/v2/imports \
  backend/tests/v2/replay/test_frozen_history_cold_replay.py
git commit -m "test: add independent frozen history verification"
```

## Task 11: Safe dump streaming and two-target rehearsal harness

**Files:**

- Create: `scripts/stream-v1-dump-to-postgres.py`
- Create: `scripts/rehearse-frozen-v1-history.sh`
- Create: `backend/tests/v2/unit/test_stream_v1_dump_to_postgres.py`
- Create: `backend/tests/v2/unit/test_frozen_rehearsal_script.py`
- Create: `backend/tests/v2/imports/test_two_target_determinism.py`
- Modify: `scripts/verify-v2.sh`
- Modify: `backend/tests/v2/unit/test_verify_v2_script.py`
- Modify: `backend/tests/v2/unit/test_staging_harness.py`
- Modify: `backend/tests/v2/unit/test_ci_v2_gates.py`
- Modify: `compose.e2e.yaml`
- Modify: `scripts/staging-v2-smoke.sh`
- Modify: `.github/workflows/docker-image.yml`

**Step 1: Write failing script-structure and stream tests**

Tests must prove the stream helper computes the dump SHA while feeding
`pg_restore`, detects short read/restore failure/hash mismatch, and never creates
a dump file. Script-structure tests must require:

- unique run-scoped container/network/volume names;
- Docker `--network` internal, no published ports, PGDATA on tmpfs;
- pinned PG17 image digest used consistently by source and both targets;
- source read-only role and transaction mode;
- plan stdout piped directly into the candidate-image runner stdin;
- rehearsal-only keys in `/dev/shm` mode 0400;
- A uses `TZ=UTC`, `LC_ALL=C`, seed 0, batch 37, worker 1;
- B uses `TZ=Pacific/Auckland`, `LC_ALL=C.UTF-8`, seed 731, batch 13,
  worker 4;
- exact second receipt replay creates zero rows;
- trap cleanup proves zero run-scoped containers/networks/volumes remain before
  reporting PASS;
- reports enforce an allowlist and contain no DSN, names, balances, purpose,
  memo, ciphertext, nonce, or key.

**Step 2: Observe RED locally without building an image**

```bash
uv run pytest backend/tests/v2/unit/test_stream_v1_dump_to_postgres.py \
  backend/tests/v2/unit/test_frozen_rehearsal_script.py \
  backend/tests/v2/unit/test_verify_v2_script.py \
  backend/tests/v2/unit/test_staging_harness.py \
  backend/tests/v2/unit/test_ci_v2_gates.py -q
```

Expected: FAIL because the scripts and CI lane do not exist. This command must
not call `docker build`.

**Step 3: Implement the safe harness**

- Stream the fixed dump from stdin directly to `docker exec -i ... pg_restore`
  while hashing; never use `tee` or a regular temporary dump file.
- Restore one source and create two fully independent targets preseeded with the
  exact 64-account/16-asset production catalog fixture.
- Run planner/importer/verifier twice and compare deterministic IDs, plan/event
  order/payloads, terminal hash, balances, projections, description plaintext
  aggregate hash, and archive hash. Exclude random nonce/ciphertext only.
- Add a synthetic, no-real-dump import lane to `scripts/verify-v2.sh` and make
  image-build CI depend on it.
- Fix the existing staging harness so the inspected PG17 image digest is exactly
  the one actually run.

**Step 4: Run local GREEN structure tests**

```bash
uv run pytest backend/tests/v2/unit/test_stream_v1_dump_to_postgres.py \
  backend/tests/v2/unit/test_frozen_rehearsal_script.py \
  backend/tests/v2/unit/test_verify_v2_script.py \
  backend/tests/v2/unit/test_staging_harness.py \
  backend/tests/v2/unit/test_ci_v2_gates.py -q
git diff --check
```

Expected: all selected tests PASS. Do not run the full Docker rehearsal locally.

**Step 5: Commit**

```bash
git add scripts/stream-v1-dump-to-postgres.py \
  scripts/rehearse-frozen-v1-history.sh scripts/verify-v2.sh \
  scripts/staging-v2-smoke.sh compose.e2e.yaml \
  .github/workflows/docker-image.yml \
  backend/tests/v2/unit/test_stream_v1_dump_to_postgres.py \
  backend/tests/v2/unit/test_frozen_rehearsal_script.py \
  backend/tests/v2/unit/test_verify_v2_script.py \
  backend/tests/v2/unit/test_staging_harness.py \
  backend/tests/v2/unit/test_ci_v2_gates.py \
  backend/tests/v2/imports/test_two_target_determinism.py
git commit -m "test: add isolated frozen history rehearsal"
```

## Task 12: Deployment configuration and operator runbook

**Files:**

- Modify: `deploy/env/prod.env.example`
- Modify: `compose.prod.yaml`
- Create: `docs/operations/v1-financial-backfill.md`
- Create: `docs/operations/v1-financial-backfill-verification-template.md`
- Modify: `docs/operations/dokploy-deploy.md`
- Modify: `docs/operations/v2-final-verification.md`
- Modify: `docs/operations/v2-capability-matrix.md`
- Modify: `docs/operations/v2-client-capability-matrix.md`
- Modify: `backend/tests/v2/unit/test_capability_matrix.py`
- Modify: `backend/tests/v2/unit/test_single_service_deployment.py`

**Step 1: Write failing documentation/config contract tests**

Require the production compose example to mount a read-only keyring file at a
fixed path for the API and one-shot runner, never expose the raw key in env, and
never add a second public service. Require the runbook to include:

- fixed source/review hashes and target Book;
- clean SHA/image proof;
- backup plus isolated PG17 restore proof;
- maintenance/write blocking;
- plan via stdin only;
- atomic apply command;
- independent verify, cold replay, projection catch-up, authorized decrypt,
  archive seal/export, CLI/OAuth/MCP smoke;
- fresh-database restore and switch recovery, never repair events;
- a separate explicit production authorization checkbox.

**Step 2: Observe RED**

```bash
uv run pytest backend/tests/v2/unit/test_capability_matrix.py \
  backend/tests/v2/unit/test_single_service_deployment.py -q
```

Expected: FAIL because the protected-content config and runbook contract are
not documented.

**Step 3: Implement configuration and docs**

Keep one public FastAPI service plus PostgreSQL. Document the offline runner as
a one-shot process using the same immutable API image and runtime role. State
that ClamAV and a separate port-3000 service are not introduced by this work.

**Step 4: Run GREEN and doc checks**

```bash
uv run pytest backend/tests/v2/unit/test_capability_matrix.py \
  backend/tests/v2/unit/test_single_service_deployment.py -q
git diff --check
```

Expected: selected tests PASS and no secret value is committed.

**Step 5: Commit**

```bash
git add deploy/env/prod.env.example compose.prod.yaml docs/operations \
  backend/tests/v2/unit/test_capability_matrix.py \
  backend/tests/v2/unit/test_single_service_deployment.py
git commit -m "docs: add frozen history operations runbook"
```

## Task 13: Local verification, final review, and branch push

**Files:**

- Review: all files changed since `26c860f`

**Step 1: Run all local non-image gates**

Do not build an image locally. Run:

```bash
uv sync --locked --extra postgres
uv run pytest backend/tests/v2/unit -q
uv run pytest backend/tests/v2/imports -q
uv run pytest backend/tests/v2/contract cli/tests contract_tests -q
uv run python -m compileall -q backend/app backend/tools cli
uv run --extra postgres alembic check
git diff --check
```

Expected: every command exits 0. If PostgreSQL integration tests require a
container not already available locally, do not build an application image;
record the exact deferred command for DO and continue only with tests that are
valid in the local venv.

**Step 2: Run a whole-branch spec review**

Use @requesting-code-review with base `26c860f` and current HEAD. Fix every
Critical or Important issue via a new RED/GREEN cycle and re-review until
approved. Inspect the actual diff and test output; never rely only on an agent
report.

**Step 3: Verify source hygiene and commit any review fixes**

```bash
git status --short
git diff --stat 26c860f...HEAD
git log --oneline 26c860f..HEAD
git diff --check 26c860f...HEAD
```

Expected: only scoped backfill/protected-content files are changed and the
worktree has no unstaged/untracked implementation artifacts.

**Step 4: Push the branch**

```bash
git push -u origin codex/full-v1-history-backfill
git rev-list --left-right --count HEAD...@{upstream}
```

Expected: push exits 0 and divergence is `0 0`.

## Task 14: Clean DigitalOcean build and full PG17 gates

**Files:**

- Remote clean clone only; do not edit source on the server.

**Step 1: Create a fresh clean checkout for the exact pushed SHA**

From local, calculate `SHA=$(git rev-parse HEAD)` and a new run ID. On `do-sfo3`:

```bash
gh repo clone vibeanyorg/track-anywhere "$REMOTE_DIR" -- \
  --branch codex/full-v1-history-backfill --single-branch
cd "$REMOTE_DIR"
test "$(git rev-parse HEAD)" = "$SHA"
test -z "$(git status --porcelain)"
uv sync --locked --extra postgres
```

Expected: exact SHA, clean tree, locked dependency sync.

**Step 2: Run source/PG17 gates before building**

Start only run-scoped, loopback/internal PG17 resources and run:

```bash
bash scripts/verify-v2.sh
```

Expected: all unit, contract, PG17, concurrency, replay, CLI, frontend,
Alembic, and synthetic backfill lanes PASS. Always tear down the run-scoped
project and prove zero containers/volumes remain.

**Step 3: Build an immutable application image from the commit object**

Only on `do-sfo3`:

```bash
git archive --format=tar "$SHA" | docker build --pull \
  --label "org.opencontainers.image.revision=$SHA" \
  --target api-runtime \
  --tag "track-anywhere-api:v1-backfill-$SHA" -
```

Read back image ID and revision label; require `sha256:*` and exact SHA. This is
the first application image build in this plan.

**Step 4: Run exact-image staging**

```bash
TRACK_ANYWHERE_POSTGRES_IMAGE="$PINNED_PG17_IMAGE" \
TRACK_ANYWHERE_E2E_API_IMAGE="track-anywhere-api:v1-backfill-$SHA" \
bash scripts/staging-v2-smoke.sh \
  --source-commit "$SHA" --run-id "$STAGING_RUN_ID" \
  --report-dir "output/v2-staging-$SHA-$STAGING_RUN_ID"
```

Expected: secret-free `verification.json` reports PASS, exact revision, PG 17,
Alembic head, separated roles, verifier PASS, zero projection lag, legacy 404,
and complete cleanup.

## Task 15: Fixed-dump double rehearsal and stop gate

**Files:**

- Remote secret-free reports only.

**Step 1: Verify the dump locally and stream it once over SSH stdin**

```bash
test "$(shasum -a 256 "$FIXED_DUMP" | awk '{print $1}')" = \
  a125b857a317e8c017d7028a26f78cf664ff6d57f6c0e698b3c229acf5d6cf9e

ssh do-sfo3 "
  cd '$REMOTE_DIR'
  TRACK_ANYWHERE_CANDIDATE_IMAGE='track-anywhere-api:v1-backfill-$SHA' \
  TRACK_ANYWHERE_POSTGRES_IMAGE='$PINNED_PG17_IMAGE' \
  bash scripts/rehearse-frozen-v1-history.sh \
    --source-commit '$SHA' \
    --run-id '$REHEARSAL_RUN_ID' \
    --book-id a682ddd2-f26a-5ad1-8f0a-f2fc1f75fa3d \
    --report-dir 'output/v1-backfill-$SHA-$REHEARSAL_RUN_ID' \
    --dump-stdin
" < "$FIXED_DUMP"
```

Expected: the dump is never persisted on DO; source and both targets use PG17
tmpfs/internal networks/no host ports; target A and B independently import and
verify.

**Step 2: Require exact A/B parity and idempotent replay**

Both targets must meet the canonical outcome table. A/B must have identical
source/manifest/review/plan hashes, deterministic IDs, event order/payloads,
terminal hash, balance/projection digests, decrypted-description aggregate hash,
archive content hash, and zero quarantine. Only nonce/ciphertext may differ.

Re-run the same receipt on each target and require zero inserted events/rows and
unchanged Book head/terminal hash.

**Step 3: Read and validate the allowlisted report**

```bash
ssh do-sfo3 "
  cd '$REMOTE_DIR'
  uv run python -m backend.tools.frozen_v1_history verify-report \
    'output/v1-backfill-$SHA-$REHEARSAL_RUN_ID/summary.json'
"
```

Expected: PASS with only SHA, image identity, PG/Alembic versions, role names,
counts, receipt state, hashes, quarantine count, and cleanup state.

**Step 4: Stop before production**

Report the exact branch SHA, remote image ID/revision, test totals, canonical
plan/terminal/projection/content hashes, A/B parity, and cleanup proof. Do not
push the image, change Dokploy, create the production backup, or run the
production importer. Ask for the separate production-apply authorization.

