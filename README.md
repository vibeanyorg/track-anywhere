# Track Anywhere

Track Anywhere is a local-first personal accounting system for people who want a complete financial ledger without making day-to-day capture painful.

The project combines a strict confirmed ledger, draft-first capture, a human- and agent-friendly CLI, a FastAPI backend, and a placeholder Next.js web UI. It is designed for workflows such as:

- tracking bank cards, credit cards, e-wallets, fintech balances, brokerage holdings, and crypto wallets
- recording balance snapshots from screenshots or manual review
- separating assets, liabilities, expenses, and internal system accounts
- letting agents create draft records or update balances through audited CLI/API commands
- keeping real local financial data out of git by default

> Status: early MVP. The CLI and backend are usable for local development; the web UI is intentionally still a placeholder.

## Core Ideas

- **Strict confirmed ledger**: official balances are derived from confirmed postings.
- **Draft-first capture**: OCR, screenshots, and voice-like descriptions can become drafts before they become financial truth.
- **Agent-safe operations**: mutating commands use idempotency keys, audit events, scoped credentials, and explicit verification.
- **Local-first persistence**: SQLite is the default local store; PostgreSQL-compatible URLs are supported through SQLAlchemy.
- **Single-asset accounts**: multi-currency services such as Wise are modeled as one account per currency; crypto wallets are modeled as one account per token/network.

## Repository Layout

```text
backend/   FastAPI app, ledger domain, persistence, tests
cli/       `ta` command-line client
frontend/  Next.js placeholder UI
docs/      Architecture, operations, ADRs, agent guidance
skills/    Codex/Hermes/OpenClaw skill for safe ledger operation
```

## Quick Start

Requirements:

- Python 3.12+
- `uv`
- Node.js if you want to run the placeholder frontend

Install dependencies:

```bash
uv sync --extra dev
```

Start the API:

```bash
uv run uvicorn track_anywhere.api:app --app-dir backend/app --host 127.0.0.1 --port 8000
```

In another shell, issue a local development token and store it for the CLI:

```bash
uv run ta auth dev-token --json
uv run ta auth login <token-from-json>
uv run ta auth status --json
```

Create a user:

```bash
uv run ta user create alice --display-name "Alice" --idempotency-key user-create-alice --json
```

Create an account and set an opening balance:

```bash
uv run ta account create "Example Cash" \
  --type asset \
  --currency CNY \
  --opening-balance 0 \
  --institution-type cash \
  --subtype cash \
  --institution local \
  --idempotency-key account-create-example-cash \
  --json

uv run ta account adjust <account_id> \
  --amount 100 \
  --currency CNY \
  --purpose "Opening cash balance" \
  --idempotency-key balance-update-example-cash-opening \
  --json
```

Check the balance:

```bash
uv run ta account balance <account_id> --json
```

## CLI

Discover the command surface from the CLI itself:

```bash
uv run ta --help
uv run ta account --help
uv run ta tx --help
uv run ta summary --help
```

Useful read commands:

```bash
uv run ta account list --json
uv run ta account find --name <text> --currency CNY --json
uv run ta account show <account_id> --json
uv run ta tx list --account-id <account_id> --limit 10 --json
uv run ta summary accounts --group-by institution --currency CNY --json
```

Useful write commands:

```bash
uv run ta account create "<name>" --type asset --currency CNY --idempotency-key <key> --json
uv run ta account adjust <account_id> --amount <delta> --currency CNY --purpose "<why>" --idempotency-key <key> --json
uv run ta tx record --amount <amount> --currency CNY --from-account-id <source> --to-account-id <target> --purpose "<why>" --idempotency-key <key> --json
uv run ta capture "spent 38 on lunch" --dry-run --json
```

For agent workflows, always prefer `--json`, stable idempotency keys, and post-write verification.

## Data Safety

Local data is real financial data. The default SQLite database lives at:

```text
.local/track-anywhere.sqlite3
```

`.local/`, `.omx/`, `.env*`, SQLite files, and generated artifacts are ignored by git.

Before any mutation against real local data:

```bash
uv run ta data backup --label before-change --json
```

Backups are written to `.local/backups/`, also ignored by git. See [Data Backup](docs/operations/data-backup.md).

## Account Model

Accounts keep ledger direction separate from product grouping:

- `type`: `asset`, `liability`, `expense`, `equity`, or `system`
- `institution_type`: `bank`, `e_wallet`, `fintech`, `brokerage`, `cash`, `crypto_wallet`, `system`, or `other`
- `subtype`: extensible product shape such as `debit_card`, `credit_card`, `money_market`, `fund`, `multicurrency_wallet`, or `crypto_token`
- `institution`: human provider name

Liabilities are stored as positive amounts owed. Summary rows expose `asset_amount`, `liability_amount`, and `net_amount` so reports do not confuse gross totals with net worth.

## Agent Usage

This repo includes a skill for Codex/Hermes/OpenClaw-style agents:

```text
skills/track-anywhere-ledger
```

It teaches agents to use `ta` safely: back up before writes, avoid direct SQLite mutation, use idempotency keys, handle screenshot-derived data conservatively, and verify balances after every change.

Standalone skill repository:

[vibeanyorg/track-anywhere-ledger-skill](https://github.com/vibeanyorg/track-anywhere-ledger-skill)

Install:

```bash
npx skills add vibeanyorg/track-anywhere-ledger-skill --skill track-anywhere-ledger
```

For project-local agent guidance, see [Hermes/OpenClaw Agent Guide](docs/agents/hermes-openclaw.md).

## Frontend

The frontend is a placeholder Next.js app:

```bash
cd frontend
npm install
npm run dev
```

The backend and CLI are the active MVP surface.

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

## Current Limitations

- No complex import pipeline yet.
- No full-featured web UI yet.
- No automatic FX conversion in summaries.
- PostgreSQL backup support is not implemented; use `pg_dump` when running against PostgreSQL.
- Security defaults are designed for local development first. Review deployment settings before exposing the API.
