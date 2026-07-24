# Everyday Entry Gateway Parallel Worktree Plan

**Baseline:** `main@499ab9a8a59b5241f1fa78b493a58b0e26c0f365`  
**Integration branch:** `codex/eeg-integration`  
**Status:** Active implementation plan

## Purpose

Implement the Everyday Entry Gateway without allowing the domain contract,
database migration order, REST/MCP/CLI behavior, or accounting invariants to
drift across parallel worktrees.

Use one integration worktree and no more than four active implementation
worktrees. All feature branches are based on the current integration branch,
not on a long-lived independent copy of `main`.

## Branch graph

```text
main
└── codex/eeg-integration
    ├── codex/eeg-contract
    ├── codex/eeg-safety
    ├── codex/eeg-core
    ├── codex/eeg-storage
    ├── codex/eeg-read-model
    ├── codex/eeg-rest
    ├── codex/eeg-cli
    ├── codex/eeg-mcp
    ├── codex/eeg-golden
    └── codex/eeg-history-repair
```

Recommended physical layout:

```text
/home/deploy/worktrees/track-anywhere-eeg/
  integration/
  lane-a/
  lane-b/
  lane-c/
  lane-d/
```

Lane directories may be removed and recreated between waves. A Git branch must
never be checked out by more than one worktree.

## Wave 0: freeze the boundary and stop new bad writes

### `codex/eeg-contract`

Owns:

- `backend/app/track_anywhere/application/entries/`
- focused entry contract unit tests and canonical fixtures

Delivers:

- discriminated entry input contracts;
- amount denomination and source-text contract;
- prepared status, preview, clarification, warning, and error contracts;
- commit input contract;
- stable service protocols used by REST, MCP, and CLI;
- canonical examples for expense, income, transfer, card payment, refund, and
  adjustment.

Must not add routes, migrations, MCP registration, or CLI registration.

### `codex/eeg-safety`

Owns the initial Phase 0 edits to:

- `backend/app/track_anywhere/mcp/tools.py`
- `backend/app/track_anywhere/mcp/server.py`
- related MCP contract tests

Delivers:

- old expense and credit-card charge writes hidden from the ordinary Agent
  surface;
- ordinary MCP account creation restricted to supported asset/liability types;
- unambiguous write-side amount documentation;
- regression tests proving integer read units are not write-side amounts.

Raw REST/CLI accounting operations remain available. Do not delete migration or
administrative capabilities.

Wave 0 branches merge before later worktrees are created.

## Wave 1: four parallel lanes

### `codex/eeg-core`

Owns:

- `backend/app/track_anywhere/application/entries/` implementation after
  contract freeze;
- `backend/app/track_anywhere/domain/journal/models.py`;
- the extraction of pure reporting validation/building from
  `application/journal/assign_reporting_lines.py`;
- focused unit tests.

Delivers amount normalization, account/category resolution, policies, duplicate
decisions that do not require persistence, and the entry compiler. The compiler
must produce one `LedgerWritePlan` containing the financial and reporting
events. It must compile credit-card charges/payments through their typed
semantics.

### `codex/eeg-storage`

Sole owner of:

- `alembic/versions/v2_0014_everyday_entry_gateway.py`;
- new or changed infrastructure DB models and repositories;
- persistence integration tests;
- protected narrative V2 persistence changes.

Delivers prepared intents, expiration, token hashes, strong external-reference
deduplication, HMAC source fingerprints, category usage metadata required by the
resolvers, and `transaction_narrative_v2`.

No other branch may allocate an Alembic revision during this program wave.

### `codex/eeg-read-model`

Owns:

- `backend/app/track_anywhere/queries/everyday_entries.py`;
- query tests and reader-facing result contracts.

Delivers `EverydayEntryView` by composing existing synchronous projections and
authorized protected-content reads. Prefer a query model over a new materialized
projection until evidence shows a projection is required.

### `codex/eeg-cli`

Initially owns only:

- `cli/track_anywhere_cli/click_entries.py`;
- `cli/track_anywhere_cli/command_entries.py`;
- focused CLI tests using a fake requester.

Build against the frozen REST JSON examples. Do not wire `click_app.py`,
`commands.py`, capability output, or rename raw commands until REST has merged.

## Wave 2: adapters and parity

### `codex/eeg-rest`

Owns:

