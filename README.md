# Track Anywhere

Track Anywhere is a PostgreSQL 17 event ledger for exact, double-entry personal
accounting. Financial writes append immutable, typed events; synchronous
projections make committed balances and journal reads visible immediately from
another process.

The repository contains a V2-only FastAPI backend, a `ta` CLI, and a Next.js
frontend. There is no legacy runtime, local database fallback, or compatibility
API. Current HEAD contains no V1 data-import path; any future import must define
and verify an explicit V2 contract before it is added.

## Requirements

- Python 3.12 or 3.13
- `uv`
- PostgreSQL 17
- Node.js 22 for the frontend
- Docker with Compose for the isolated E2E stack

Install locked dependencies:

```bash
uv sync --locked --extra postgres
npm --prefix frontend ci
```

## Local API

Set `TRACK_ANYWHERE_DATABASE_URL` to a migrated PostgreSQL 17 database owned by
the non-owner runtime role, then start the API:

```bash
uv run uvicorn track_anywhere.api:app \
  --app-dir backend/app \
  --host 127.0.0.1 \
  --port 8000
```

Readiness is fail-closed and verifies PostgreSQL major version, migration head,
and runtime identity:

```bash
curl --fail http://127.0.0.1:8000/api/v2/health
curl --fail http://127.0.0.1:8000/api/v2/ready
```

## CLI

Discover the exact supported surface from the binary rather than relying on an
old command list:

```bash
uv run ta capabilities --json
uv run ta schema --json
uv run ta --help
```

Implemented V2 operations cover Books, assets, accounts, categories, exact
journal posting, typed credit-card charge/payment/refund/fee, reversal,
classification, FX, investment lots, balances, and journal/reporting queries.
Every financial write requires a stable idempotency key and sends amounts as
decimal strings; the ledger stores integer units at the asset's fixed scale.

Examples:

```bash
uv run ta auth login --agent
uv run ta book list --json
uv run ta book create --help
uv run ta asset list <book_id> --json
uv run ta asset create --help
uv run ta account list <book_id> --type liability --subtype credit_card --json
uv run ta account show <book_id> <account_id> --json
uv run ta account balance <book_id> <account_id> --json
uv run ta account create --help
uv run ta account reopen --help
uv run ta category list <book_id> --json
uv run ta tx record --help
uv run ta tx list --help
uv run ta tx show <book_id> <transaction_id> --json
uv run ta book balances --help
uv run ta card charge --help
uv run ta card payment --help
uv run ta card refund --help
uv run ta card fee --help
```

Credit-card accounts are strict liabilities and are created explicitly:

```bash
uv run ta account create <book_id> <account_id> \
  --asset-code USD --type liability --account-subtype credit_card \
  --name "Primary card" --json
```

Card commands accept only a positive business amount. They choose the canonical
debit/credit legs server-side; callers cannot submit posting sides through this
surface.

Draft capture, recurring rules, payment-profile helpers, attachments,
client-side backup/restore, and broad search are not compatibility-backed V2
features. Unsupported command groups fail locally before making a network
request. See the reviewed
[capability matrix](docs/operations/v2-client-capability-matrix.md) for the exact
implemented, deferred, and removed boundary.

## Verification

The aggregate local gate requires three distinct loopback PostgreSQL identities:

```bash
export TRACK_ANYWHERE_TEST_POSTGRES_ADMIN_URL='postgresql+psycopg://...'
export TRACK_ANYWHERE_TEST_POSTGRES_MIGRATOR_BASE_URL='postgresql+psycopg://...'
export TRACK_ANYWHERE_TEST_POSTGRES_RUNTIME_BASE_URL='postgresql+psycopg://...'
bash scripts/verify-v2.sh
```

The gate covers unit, PostgreSQL constraints, role separation, concurrency,
deterministic replay, contracts, CLI, frontend lint/build, and Alembic head
checks.

For an isolated local container rehearsal, use:

```bash
bash scripts/e2e-docker-postgres.sh
```

Operational details live in:

- [V2 isolated staging checklist](docs/operations/v2-isolated-staging-checklist.md)

## Safety boundary

Repository tests and staging harnesses bind published ports to loopback and use
isolated databases. They do not deploy production, mutate the stable backend,
or change production connection strings. Production cutover requires a new,
explicit authorization after all local evidence is accepted.
