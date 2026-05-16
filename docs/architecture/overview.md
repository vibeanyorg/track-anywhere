# Track Anywhere Architecture

Track Anywhere is a personal accounting system with a strict confirmed ledger and draft-first capture.

The key boundary is the command governance layer. Web, CLI, Agent, and OCR-assisted inputs all become strict versioned command objects before they can mutate domain state. Mutating commands pass through transport/session checks, authentication, actor derivation, schema validation, policy, idempotency, optimistic concurrency, and redacted audit logging.

## First Slice

The current implementation establishes a minimum secure vertical slice:
- strict ledger accounts, transactions, postings, balance derivation
- read APIs for account lookup and transaction inspection
- draft capture and confirmation
- draft rejection and superseding
- fund creation, allocation, spending, and transaction reversal
- command idempotency and stale-version detection
- verified credentials and scoped Agent tokens
- CSRF/session and Origin validation at the API boundary
- explicit CORS allowlist configuration
- fail-closed attachment intake exposed through an authenticated API endpoint
- redacted audit events
- audit records for security, validation, idempotency, and stale-version failures
- environment-driven deployment security readiness checks
- SQLAlchemy ORM persistence with SQLite by default and Postgres-compatible URLs
- CLI command surface
- Next.js operational dashboard shell with a CSRF-aware draft-capture API path
- public API and CLI JSON contract snapshot tests

## Confirmed vs Projected

Official balances are derived only from confirmed postings. Drafts can be included in projected balances only when explicitly requested.

## Account Metadata

Accounts keep accounting type separate from product grouping:
- `type` is the ledger direction, such as `asset`, `liability`, `expense`, `equity`, or `system`.
- `institution_type` groups the provider category, such as `bank`, `e_wallet`, `fintech`, `brokerage`, `cash`, `crypto_wallet`, `system`, or `other`.
- `subtype` is an extensible lowercase slug for product shape, such as `debit_card`, `credit_card`, `ewallet_cash`, `ewallet_money_market`, `multicurrency_wallet`, or `crypto_token`.
- `institution` stores the human provider name, such as `中国银行`, `微信`, `Wise`, `SafePal Wallet01-LM3`, or `track-anywhere`.

Each account remains single-asset. Multi-currency providers such as Wise should be modeled as one account per currency, for example `Wise USD`, `Wise EUR`, and `Wise CNY`, with `institution_type=fintech` and `subtype=multicurrency_wallet`. Crypto wallets should be modeled as one account per token and network, for example `SafePal USDC (Arbitrum)` with `currency=USDC`, `institution_type=crypto_wallet`, and `subtype=crypto_token`.

Account summaries group confirmed balances by `type`, `institution_type`, `subtype`, `institution`, or `currency`. Summaries do not do FX conversion; multi-currency providers are returned as separate currency totals.

Summary rows expose raw `amount` plus `asset_amount`, `liability_amount`, and `net_amount`. Use `asset_amount` for total assets and `net_amount` for net-worth style views; liabilities are stored as positive amounts owed.

## Security Posture

Security is a prerequisite for high-authority Agent/OCR flows, not a final hardening pass. Attachment parsing fails closed outside local development when scanner/parsing hardening is unavailable. Audit and logs redact sensitive payloads by default.

## Persistence

Local development uses SQLite at `.local/track-anywhere.sqlite3` by default. Override storage with `TRACK_ANYWHERE_DATABASE_URL`; for Postgres use a SQLAlchemy URL such as `postgresql+psycopg://user:password@localhost:5432/track_anywhere` and install the `postgres` extra.

The first persistence slice keeps the domain model in memory during a request and writes an ORM snapshot after mutations. It persists accounts, transactions, postings, drafts, funds, attachments, credentials, idempotency receipts, audit events, reconciliation actions, and local owner token state. This keeps the current domain code small while leaving room to replace the snapshot layer with per-aggregate repositories later.
