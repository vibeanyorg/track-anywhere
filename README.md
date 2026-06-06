# Track Anywhere

Track Anywhere is a local-first personal ledger. It keeps official balances strict while allowing drafts, screenshots, and agent input to become entries only after review.

It ships a FastAPI backend, a `ta`/`track-anywhere` CLI usable by humans and agents, SQLite persistence by default, PostgreSQL-compatible storage URLs, and a Next.js frontend.

Status: MVP. The CLI and backend are usable locally. FastAPI owns the login/signup flow and platform OAuth connection surface; the Next.js frontend is optional.

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

### Docker

The registry publishes a split API/CLI image and production web image:

```text
ghcr.io/vibeanyorg/track-anywhere-api:latest
ghcr.io/vibeanyorg/track-anywhere-web:latest
```

Start an isolated local development stack:

```bash
scripts/deploy-local.sh
```

Deploy the production stack to the default VPS alias (`root@cc6`):

```bash
scripts/deploy-vps.sh
```

Development and production use separate Compose project names, container names,
ports, and env files. Agents should read the service address from
`TRACK_ANYWHERE_SERVICE_URL` first, falling back to `TRACK_ANYWHERE_API`.

See [Docker Deployment](docs/operations/docker-deploy.md).

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
uv run ta auth login
uv run ta auth status --json
```

`ta auth login <token>` still accepts a bearer/API token for automation. Without
a token, the CLI opens the web app, completes a PKCE code exchange, and saves the
issued platform token.

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

`account adjust --amount` is a signed natural balance delta. For liabilities and
credit cards, positive increases amount owed and negative decreases debt or
creates overpayment; storage still writes positive debit/credit postings.
`account balance` returns natural balances, not raw posting signs. Always read
`balance_semantics` and `official_balance.amount_semantics` together with
`official_balance.amount`: `asset`, `fund`, `system`, `expense`, `income`,
`equity`, and `liability` each have explicit natural balance semantics. For
liabilities, use `liability_balance.outstanding_amount` and
`liability_balance.overpayment_amount` instead of inferring debt from the sign.

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
For debit/credit posting migration work, follow the
[Posting Semantics Cutover Runbook](docs/operations/posting-semantics-cutover.md).

## Browser, OAuth, and RBAC auth

The API keeps CLI/agent bearer tokens and browser OAuth login separate. Bearer tokens remain the automation path; browser login uses Authlib-backed OAuth/OIDC routes, persistent provider identities, and role-to-scope mapping before entering the existing ledger authorization layer. See [Auth Integration](docs/architecture/auth-integration.md).
Password signup is open only in local mode. In non-local deployments, signup
requires `TRACK_ANYWHERE_PASSWORD_SIGNUP_ALLOWED_EMAILS`; login still works for
existing password accounts.

## Backoffice API

Internal read-only inspection endpoints live under `/api/v1/backoffice/`.
They expose books, memberships, accounts, ledger users, auth identities,
categories, transactions, recurring items, audit events, and role/scope
metadata. These routes require owner/admin-level `user:write` scope and use the
same FastAPI auth, session, CSRF, and ledger authorization layer as the rest of
the API.

## CLI

Discover syntax from the CLI:

```bash
uv run ta --help
uv run ta account --help
uv run ta tx --help
uv run ta summary --help
```

The CLI resolves the service URL from `--base-url`, then
`TRACK_ANYWHERE_API`, then `TRACK_ANYWHERE_SERVICE_URL`, and finally
`http://localhost:8000`.

Read the ledger:

```bash
uv run ta account list --json
uv run ta account find --name <text> --currency CNY --json
uv run ta account show <account_id> --json
uv run ta category list --kind expense --json
uv run ta credit-card list --json
uv run ta tx list --account-id <account_id> --limit 10 --json
uv run ta summary accounts --group-by institution --currency CNY --json
uv run ta summary categories --kind expense --currency CNY --json
```

Write to the ledger:

