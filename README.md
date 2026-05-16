# Track Anywhere

Track Anywhere is a local-first personal ledger. It keeps official balances strict while allowing drafts, screenshots, and agent input to become entries only after review.

It ships a FastAPI backend, a `ta` CLI usable by humans and agents, SQLite persistence by default, PostgreSQL-compatible storage URLs, and a Next.js frontend stub.

Status: MVP. The CLI and backend are usable locally. The frontend is not yet a full product surface.

## Typical use

- Track bank cards, credit cards, e-wallets, fintech balances, brokerage holdings, and crypto wallets.
- Record balance snapshots from screenshots or manual review.
- Separate assets, liabilities, expenses, and internal system accounts.
- Let agents create drafts or update balances through audited CLI/API commands.
- Keep local financial data out of git by default.

## Core model

- **Confirmed-only balances**: official balances come from confirmed postings; drafts never affect them unless explicitly requested as projections.
- **Draft-first capture**: uncertain input from OCR, screenshots, or agents can stay in review before it becomes ledger truth.
- **Agent-safe writes**: mutations require idempotency keys, emit audit events, and should be verified by follow-up reads.
- **Single-asset accounts**: multi-currency services are modeled as one account per currency; crypto wallets are modeled as one account per token/network.

## Repository layout

```text
backend/   FastAPI app, ledger domain, persistence, tests
cli/       ta command-line client
frontend/  Next.js frontend stub
docs/      Architecture, operations, ADRs, agent guidance
skills/    Codex/Hermes/OpenClaw skill for safe ledger operation
```

## Quick Start

### 1. Install

Requirements:

- Python 3.12+
- `uv`
- Node.js only if you want to run the frontend stub

```bash
uv sync --extra dev
```

### 2. Run the API

```bash
uv run uvicorn track_anywhere.api:app --app-dir backend/app --host 127.0.0.1 --port 8000
```

### 3. Authenticate the CLI

In another shell:

```bash
uv run ta auth dev-token --json
uv run ta auth login <token-from-json>
uv run ta auth status --json
```

### 4. Create a user and account

```bash
uv run ta user create alice --display-name "Alice" --idempotency-key user-create-alice --json

uv run ta account create "Example Cash" \
  --type asset \
  --currency CNY \
  --opening-balance 0 \
  --institution-type cash \
  --subtype cash \
  --institution local \
  --idempotency-key account-create-example-cash \
  --json
```

### 5. Record and verify a balance

```bash
uv run ta account adjust <account_id> \
  --amount 100 \
  --currency CNY \
  --purpose "Opening cash balance" \
  --idempotency-key balance-update-example-cash-opening \
  --json

uv run ta account balance <account_id> --json
```

## Data safety

Treat local data as real financial data. The default SQLite database lives at:

```text
.local/track-anywhere.sqlite3
```

`.local/`, `.omx/`, `.env*`, SQLite files, and generated artifacts are ignored by git.

Back up before every mutation:

```bash
uv run ta data backup --label before-change --json
```

Backups are written to `.local/backups/`, also ignored by git. See [Data Backup](docs/operations/data-backup.md).

## CLI

Discover syntax from the CLI:

```bash
uv run ta --help
uv run ta account --help
uv run ta tx --help
uv run ta summary --help
```

Read the ledger:

```bash
uv run ta account list --json
uv run ta account find --name <text> --currency CNY --json
uv run ta account show <account_id> --json
uv run ta tx list --account-id <account_id> --limit 10 --json
uv run ta summary accounts --group-by institution --currency CNY --json
```

Write to the ledger:

```bash
uv run ta account create "<name>" --type asset --currency CNY --idempotency-key <key> --json
uv run ta account adjust <account_id> --amount <delta> --currency CNY --purpose "<why>" --idempotency-key <key> --json
uv run ta tx record --amount <amount> --currency CNY --from-account-id <source> --to-account-id <target> --purpose "<why>" --idempotency-key <key> --json
uv run ta capture "spent 38 on lunch" --dry-run --json
```

Agent workflows: pass `--json`, supply a stable `--idempotency-key`, back up before writes, and re-read affected records after every write.

## Account model

- `type`: ledger direction, such as `asset`, `liability`, `expense`, `equity`, or `system`
- `institution_type`: provider category, such as `bank`, `e_wallet`, `fintech`, `brokerage`, `cash`, `crypto_wallet`, `system`, or `other`
- `subtype`: product shape, such as `debit_card`, `credit_card`, `money_market`, `fund`, `multicurrency_wallet`, or `crypto_token`
- `institution`: human provider name

Liabilities are stored as positive amounts owed. Summary rows expose `asset_amount`, `liability_amount`, and `net_amount` so reports do not confuse gross totals with net worth.

## Agent usage

Use the in-repo skill when an agent is already working inside this checkout:

```text
skills/track-anywhere-ledger
```

Install the standalone skill package for agents that need a reusable public skill:

```bash
npx skills add vibeanyorg/track-anywhere-ledger-skill --skill track-anywhere-ledger
```

Standalone skill repo: [vibeanyorg/track-anywhere-ledger-skill](https://github.com/vibeanyorg/track-anywhere-ledger-skill)

Project-local agent guide: [Hermes/OpenClaw Agent Guide](docs/agents/hermes-openclaw.md)

## Frontend

Run the frontend stub:

```bash
cd frontend
npm install
npm run dev
```

Use the CLI and backend for the current MVP.

## Development

Run tests:

```bash
uv run pytest -q
```

Run the frontend type check:

```bash
cd frontend
npm run lint
```

The public API surface is covered by snapshot tests under `backend/tests/snapshots/`.

## Documentation

- [Architecture Overview](docs/architecture/overview.md)
- [Data Backup](docs/operations/data-backup.md)
- [ADR 0001: Draft-First Capture With Strict Confirmed Ledger](docs/adr/0001-draft-first-strict-ledger.md)
- [Hermes/OpenClaw Agent Guide](docs/agents/hermes-openclaw.md)

## Not yet implemented

- Full import pipeline.
- Full-featured web UI.
- Automatic FX conversion in summaries.

## Operational caveats

- PostgreSQL backup support is not implemented; use `pg_dump` when running against PostgreSQL.
- Defaults assume local-only use. Review auth, CORS, and bind address before exposing the API.
