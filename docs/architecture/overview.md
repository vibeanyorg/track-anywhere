# Track Anywhere Architecture

Track Anywhere is a personal accounting system with a strict confirmed ledger and draft-first capture.

The key boundary is the command governance layer. Web, CLI, Agent, and OCR-assisted inputs all become strict versioned command objects before they can mutate domain state. Mutating commands pass through transport/session checks, authentication, actor derivation, schema validation, policy, idempotency, optimistic concurrency, and redacted audit logging.

The target domain redesign is documented in [Domain Redesign: Books, Classification, and Reporting](domain-redesign.md). That design introduces ledger books as the primary namespace, replaces first/second-level category strings with two managed two-level category trees for income and expense, and separates transaction postings from reporting dimensions such as category, tag, project, merchant, and budget target. It also treats category maintenance as auditable classification events so reports can choose between recorded and current taxonomy.

## First Slice

The current implementation establishes a minimum secure vertical slice:
- strict ledger accounts, transactions, postings, balance derivation
- user-created income and expense categories with first/second-level labels
- credit-card profile metadata separate from liability balances
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

Summary rows expose `amount` plus `asset_amount`, `fund_amount`,
`liability_amount`, `liability_outstanding_amount`,
`liability_overpayment_amount`, and `net_amount`. `amount` is only the sum of
natural account balances in the row; it is not a net-worth field for mixed
asset/liability groups. Use `asset_amount` for ordinary assets, `fund_amount`
for earmarked fund balances, and `net_amount` for net-worth style views.
`liability_amount` is the natural liability net balance;
`liability_outstanding_amount` is the positive amount owed, and
`liability_overpayment_amount` is the positive overpayment amount. Summary
responses include `summary_semantics` and per-row `*_semantics` fields so agents
do not infer liability meaning from a bare sign.

## Security Posture

Security is a prerequisite for high-authority Agent/OCR flows, not a final hardening pass. Attachment parsing fails closed outside local development when scanner/parsing hardening is unavailable. Audit and logs redact sensitive payloads by default.

## Persistence

Local development uses PostgreSQL by default. Start the local database with `docker compose up -d postgres`; the default SQLAlchemy URL is `postgresql+psycopg://track_anywhere:track_anywhere@localhost:55432/track_anywhere`. Override storage with `TRACK_ANYWHERE_DATABASE_URL` for Neon or any other Postgres target. SQLite remains available for focused tests by passing an explicit `sqlite:///...` URL.

Database schema changes are applied through Alembic migrations in `alembic/versions`. Service startup runs `alembic upgrade head` programmatically before repositories load persisted state, and Alembic records the active revision in `alembic_version`.

For schema changes, update the SQLAlchemy models, generate a revision with `uv run alembic revision --autogenerate -m "<change>"`, inspect the generated migration, then verify with `uv run alembic upgrade head` and `uv run alembic check`.

Writes use command-scoped repository transactions. Each mutating use case validates against the storage-backed truth, builds the specific aggregate changes it owns, and commits those changes through `UnitOfWork` repositories together with idempotency receipts and audit events. Startup may run targeted maintenance for legacy owner-token cleanup and default-domain rows, but production API writes must not call full-service snapshot persistence.

Storage write boundaries accept one explicit change-set object per operation, not a service object or scattered keyword arguments. This keeps the persistence contract narrow: adding a field to catalog, ledger, workflow, or profile writes should change the relevant change-set type and writer only, rather than widening unrelated use cases.

High-churn API routers should receive the application service through FastAPI dependency injection instead of importing the runtime singleton directly. That keeps request handlers testable and makes it possible to split the service façade behind narrower use-case providers without rewriting route modules.