```bash
uv run ta account create "<name>" --type asset --currency CNY --idempotency-key <key> --json
uv run ta category create --kind expense --primary "<level-1>" --secondary "<level-2>" --idempotency-key <key> --json
uv run ta expense record --amount <amount> --currency CNY --from-account-id <source> --category-id <category_id> --purpose "<why>" --idempotency-key <key> --json
uv run ta expense record --payment <payment_slug> --amount <amount> --currency USD --category-id <category_id> --purpose "<why>" --idempotency-key <key> --json
uv run ta income record --amount <amount> --currency CNY --to-account-id <target> --category-id <category_id> --purpose "<why>" --idempotency-key <key> --json
uv run ta credit-card update <credit_card_account_id> --credit-limit <limit> --statement-day <day> --due-day <day> --idempotency-key <key> --json
uv run ta account adjust <account_id> --amount <signed-natural-delta> --currency CNY --purpose "<why>" --idempotency-key <key> --json
uv run ta tx record --amount <amount> --currency CNY --from-account-id <source> --to-account-id <target> --purpose "<why>" --idempotency-key <key> --json
uv run ta investment event <investment_account_id> --type buy --amount <principal> --occurred-at <iso-date> --idempotency-key <key> --json
uv run ta capture "spent 38 on lunch" --dry-run --json
```

Use positive amounts for both credit-card spending and repayment. A credit-card
purchase should be recorded as `expense record --from-account-id <credit_card>`;
that credits the liability and increases `outstanding_balance`. A credit-card
repayment should be recorded as `tx record --from-account-id <source_asset>
--to-account-id <credit_card>`; that debits the liability and decreases
`outstanding_balance`. Do not use negative amounts or raw posting fields to
force either direction.

Agent workflows: pass `--json`, supply a stable `--idempotency-key`, back up before writes, and re-read affected records after every write.

## Account model

- `type`: ledger direction, such as `asset`, `liability`, `expense`, `equity`, or `system`
- `institution_type`: provider category, such as `bank`, `e_wallet`, `fintech`, `brokerage`, `cash`, `crypto_wallet`, `system`, or `other`
- `subtype`: product shape, such as `debit_card`, `credit_card`, `money_market`, `fund`, `multicurrency_wallet`, or `crypto_token`
- `institution`: human provider name

Liability balances use natural debit/credit semantics: positive means amount
owed, and negative means overpayment or credit balance. Summary rows expose
`asset_amount`, `fund_amount`, `liability_outstanding_amount`,
`liability_overpayment_amount`, and `net_amount`; use `net_amount` for
net-worth-style reporting instead of inferring meaning from a bare `amount`
sign.

### Account API resource split

`/api/v1/accounts` is retained as the legacy catalog-compatible ledger account endpoint. It still exposes the underlying accounting account model and may include `asset`, `liability`, `income`, `expense`, `equity`, `fund`, and `system` records. Existing CLI commands such as `ta account list` continue to call this legacy endpoint.

Use `/api/v1/ledger-accounts` when you explicitly want the double-entry ledger resource. It is a read alias for the same underlying account records and is allowed to return accounting-only and system accounts.

Use `/api/v1/financial-accounts` for user-visible financial accounts: cash, bank accounts/cards, e-wallets, credit cards, brokerage accounts, crypto wallets, funds, and other visible asset/liability locations. This read model is backed by the current `Account.account_id`; the response therefore returns both `account_id` and `ledger_account_id` with the same value, and `ledger_account_type` records the underlying `asset`/`liability`/`fund` type. Its `type` field is the product-facing financial account type such as `cash`, `bank`, `e_wallet`, `credit_card`, `brokerage`, `crypto_wallet`, `fund`, or `other`.

`GET /api/v1/financial-accounts` supports `q`, `type`, `currency`, `institution_type`, `subtype`, `institution`, and `status=active`. By default it excludes `income`, `expense`, `equity`, `system`, opening-equity, adjustment, FX-clearing, and category-clearing accounts. Add `include=balance` to expand confirmed balances in the list without requiring one balance request per account. A single visible account balance is also available at `GET /api/v1/financial-accounts/{account_id}/balance`.

## Credit card profiles

Credit-card liability account balances use the same natural liability semantics:
positive balance means current amount owed, and negative balance means
overpayment. Credit limits and billing metadata live in a separate profile, so
changing a limit never mutates ledger balance.

```bash
uv run ta credit-card update <credit_card_account_id> \
  --credit-limit 10000 \
  --available-credit 9700 \
  --statement-day 8 \
  --due-day 26 \
  --annual-fee 0 \
  --idempotency-key credit-card-profile-example \
  --json

uv run ta credit-card show <credit_card_account_id> --json
uv run ta credit-card list --json
```