- application prepare/commit orchestration;
- `backend/app/track_anywhere/api/v2/entries.py`;
- `backend/app/track_anywhere/api/v2/entry_schemas.py`;
- entry error mapping;
- `api/v2/router.py` wiring;
- the public API snapshot.

Prepare stores a short-lived intent. Commit takes only `intent_id`,
`commit_token`, and `request_id`, revalidates under the Book lock, and persists
ledger events plus protected narrative in one database transaction.

### `codex/eeg-mcp`

Owns:

- `backend/app/track_anywhere/mcp/entry_tools.py`;
- MCP entry-tool tests;
- final MCP composition after rebasing on REST.

MCP is an authentication, parameter-mapping, and presentation adapter. It must
not duplicate resolver, policy, money, duplicate, or accounting rules. New
tools launch in shadow/preview mode before commit is exposed.

### `codex/eeg-golden`

Owns cross-surface golden fixtures and parity tests. The required scenarios are
listed in the design. Tests must validate final events, projections, categories,
amounts, and relationships, not merely HTTP 200 responses.

Do not merge permanent `xfail` placeholders. Test scaffolding may be developed
in parallel but merges only when the implemented behavior passes.

### CLI completion

After REST merges, rebase `codex/eeg-cli` and add the thin registration changes
to `click_app.py`, `commands.py`, protocol capabilities, and schema output.
Existing raw commands remain as compatibility aliases for at least one release
and receive an explicit deprecation warning before removal.

## Wave 3: history repair and deprecation

`codex/eeg-history-repair` owns read-only discovery, dry-run planning, stable
idempotency keys, reversal/replacement execution, reconciliation checks, and
closing erroneous accounts.

Development and tests must never run the repair against production. Production
execution requires separate explicit authorization after dry-run evidence is
reviewed.

## Shared-file ownership

| Path | Owner |
|---|---|
| `alembic/versions/` | storage only |
| `domain/journal/models.py` | core only |
| `application/journal/assign_reporting_lines.py` | core only |
| `api/v2/router.py` | REST only |
| `api/v2/schemas.py` | unchanged; entries use `entry_schemas.py` |
| `mcp/tools.py`, `mcp/server.py` | safety first; frozen afterward |
| `mcp/entry_tools.py` | MCP only |
| `cli/click_app.py`, `cli/commands.py` | CLI completion only |
| API snapshot | REST only |
| README/capability matrix/reference docs | integration owner |

If a task needs another lane's owned file, stop and send a small interface
request to the owner or integration branch. Do not opportunistically edit it.

## Merge order

1. `codex/eeg-contract`
2. `codex/eeg-safety`
3. `codex/eeg-storage`
4. `codex/eeg-core`
5. `codex/eeg-read-model`
6. `codex/eeg-rest`
7. `codex/eeg-cli`
8. `codex/eeg-mcp`
9. `codex/eeg-golden`
10. `codex/eeg-history-repair`

Branches rebase on the latest `codex/eeg-integration` immediately before merge.
Use non-fast-forward merges on the integration branch to preserve lane
boundaries. The final reviewed integration branch is proposed to `main` as one
program-level pull request.

## Verification gates

Per lane:

- contract/core: focused pure unit tests;
- storage: PostgreSQL 17 migrations, repositories, constraints, rollback,
  expiration, and concurrency;
- read model: authorization, redaction, refund/reversal links, and exact amount
  rendering;
- REST: API contract, token security, idempotency, stale intent, atomic rollback,
  and OpenAPI snapshot;
- CLI/MCP: adapter contract tests and parity against the shared service;
- repair: dry-run determinism and reversal/replacement reconciliation.

Per wave:

```bash
uv run pytest backend/tests
```

Final program gate:

```bash
bash scripts/verify-v2.sh
bash scripts/e2e-docker-postgres.sh
```

Each active database-bearing worktree uses its own PostgreSQL database or
isolated Compose project and automatically selected loopback ports. Never run
different migration heads against the same schema.

## Integration review checklist

- Both reference documents were read before implementation.
- The branch changed only its owned files or documented an approved exception.
- Money remains exact and never uses `float`.
- Sensitive narrative never enters immutable event payloads, logs, errors, or
  token contents.
- Normal expense classification is atomic.
- Preview and commit cannot drift.
- Commit rechecks expiry, token, account/category state, duplicate risk, and
  Book write state.
- REST, MCP, and CLI call the same application service/compiler.
- Every behavior change has focused tests and the reported test command actually
  passed.