The overview returns `natural_balance` with
`natural_balance_semantics = natural_liability_balance`. It also returns
`current_balance` as a compatibility alias plus explicit `outstanding_balance`
and `overpayment_balance`, each with `outstanding_balance_semantics` and
`overpayment_balance_semantics`. Prefer the explicit fields for display and
agent logic. Derived available credit is
`credit_limit - outstanding_balance + overpayment_balance`, so an overpayment
can increase derived available credit above the nominal limit.

## Token-backed payment profiles

Use a payment profile when a user-visible payment instrument is backed by another asset account. SafePal Card USD backed by SafePal USD24 is the first supported shape. Users record the payment once; Track Anywhere writes one confirmed transaction that contains both the USD expense and the immediate USD24 settlement.

Set up the profile:

```bash
uv run ta payment profile create safepal \
  --display-name "SafePal" \
  --kind token-backed-card \
  --instrument-account-id <safepal_card_usd_account_id> \
  --backing-account-id <safepal_usd24_account_id> \
  --settlement-mode immediate \
  --settlement-rate 1 \
  --idempotency-key payment-profile-safepal \
  --json
```

Record daily spending:

```bash
uv run ta expense record \
  --payment safepal \
  --amount 3.40 \
  --currency USD \
  --category-id <category_id> \
  --purpose "Meituan" \
  --idempotency-key safepal-expense-<stable-key> \
  --json
```

Read the composite SafePal view:

```bash
uv run ta payment profile status safepal --json
```

The first version assumes `1 USD = 1 USD24` with no spread, fee, slippage, or rate difference. The raw card clearing account should normally stay at zero after immediate-settlement payments; the status view shows the USD24 backing balance and the effective USD spendable balance.

## Income and expense categories

Categories are explicit user data. Track Anywhere does not seed preset categories; create them one at a time as real transactions need them.

Each category has:

- `kind`: `expense` or `income`
- `primary`: first-level label, such as `餐饮`
- `secondary`: optional second-level label, such as `外卖`

```bash
uv run ta category create \
  --kind expense \
  --primary "餐饮" \
  --secondary "外卖" \
  --idempotency-key category-expense-food-delivery \
  --json

uv run ta expense record \
  --amount 38 \
  --currency CNY \
  --from-account-id <payment_account_id> \
  --category-id <category_id> \
  --purpose "lunch delivery" \
  --idempotency-key expense-lunch-delivery-20260516 \
  --json

uv run ta summary categories --kind expense --currency CNY --json
```

`expense record` and `income record` use internal system clearing accounts, so day-to-day classification is not tied to one expense account per category. Lower-level `ta tx record` still accepts `--category-id` when the from/to accounts already represent an income or expense flow.

## Investment performance

Investment accounts still use normal account balances for current value. Record dated investment events for performance analytics:

- `buy` or `add`: money invested into the position
- `sell`: redemption proceeds
- `income`: cash income or distribution received

```bash
uv run ta investment event <account_id> \
  --type buy \
  --amount 35000 \
  --currency CNY \
  --occurred-at 2026-04-24T00:00:00+08:00 \
  --memo "initial purchase" \
  --idempotency-key investment-buy-example \
  --json

uv run ta investment performance <account_id> \
  --as-of 2026-05-15T00:00:00+08:00 \
  --json
```

Performance reports use the investment events plus the account's current confirmed balance to return holding days, net contributed principal, total return, and money-weighted annualized return.

## Agent usage

The canonical Track Anywhere ledger skill lives in this repository:

```text
skills/track-anywhere-ledger
```

Keep this in-repo skill updated with CLI/API changes so agent guidance and implementation stay in one source of truth.

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
- [Posting Semantics Cutover Runbook](docs/operations/posting-semantics-cutover.md)
- [ADR 0001: Draft-First Capture With Strict Confirmed Ledger](docs/adr/0001-draft-first-strict-ledger.md)
- [ADR 0002: Debit/Credit Posting Model](docs/adr/0002-debit-credit-posting-model.md)
- [Hermes/OpenClaw Agent Guide](docs/agents/hermes-openclaw.md)

## Not yet implemented

- Full import pipeline.
- Full-featured web UI.
- Automatic FX conversion in summaries.

## Operational caveats

- PostgreSQL backup support is not implemented; use `pg_dump` when running against PostgreSQL.
- Defaults assume local-only use. Review auth, CORS, and bind address before exposing the API.
