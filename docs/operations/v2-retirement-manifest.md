# V2 retirement manifest

This is the exact pre-deletion inventory for Task 33. It covers every tracked
root entry in `backend/app/track_anywhere/`, every tracked root-level
`backend/tests/test_*.py`, the shared test seams named by the retirement plan,
the isolated backfill tool, and every retained auth and CLI Python utility.
There are no wildcard deletion decisions.

Disposition meanings:

- `retain`: reachable from an implemented V2 capability or deliberately
  isolated as migration-only tooling.
- `rewrite`: keep the path but remove its V1 or SQLite behavior before the
  retirement commit.
- `delete`: remove the exact path in Task 33. A missing delete target after
  retirement is expected; a new unlisted root runtime entry is not.

The `backend/tools/backfill_v1/` name describes its source format, not a V1
runtime. It is an offline-only deterministic migration boundary and is the sole
historical-code exclusion allowed by the post-retirement scan.

| Disposition | Path | V2 consumer | Rationale |
| --- | --- | --- | --- |
| retain | `backend/app/track_anywhere/__init__.py` | [V2 API package](../../backend/app/track_anywhere/api/__init__.py) | Side-effect-free package boundary used by every V2 import. |
| delete | `backend/app/track_anywhere/accounting.py` | — | V1 root domain or persistence module; no implemented V2 import consumer. |
| delete | `backend/app/track_anywhere/api_auth_runtime.py` | — | V1 API composition or adapter; V2 composes only backend/app/track_anywhere/api/. |
| delete | `backend/app/track_anywhere/api_browser_sessions.py` | — | V1 API composition or adapter; V2 composes only backend/app/track_anywhere/api/. |
| delete | `backend/app/track_anywhere/api_config.py` | — | V1 API composition or adapter; V2 composes only backend/app/track_anywhere/api/. |
| delete | `backend/app/track_anywhere/api_dependencies.py` | — | V1 API composition or adapter; V2 composes only backend/app/track_anywhere/api/. |
| delete | `backend/app/track_anywhere/api_errors.py` | — | V1 API composition or adapter; V2 composes only backend/app/track_anywhere/api/. |
| delete | `backend/app/track_anywhere/api_ports/` | — | V1 API composition or adapter; V2 composes only backend/app/track_anywhere/api/. |
| delete | `backend/app/track_anywhere/api_routers/` | — | V1 API composition or adapter; V2 composes only backend/app/track_anywhere/api/. |
| delete | `backend/app/track_anywhere/api_routes.py` | — | V1 API composition or adapter; V2 composes only backend/app/track_anywhere/api/. |
| delete | `backend/app/track_anywhere/api_runtime.py` | — | V1 API composition or adapter; V2 composes only backend/app/track_anywhere/api/. |
| delete | `backend/app/track_anywhere/api_serialization.py` | — | V1 API composition or adapter; V2 composes only backend/app/track_anywhere/api/. |
| delete | `backend/app/track_anywhere/api_sessions.py` | — | V1 API composition or adapter; V2 composes only backend/app/track_anywhere/api/. |
| retain | `backend/app/track_anywhere/api/` | [application composition](../../backend/app/track_anywhere/api/app.py) | The public V2-only FastAPI composition and routers. |
| retain | `backend/app/track_anywhere/application/` | [V2 API command routers](../../backend/app/track_anywhere/api/v2/router.py) | V2 commands, unit of work, idempotency, and ledger commit boundary. |
| delete | `backend/app/track_anywhere/assets.py` | — | V1 root domain or persistence module; no implemented V2 import consumer. |
| delete | `backend/app/track_anywhere/attachments.py` | — | Attachments are deferred and have no approved V2 consumer. |
| delete | `backend/app/track_anywhere/audit.py` | — | V1 root domain or persistence module; no implemented V2 import consumer. |
| delete | `backend/app/track_anywhere/auth_identities.py` | — | V1 root domain or persistence module; no implemented V2 import consumer. |
| delete | `backend/app/track_anywhere/auth_oauth.py` | — | V1 root domain or persistence module; no implemented V2 import consumer. |
| retain | `backend/app/track_anywhere/auth/` | [V2 auth router](../../backend/app/track_anywhere/api/v2/auth.py) | Persistent session, OAuth, device, PKCE, and policy support consumed only by V2. |
| retain | `backend/app/track_anywhere/auth/__init__.py` | [V2 auth router](../../backend/app/track_anywhere/api/v2/auth.py) | Explicit V2 auth import; retained with the persistent PostgreSQL auth boundary. |
| retain | `backend/app/track_anywhere/auth/contracts.py` | [V2 auth router](../../backend/app/track_anywhere/api/v2/auth.py) | Explicit V2 auth import; retained with the persistent PostgreSQL auth boundary. |
| retain | `backend/app/track_anywhere/auth/device.py` | [V2 auth router](../../backend/app/track_anywhere/api/v2/auth.py) | Explicit V2 auth import; retained with the persistent PostgreSQL auth boundary. |
| retain | `backend/app/track_anywhere/auth/errors.py` | [V2 auth router](../../backend/app/track_anywhere/api/v2/auth.py) | Explicit V2 auth import; retained with the persistent PostgreSQL auth boundary. |
| retain | `backend/app/track_anywhere/auth/http.py` | [V2 auth router](../../backend/app/track_anywhere/api/v2/auth.py) | Explicit V2 auth import; retained with the persistent PostgreSQL auth boundary. |
| retain | `backend/app/track_anywhere/auth/oauth.py` | [V2 auth router](../../backend/app/track_anywhere/api/v2/auth.py) | Explicit V2 auth import; retained with the persistent PostgreSQL auth boundary. |
| retain | `backend/app/track_anywhere/auth/security.py` | [V2 auth router](../../backend/app/track_anywhere/api/v2/auth.py) | Explicit V2 auth import; retained with the persistent PostgreSQL auth boundary. |
| retain | `backend/app/track_anywhere/auth/sessions.py` | [V2 auth router](../../backend/app/track_anywhere/api/v2/auth.py) | Explicit V2 auth import; retained with the persistent PostgreSQL auth boundary. |
| delete | `backend/app/track_anywhere/balance_semantics.py` | — | V1 root domain or persistence module; no implemented V2 import consumer. |
| delete | `backend/app/track_anywhere/books.py` | — | V1 root domain or persistence module; no implemented V2 import consumer. |
| delete | `backend/app/track_anywhere/budgets.py` | — | V1 root domain or persistence module; no implemented V2 import consumer. |
| delete | `backend/app/track_anywhere/categories.py` | — | V1 root domain or persistence module; no implemented V2 import consumer. |
| delete | `backend/app/track_anywhere/category_commands.py` | — | V1 root domain or persistence module; no implemented V2 import consumer. |
| delete | `backend/app/track_anywhere/category_history.py` | — | V1 root domain or persistence module; no implemented V2 import consumer. |
| delete | `backend/app/track_anywhere/category_models.py` | — | V1 root domain or persistence module; no implemented V2 import consumer. |
| delete | `backend/app/track_anywhere/commands.py` | — | V1 root domain or persistence module; no implemented V2 import consumer. |
| delete | `backend/app/track_anywhere/counterparties.py` | — | V1 root domain or persistence module; no implemented V2 import consumer. |
| delete | `backend/app/track_anywhere/counterparty_storage_models.py` | — | V1 root domain or persistence module; no implemented V2 import consumer. |
| delete | `backend/app/track_anywhere/credential_commands.py` | — | V1 root domain or persistence module; no implemented V2 import consumer. |
| delete | `backend/app/track_anywhere/credit_cards.py` | — | V1 root domain or persistence module; no implemented V2 import consumer. |
| delete | `backend/app/track_anywhere/db_migrations.py` | — | V1 root domain or persistence module; no implemented V2 import consumer. |
| delete | `backend/app/track_anywhere/deployment_security.py` | — | V1 security helper; V2 imports only track_anywhere.auth.security. |
| delete | `backend/app/track_anywhere/domain_commands.py` | — | V1 root domain or persistence module; no implemented V2 import consumer. |
| delete | `backend/app/track_anywhere/domain_storage_loaders.py` | — | V1 root domain or persistence module; no implemented V2 import consumer. |
| delete | `backend/app/track_anywhere/domain_storage_models.py` | — | V1 root domain or persistence module; no implemented V2 import consumer. |
| delete | `backend/app/track_anywhere/domain_storage_writers.py` | — | V1 root domain or persistence module; no implemented V2 import consumer. |
| retain | `backend/app/track_anywhere/domain/` | [V2 application layer](../../backend/app/track_anywhere/application/ledger_committer.py) | Immutable V2 journal, money, reporting, investment, and privacy contracts. |
| delete | `backend/app/track_anywhere/drafts.py` | — | V1 root domain or persistence module; no implemented V2 import consumer. |
| delete | `backend/app/track_anywhere/errors.py` | — | V1 root domain or persistence module; no implemented V2 import consumer. |
| delete | `backend/app/track_anywhere/idempotency.py` | — | V1 ledger or posting compatibility path; replaced by immutable V2 events and explicit commands. |
| retain | `backend/app/track_anywhere/infrastructure/` | [V2 application unit of work](../../backend/app/track_anywhere/application/unit_of_work.py) | PostgreSQL event store, projections, repositories, and migration-facing models. |
| delete | `backend/app/track_anywhere/investments.py` | — | V1 root domain or persistence module; no implemented V2 import consumer. |
| delete | `backend/app/track_anywhere/ledger.py` | — | V1 ledger or posting compatibility path; replaced by immutable V2 events and explicit commands. |
| delete | `backend/app/track_anywhere/oauth_grants.py` | — | V1 root domain or persistence module; no implemented V2 import consumer. |
| retain | `backend/app/track_anywhere/observability/` | [V2 projection worker](../../backend/app/track_anywhere/infrastructure/projections/worker.py) | Redacted metrics and integrity audit boundary for V2. |
| retain | `backend/app/track_anywhere/outbox/` | [outbox concurrency test](../../backend/tests/v2/concurrency/test_outbox_delivery.py) | Lease, retry, and stable delivery identity for V2 outbox messages. |
| delete | `backend/app/track_anywhere/password_auth.py` | — | V1 root domain or persistence module; no implemented V2 import consumer. |
| delete | `backend/app/track_anywhere/payment_instrument_storage_models.py` | — | V1 root domain or persistence module; no implemented V2 import consumer. |
| delete | `backend/app/track_anywhere/payment_instruments.py` | — | V1 root domain or persistence module; no implemented V2 import consumer. |
| delete | `backend/app/track_anywhere/payment_profile_storage_models.py` | — | V1 root domain or persistence module; no implemented V2 import consumer. |
| delete | `backend/app/track_anywhere/payment_profiles.py` | — | V1 root domain or persistence module; no implemented V2 import consumer. |
| delete | `backend/app/track_anywhere/platform_auth_http.py` | — | V1 root domain or persistence module; no implemented V2 import consumer. |
| delete | `backend/app/track_anywhere/platform_auth_metadata.py` | — | V1 root domain or persistence module; no implemented V2 import consumer. |
| delete | `backend/app/track_anywhere/platform_auth_models.py` | — | V1 root domain or persistence module; no implemented V2 import consumer. |
| delete | `backend/app/track_anywhere/platform_auth.py` | — | V1 root domain or persistence module; no implemented V2 import consumer. |
| delete | `backend/app/track_anywhere/posting_semantics_audit.py` | — | V1 ledger or posting compatibility path; replaced by immutable V2 events and explicit commands. |
| delete | `backend/app/track_anywhere/posting_semantics_views.py` | — | V1 ledger or posting compatibility path; replaced by immutable V2 events and explicit commands. |
| delete | `backend/app/track_anywhere/posting_semantics.py` | — | V1 ledger or posting compatibility path; replaced by immutable V2 events and explicit commands. |
| retain | `backend/app/track_anywhere/queries/` | [V2 query router](../../backend/app/track_anywhere/api/v2/queries.py) | Book-scoped journal, balance, and reporting reads with as-of semantics. |
| delete | `backend/app/track_anywhere/recurring.py` | — | V1 root domain or persistence module; no implemented V2 import consumer. |
| delete | `backend/app/track_anywhere/security.py` | — | V1 security helper; V2 imports only track_anywhere.auth.security. |
| retain | `backend/app/track_anywhere/serialization/` | [V2 event store](../../backend/app/track_anywhere/infrastructure/db/event_store.py) | Canonical JSON, event registry, schemas, and upcasters are part of the V2 hash contract. |
| delete | `backend/app/track_anywhere/service_account_commands.py` | — | V1 service layer; no implemented V2 consumer. |
| delete | `backend/app/track_anywhere/service_account_factory.py` | — | V1 service layer; no implemented V2 consumer. |
| delete | `backend/app/track_anywhere/service_account_queries.py` | — | V1 service layer; no implemented V2 consumer. |
| delete | `backend/app/track_anywhere/service_account_summary.py` | — | V1 service layer; no implemented V2 consumer. |
| delete | `backend/app/track_anywhere/service_accounts.py` | — | V1 service layer; no implemented V2 consumer. |
| delete | `backend/app/track_anywhere/service_assets.py` | — | V1 service layer; no implemented V2 consumer. |
| delete | `backend/app/track_anywhere/service_attachments.py` | — | V1 service layer; no implemented V2 consumer. |
| delete | `backend/app/track_anywhere/service_auth.py` | — | V1 service layer; no implemented V2 consumer. |
| delete | `backend/app/track_anywhere/service_backoffice.py` | — | V1 service layer; no implemented V2 consumer. |
| delete | `backend/app/track_anywhere/service_balance_adjustments.py` | — | V1 service layer; no implemented V2 consumer. |
| delete | `backend/app/track_anywhere/service_balance_queries.py` | — | V1 service layer; no implemented V2 consumer. |
| delete | `backend/app/track_anywhere/service_balance_system_accounts.py` | — | V1 service layer; no implemented V2 consumer. |
| delete | `backend/app/track_anywhere/service_balance_views.py` | — | V1 service layer; no implemented V2 consumer. |
| delete | `backend/app/track_anywhere/service_balances.py` | — | V1 service layer; no implemented V2 consumer. |
| delete | `backend/app/track_anywhere/service_book_accounts.py` | — | V1 service layer; no implemented V2 consumer. |
| delete | `backend/app/track_anywhere/service_book_budgets.py` | — | V1 service layer; no implemented V2 consumer. |
| delete | `backend/app/track_anywhere/service_book_categories.py` | — | V1 service layer; no implemented V2 consumer. |
| delete | `backend/app/track_anywhere/service_book_core.py` | — | V1 service layer; no implemented V2 consumer. |
| delete | `backend/app/track_anywhere/service_book_ledger.py` | — | V1 service layer; no implemented V2 consumer. |
| delete | `backend/app/track_anywhere/service_books.py` | — | V1 service layer; no implemented V2 consumer. |
| delete | `backend/app/track_anywhere/service_catalog.py` | — | V1 service layer; no implemented V2 consumer. |
| delete | `backend/app/track_anywhere/service_categories.py` | — | V1 service layer; no implemented V2 consumer. |
| delete | `backend/app/track_anywhere/service_category_commands.py` | — | V1 service layer; no implemented V2 consumer. |
| delete | `backend/app/track_anywhere/service_category_lines.py` | — | V1 service layer; no implemented V2 consumer. |
| delete | `backend/app/track_anywhere/service_category_queries.py` | — | V1 service layer; no implemented V2 consumer. |
| delete | `backend/app/track_anywhere/service_category_reporting.py` | — | V1 service layer; no implemented V2 consumer. |
| delete | `backend/app/track_anywhere/service_counterparties.py` | — | V1 service layer; no implemented V2 consumer. |
| delete | `backend/app/track_anywhere/service_credential_audit.py` | — | V1 service layer; no implemented V2 consumer. |
| delete | `backend/app/track_anywhere/service_credential_issuance.py` | — | V1 service layer; no implemented V2 consumer. |
| delete | `backend/app/track_anywhere/service_credential_queries.py` | — | V1 service layer; no implemented V2 consumer. |
| delete | `backend/app/track_anywhere/service_credential_revocation.py` | — | V1 service layer; no implemented V2 consumer. |
| delete | `backend/app/track_anywhere/service_credential_utils.py` | — | V1 service layer; no implemented V2 consumer. |
| delete | `backend/app/track_anywhere/service_credentials.py` | — | V1 service layer; no implemented V2 consumer. |
| delete | `backend/app/track_anywhere/service_credit_cards.py` | — | V1 service layer; no implemented V2 consumer. |
| delete | `backend/app/track_anywhere/service_draft_capture.py` | — | V1 service layer; no implemented V2 consumer. |
| delete | `backend/app/track_anywhere/service_draft_confirmation.py` | — | V1 service layer; no implemented V2 consumer. |
| delete | `backend/app/track_anywhere/service_draft_lifecycle.py` | — | V1 service layer; no implemented V2 consumer. |
| delete | `backend/app/track_anywhere/service_draft_store.py` | — | V1 service layer; no implemented V2 consumer. |
| delete | `backend/app/track_anywhere/service_drafts.py` | — | V1 service layer; no implemented V2 consumer. |
| delete | `backend/app/track_anywhere/service_finance.py` | — | V1 service layer; no implemented V2 consumer. |
| delete | `backend/app/track_anywhere/service_foundations.py` | — | V1 service layer; no implemented V2 consumer. |
| delete | `backend/app/track_anywhere/service_fund_catalog.py` | — | V1 service layer; no implemented V2 consumer. |
| delete | `backend/app/track_anywhere/service_fund_flows.py` | — | V1 service layer; no implemented V2 consumer. |
| delete | `backend/app/track_anywhere/service_funds.py` | — | V1 service layer; no implemented V2 consumer. |
| delete | `backend/app/track_anywhere/service_fx.py` | — | V1 service layer; no implemented V2 consumer. |
| delete | `backend/app/track_anywhere/service_idempotency.py` | — | V1 service layer; no implemented V2 consumer. |
| delete | `backend/app/track_anywhere/service_identity.py` | — | V1 service layer; no implemented V2 consumer. |
| delete | `backend/app/track_anywhere/service_investment_events.py` | — | V1 service layer; no implemented V2 consumer. |
| delete | `backend/app/track_anywhere/service_investment_performance.py` | — | V1 service layer; no implemented V2 consumer. |
| delete | `backend/app/track_anywhere/service_investment_valuations.py` | — | V1 service layer; no implemented V2 consumer. |
| delete | `backend/app/track_anywhere/service_investments.py` | — | V1 service layer; no implemented V2 consumer. |
| delete | `backend/app/track_anywhere/service_ledger_queries.py` | — | V1 service layer; no implemented V2 consumer. |
| delete | `backend/app/track_anywhere/service_ledger_records.py` | — | V1 service layer; no implemented V2 consumer. |
| delete | `backend/app/track_anywhere/service_ledger_reversals.py` | — | V1 service layer; no implemented V2 consumer. |
| delete | `backend/app/track_anywhere/service_ledger_transfers.py` | — | V1 service layer; no implemented V2 consumer. |
| delete | `backend/app/track_anywhere/service_ledger.py` | — | V1 service layer; no implemented V2 consumer. |
| delete | `backend/app/track_anywhere/service_owner_bootstrap.py` | — | V1 service layer; no implemented V2 consumer. |
| delete | `backend/app/track_anywhere/service_password_auth.py` | — | V1 service layer; no implemented V2 consumer. |
| delete | `backend/app/track_anywhere/service_payment_instruments.py` | — | V1 service layer; no implemented V2 consumer. |
| delete | `backend/app/track_anywhere/service_payment_profile_expenses.py` | — | V1 service layer; no implemented V2 consumer. |
| delete | `backend/app/track_anywhere/service_payment_profile_lifecycle.py` | — | V1 service layer; no implemented V2 consumer. |
| delete | `backend/app/track_anywhere/service_payment_profiles.py` | — | V1 service layer; no implemented V2 consumer. |
| delete | `backend/app/track_anywhere/service_persistence/` | — | V1 service layer; no implemented V2 consumer. |
| delete | `backend/app/track_anywhere/service_platform_auth.py` | — | V1 service layer; no implemented V2 consumer. |
| delete | `backend/app/track_anywhere/service_reclassification.py` | — | V1 service layer; no implemented V2 consumer. |
| delete | `backend/app/track_anywhere/service_reconciliation.py` | — | V1 service layer; no implemented V2 consumer. |
| delete | `backend/app/track_anywhere/service_recurring_drafts.py` | — | V1 service layer; no implemented V2 consumer. |
| delete | `backend/app/track_anywhere/service_recurring_item_commands.py` | — | V1 service layer; no implemented V2 consumer. |
| delete | `backend/app/track_anywhere/service_recurring_item_queries.py` | — | V1 service layer; no implemented V2 consumer. |
| delete | `backend/app/track_anywhere/service_recurring_item_validation.py` | — | V1 service layer; no implemented V2 consumer. |
| delete | `backend/app/track_anywhere/service_recurring_items.py` | — | V1 service layer; no implemented V2 consumer. |
| delete | `backend/app/track_anywhere/service_recurring_reminders.py` | — | V1 service layer; no implemented V2 consumer. |
| delete | `backend/app/track_anywhere/service_recurring.py` | — | V1 service layer; no implemented V2 consumer. |
| delete | `backend/app/track_anywhere/service_reports.py` | — | V1 service layer; no implemented V2 consumer. |
| delete | `backend/app/track_anywhere/service_state_hydration.py` | — | V1 service layer; no implemented V2 consumer. |
| delete | `backend/app/track_anywhere/service_system.py` | — | V1 service layer; no implemented V2 consumer. |
| delete | `backend/app/track_anywhere/service_users.py` | — | V1 service layer; no implemented V2 consumer. |
| delete | `backend/app/track_anywhere/service.py` | — | V1 service layer; no implemented V2 consumer. |
| delete | `backend/app/track_anywhere/storage_annotation_writers.py` | — | V1 storage and SQLite persistence path; replaced by V2 PostgreSQL infrastructure. |
| delete | `backend/app/track_anywhere/storage_audit_idempotency_writers.py` | — | V1 storage and SQLite persistence path; replaced by V2 PostgreSQL infrastructure. |
| delete | `backend/app/track_anywhere/storage_auth_models.py` | — | V1 storage and SQLite persistence path; replaced by V2 PostgreSQL infrastructure. |
| delete | `backend/app/track_anywhere/storage_auth.py` | — | V1 storage and SQLite persistence path; replaced by V2 PostgreSQL infrastructure. |
| delete | `backend/app/track_anywhere/storage_backoffice_reads.py` | — | V1 storage and SQLite persistence path; replaced by V2 PostgreSQL infrastructure. |
| delete | `backend/app/track_anywhere/storage_catalog_reads.py` | — | V1 storage and SQLite persistence path; replaced by V2 PostgreSQL infrastructure. |
| delete | `backend/app/track_anywhere/storage_change_writers/` | — | V1 storage and SQLite persistence path; replaced by V2 PostgreSQL infrastructure. |
| delete | `backend/app/track_anywhere/storage_changes.py` | — | V1 storage and SQLite persistence path; replaced by V2 PostgreSQL infrastructure. |
| delete | `backend/app/track_anywhere/storage_counterparties.py` | — | V1 storage and SQLite persistence path; replaced by V2 PostgreSQL infrastructure. |
| delete | `backend/app/track_anywhere/storage_draft_reads.py` | — | V1 storage and SQLite persistence path; replaced by V2 PostgreSQL infrastructure. |
| delete | `backend/app/track_anywhere/storage_engine.py` | — | V1 storage and SQLite persistence path; replaced by V2 PostgreSQL infrastructure. |
| delete | `backend/app/track_anywhere/storage_json.py` | — | V1 storage and SQLite persistence path; replaced by V2 PostgreSQL infrastructure. |
| delete | `backend/app/track_anywhere/storage_ledger_reads.py` | — | V1 storage and SQLite persistence path; replaced by V2 PostgreSQL infrastructure. |
| delete | `backend/app/track_anywhere/storage_loaders.py` | — | V1 storage and SQLite persistence path; replaced by V2 PostgreSQL infrastructure. |
| delete | `backend/app/track_anywhere/storage_models.py` | — | V1 storage and SQLite persistence path; replaced by V2 PostgreSQL infrastructure. |
| delete | `backend/app/track_anywhere/storage_partial.py` | — | V1 storage and SQLite persistence path; replaced by V2 PostgreSQL infrastructure. |
| delete | `backend/app/track_anywhere/storage_password_accounts.py` | — | V1 storage and SQLite persistence path; replaced by V2 PostgreSQL infrastructure. |
| delete | `backend/app/track_anywhere/storage_payment_instruments.py` | — | V1 storage and SQLite persistence path; replaced by V2 PostgreSQL infrastructure. |
| delete | `backend/app/track_anywhere/storage_payment_profiles.py` | — | V1 storage and SQLite persistence path; replaced by V2 PostgreSQL infrastructure. |
| delete | `backend/app/track_anywhere/storage_posting_integrity.py` | — | V1 storage and SQLite persistence path; replaced by V2 PostgreSQL infrastructure. |
| delete | `backend/app/track_anywhere/storage_read_cache.py` | — | V1 storage and SQLite persistence path; replaced by V2 PostgreSQL infrastructure. |
| delete | `backend/app/track_anywhere/storage_redaction.py` | — | V1 storage and SQLite persistence path; replaced by V2 PostgreSQL infrastructure. |
| delete | `backend/app/track_anywhere/storage_repositories/` | — | V1 storage and SQLite persistence path; replaced by V2 PostgreSQL infrastructure. |
| delete | `backend/app/track_anywhere/storage_snapshot_loader.py` | — | V1 storage and SQLite persistence path; replaced by V2 PostgreSQL infrastructure. |
| delete | `backend/app/track_anywhere/storage_snapshot.py` | — | V1 storage and SQLite persistence path; replaced by V2 PostgreSQL infrastructure. |
| delete | `backend/app/track_anywhere/storage_system.py` | — | V1 storage and SQLite persistence path; replaced by V2 PostgreSQL infrastructure. |
| delete | `backend/app/track_anywhere/storage_uow.py` | — | V1 storage and SQLite persistence path; replaced by V2 PostgreSQL infrastructure. |
| delete | `backend/app/track_anywhere/storage_upsert_writers.py` | — | V1 storage and SQLite persistence path; replaced by V2 PostgreSQL infrastructure. |
| delete | `backend/app/track_anywhere/storage_writers.py` | — | V1 storage and SQLite persistence path; replaced by V2 PostgreSQL infrastructure. |
| delete | `backend/app/track_anywhere/storage.py` | — | V1 storage and SQLite persistence path; replaced by V2 PostgreSQL infrastructure. |
| delete | `backend/app/track_anywhere/transaction_builder.py` | — | V1 ledger or posting compatibility path; replaced by immutable V2 events and explicit commands. |
| delete | `backend/app/track_anywhere/users.py` | — | V1 root domain or persistence module; no implemented V2 import consumer. |
| rewrite | `backend/tests/conftest.py` | [V2 PG17 fixtures](../../backend/tests/v2/conftest.py) | Remove the remaining SQLite fallback and retain only shared V2-safe test configuration. |
| delete | `backend/tests/schema_assertions.py` | — | Legacy schema assertion helper used only by root-level V1 tests. |
| retain | `backend/tests/snapshots/public-api-v2.json` | [V2 OpenAPI snapshot test](../../backend/tests/v2/contract/test_public_api_v2_snapshot.py) | Frozen public V2 API evidence. |
| delete | `backend/tests/test_account_write_architecture.py` | — | Root-level V1 test; V2 coverage lives under backend/tests/v2/. |
| delete | `backend/tests/test_accounting_debit_credit.py` | — | Root-level V1 test; V2 coverage lives under backend/tests/v2/. |
| delete | `backend/tests/test_alembic_revision_ids.py` | — | Root-level V1 test; V2 coverage lives under backend/tests/v2/. |
| delete | `backend/tests/test_api_account_resources.py` | — | Root-level V1 test; V2 coverage lives under backend/tests/v2/. |
| delete | `backend/tests/test_api_backoffice.py` | — | Root-level V1 test; V2 coverage lives under backend/tests/v2/. |
| delete | `backend/tests/test_api_catalog.py` | — | Root-level V1 test; V2 coverage lives under backend/tests/v2/. |
| delete | `backend/tests/test_api_config.py` | — | Root-level V1 test; V2 coverage lives under backend/tests/v2/. |
| delete | `backend/tests/test_api_finance.py` | — | Root-level V1 test; V2 coverage lives under backend/tests/v2/. |
| delete | `backend/tests/test_api_recurring.py` | — | Root-level V1 test; V2 coverage lives under backend/tests/v2/. |
| delete | `backend/tests/test_api_security.py` | — | Root-level V1 test; V2 coverage lives under backend/tests/v2/. |
| delete | `backend/tests/test_api.py` | — | Root-level V1 test; V2 coverage lives under backend/tests/v2/. |
| delete | `backend/tests/test_asset_read_source_of_truth.py` | — | Root-level V1 test; V2 coverage lives under backend/tests/v2/. |
| delete | `backend/tests/test_auth_device_router_boundaries.py` | — | Root-level V1 test; V2 coverage lives under backend/tests/v2/. |
| delete | `backend/tests/test_auth_machine_pages.py` | — | Root-level V1 test; V2 coverage lives under backend/tests/v2/. |
| delete | `backend/tests/test_auth_machine_router_boundaries.py` | — | Root-level V1 test; V2 coverage lives under backend/tests/v2/. |
| delete | `backend/tests/test_auth_oauth.py` | — | Root-level V1 test; V2 coverage lives under backend/tests/v2/. |
| delete | `backend/tests/test_auth_pages_router_boundaries.py` | — | Root-level V1 test; V2 coverage lives under backend/tests/v2/. |
| delete | `backend/tests/test_auth_pages.py` | — | Root-level V1 test; V2 coverage lives under backend/tests/v2/. |
| delete | `backend/tests/test_auth_password.py` | — | Root-level V1 test; V2 coverage lives under backend/tests/v2/. |
| delete | `backend/tests/test_auth_rbac.py` | — | Root-level V1 test; V2 coverage lives under backend/tests/v2/. |
| delete | `backend/tests/test_auth_router_boundaries.py` | — | Root-level V1 test; V2 coverage lives under backend/tests/v2/. |
| delete | `backend/tests/test_backoffice_source_of_truth.py` | — | Root-level V1 test; V2 coverage lives under backend/tests/v2/. |
| delete | `backend/tests/test_balance_semantics.py` | — | Root-level V1 test; V2 coverage lives under backend/tests/v2/. |
| delete | `backend/tests/test_book_write_source_of_truth.py` | — | Root-level V1 test; V2 coverage lives under backend/tests/v2/. |
| delete | `backend/tests/test_budget_counterparty_targets.py` | — | Root-level V1 test; V2 coverage lives under backend/tests/v2/. |
| delete | `backend/tests/test_budget_target_migration.py` | — | Root-level V1 test; V2 coverage lives under backend/tests/v2/. |
| delete | `backend/tests/test_catalog_write_source_of_truth.py` | — | Root-level V1 test; V2 coverage lives under backend/tests/v2/. |
| delete | `backend/tests/test_categories.py` | — | Root-level V1 test; V2 coverage lives under backend/tests/v2/. |
| delete | `backend/tests/test_category_line_posting_semantics.py` | — | Root-level V1 test; V2 coverage lives under backend/tests/v2/. |
| delete | `backend/tests/test_contracts.py` | — | Root-level V1 test; V2 coverage lives under backend/tests/v2/. |
| delete | `backend/tests/test_counterparty_api.py` | — | Root-level V1 test; V2 coverage lives under backend/tests/v2/. |
| delete | `backend/tests/test_counterparty_migration.py` | — | Root-level V1 test; V2 coverage lives under backend/tests/v2/. |
| delete | `backend/tests/test_counterparty_repository_boundaries.py` | — | Root-level V1 test; V2 coverage lives under backend/tests/v2/. |
| delete | `backend/tests/test_credential_persistence.py` | — | Root-level V1 test; V2 coverage lives under backend/tests/v2/. |
| delete | `backend/tests/test_credit_cards.py` | — | Root-level V1 test; V2 coverage lives under backend/tests/v2/. |
| delete | `backend/tests/test_debit_credit_command_boundaries.py` | — | Root-level V1 test; V2 coverage lives under backend/tests/v2/. |
| delete | `backend/tests/test_domain_redesign.py` | — | Root-level V1 test; V2 coverage lives under backend/tests/v2/. |
| delete | `backend/tests/test_draft_debit_credit_projection.py` | — | Root-level V1 test; V2 coverage lives under backend/tests/v2/. |
| delete | `backend/tests/test_e2e_scripts.py` | — | Root-level V1 test; V2 coverage lives under backend/tests/v2/. |
| delete | `backend/tests/test_financial_hardening.py` | — | Root-level V1 test; V2 coverage lives under backend/tests/v2/. |
| delete | `backend/tests/test_fund_write_source_of_truth.py` | — | Root-level V1 test; V2 coverage lives under backend/tests/v2/. |
| delete | `backend/tests/test_investment_read_source_of_truth.py` | — | Root-level V1 test; V2 coverage lives under backend/tests/v2/. |
| delete | `backend/tests/test_investment_write_source_of_truth.py` | — | Root-level V1 test; V2 coverage lives under backend/tests/v2/. |
| delete | `backend/tests/test_investments.py` | — | Root-level V1 test; V2 coverage lives under backend/tests/v2/. |
| delete | `backend/tests/test_ledger_and_drafts.py` | — | Root-level V1 test; V2 coverage lives under backend/tests/v2/. |
| delete | `backend/tests/test_ledger_source_of_truth.py` | — | Root-level V1 test; V2 coverage lives under backend/tests/v2/. |
| delete | `backend/tests/test_major_bug_regressions.py` | — | Root-level V1 test; V2 coverage lives under backend/tests/v2/. |
| delete | `backend/tests/test_migrations.py` | — | Root-level V1 test; V2 coverage lives under backend/tests/v2/. |
| delete | `backend/tests/test_oauth_router_boundaries.py` | — | Root-level V1 test; V2 coverage lives under backend/tests/v2/. |
| delete | `backend/tests/test_password_auth_boundaries.py` | — | Root-level V1 test; V2 coverage lives under backend/tests/v2/. |
| delete | `backend/tests/test_payment_instrument_api.py` | — | Root-level V1 test; V2 coverage lives under backend/tests/v2/. |
| delete | `backend/tests/test_payment_instrument_repository_boundaries.py` | — | Root-level V1 test; V2 coverage lives under backend/tests/v2/. |
| delete | `backend/tests/test_payment_instruments.py` | — | Root-level V1 test; V2 coverage lives under backend/tests/v2/. |
| delete | `backend/tests/test_payment_profile_api.py` | — | Root-level V1 test; V2 coverage lives under backend/tests/v2/. |
| delete | `backend/tests/test_payment_profile_expenses.py` | — | Root-level V1 test; V2 coverage lives under backend/tests/v2/. |
| delete | `backend/tests/test_payment_profile_repository_boundaries.py` | — | Root-level V1 test; V2 coverage lives under backend/tests/v2/. |
| delete | `backend/tests/test_payment_profiles.py` | — | Root-level V1 test; V2 coverage lives under backend/tests/v2/. |
| delete | `backend/tests/test_persistence_hardening.py` | — | Root-level V1 test; V2 coverage lives under backend/tests/v2/. |
| delete | `backend/tests/test_persistence.py` | — | Root-level V1 test; V2 coverage lives under backend/tests/v2/. |
| delete | `backend/tests/test_platform_auth_api.py` | — | Root-level V1 test; V2 coverage lives under backend/tests/v2/. |
| delete | `backend/tests/test_posting_semantics_api.py` | — | Root-level V1 test; V2 coverage lives under backend/tests/v2/. |
| delete | `backend/tests/test_posting_semantics_audit.py` | — | Root-level V1 test; V2 coverage lives under backend/tests/v2/. |
| delete | `backend/tests/test_posting_semantics_views.py` | — | Root-level V1 test; V2 coverage lives under backend/tests/v2/. |
| delete | `backend/tests/test_posting_storage_constraints.py` | — | Root-level V1 test; V2 coverage lives under backend/tests/v2/. |
| delete | `backend/tests/test_reclassification_persistence.py` | — | Root-level V1 test; V2 coverage lives under backend/tests/v2/. |
| delete | `backend/tests/test_recurring_repository_boundaries.py` | — | Root-level V1 test; V2 coverage lives under backend/tests/v2/. |
| delete | `backend/tests/test_recurring_update.py` | — | Root-level V1 test; V2 coverage lives under backend/tests/v2/. |
| delete | `backend/tests/test_recurring.py` | — | Root-level V1 test; V2 coverage lives under backend/tests/v2/. |
| delete | `backend/tests/test_report_read_source_of_truth.py` | — | Root-level V1 test; V2 coverage lives under backend/tests/v2/. |
| delete | `backend/tests/test_security_foundation.py` | — | Root-level V1 test; V2 coverage lives under backend/tests/v2/. |
| delete | `backend/tests/test_service_account_boundaries.py` | — | Root-level V1 test; V2 coverage lives under backend/tests/v2/. |
| delete | `backend/tests/test_service_asset_boundaries.py` | — | Root-level V1 test; V2 coverage lives under backend/tests/v2/. |
| delete | `backend/tests/test_service_balance_boundaries.py` | — | Root-level V1 test; V2 coverage lives under backend/tests/v2/. |
| delete | `backend/tests/test_service_book_boundaries.py` | — | Root-level V1 test; V2 coverage lives under backend/tests/v2/. |
| delete | `backend/tests/test_service_category_boundaries.py` | — | Root-level V1 test; V2 coverage lives under backend/tests/v2/. |
| delete | `backend/tests/test_service_composition.py` | — | Root-level V1 test; V2 coverage lives under backend/tests/v2/. |
| delete | `backend/tests/test_service_credential_boundaries.py` | — | Root-level V1 test; V2 coverage lives under backend/tests/v2/. |
| delete | `backend/tests/test_service_credit_card_boundaries.py` | — | Root-level V1 test; V2 coverage lives under backend/tests/v2/. |
| delete | `backend/tests/test_service_draft_boundaries.py` | — | Root-level V1 test; V2 coverage lives under backend/tests/v2/. |
| delete | `backend/tests/test_service_finance_boundaries.py` | — | Root-level V1 test; V2 coverage lives under backend/tests/v2/. |
| delete | `backend/tests/test_service_fund_boundaries.py` | — | Root-level V1 test; V2 coverage lives under backend/tests/v2/. |
| delete | `backend/tests/test_service_fx_boundaries.py` | — | Root-level V1 test; V2 coverage lives under backend/tests/v2/. |
| delete | `backend/tests/test_service_investment_boundaries.py` | — | Root-level V1 test; V2 coverage lives under backend/tests/v2/. |
| delete | `backend/tests/test_service_ledger_boundaries.py` | — | Root-level V1 test; V2 coverage lives under backend/tests/v2/. |
| delete | `backend/tests/test_service_metadata_boundaries.py` | — | Root-level V1 test; V2 coverage lives under backend/tests/v2/. |
| delete | `backend/tests/test_service_payment_profile_boundaries.py` | — | Root-level V1 test; V2 coverage lives under backend/tests/v2/. |
| delete | `backend/tests/test_service_reclassification_boundaries.py` | — | Root-level V1 test; V2 coverage lives under backend/tests/v2/. |
| delete | `backend/tests/test_service_recurring_boundaries.py` | — | Root-level V1 test; V2 coverage lives under backend/tests/v2/. |
| delete | `backend/tests/test_service_recurring_item_boundaries.py` | — | Root-level V1 test; V2 coverage lives under backend/tests/v2/. |
| delete | `backend/tests/test_service_report_boundaries.py` | — | Root-level V1 test; V2 coverage lives under backend/tests/v2/. |
| delete | `backend/tests/test_service_storage_decoupling.py` | — | Root-level V1 test; V2 coverage lives under backend/tests/v2/. |
| delete | `backend/tests/test_snapshot_read_source_of_truth.py` | — | Root-level V1 test; V2 coverage lives under backend/tests/v2/. |
| delete | `backend/tests/test_stable_ops_api.py` | — | Root-level V1 test; V2 coverage lives under backend/tests/v2/. |
| delete | `backend/tests/test_storage_change_writer_signatures.py` | — | Root-level V1 test; V2 coverage lives under backend/tests/v2/. |
| delete | `backend/tests/test_storage_repository_boundaries.py` | — | Root-level V1 test; V2 coverage lives under backend/tests/v2/. |
| delete | `backend/tests/test_storage_writer_boundaries.py` | — | Root-level V1 test; V2 coverage lives under backend/tests/v2/. |
| delete | `backend/tests/test_structure.py` | — | Root-level V1 test; V2 coverage lives under backend/tests/v2/. |
| delete | `backend/tests/test_transaction_write_source_of_truth.py` | — | Root-level V1 test; V2 coverage lives under backend/tests/v2/. |
| delete | `backend/tests/test_workflow_source_of_truth.py` | — | Root-level V1 test; V2 coverage lives under backend/tests/v2/. |
| delete | `backend/tests/test_write_architecture.py` | — | Root-level V1 test; V2 coverage lives under backend/tests/v2/. |
| retain | `backend/tests/v2/` | [aggregate V2 gate](../../scripts/verify-v2.sh) | All V2 unit, PostgreSQL, concurrency, replay, backfill, and contract tests. |
| retain | `backend/tools/backfill_v1/` | [V2 backfill tests](../../backend/tests/v2/backfill/test_manifest.py) | Isolated deterministic extractor and loader for the fixed legacy snapshot; never imported by runtime. |
| retain | `cli/tests/` | [aggregate V2 gate](../../scripts/verify-v2.sh) | V2-only CLI contract and transport coverage. |
| retain | `cli/track_anywhere_cli/__init__.py` | [V2 CLI composition](../../cli/track_anywhere_cli/click_app.py) | Task 23 V2-only CLI utility; unsupported V1 capabilities fail before HTTP. |
| retain | `cli/track_anywhere_cli/browser_login.py` | [V2 CLI composition](../../cli/track_anywhere_cli/click_app.py) | Task 23 V2-only CLI utility; unsupported V1 capabilities fail before HTTP. |
| retain | `cli/track_anywhere_cli/click_app.py` | [CLI entry point](../../cli/track_anywhere_cli/main.py) | Task 23 V2-only CLI utility; unsupported V1 capabilities fail before HTTP. |
| retain | `cli/track_anywhere_cli/click_auth.py` | [V2 CLI composition](../../cli/track_anywhere_cli/click_app.py) | Task 23 V2-only CLI utility; unsupported V1 capabilities fail before HTTP. |
| retain | `cli/track_anywhere_cli/click_catalog.py` | [V2 CLI composition](../../cli/track_anywhere_cli/click_app.py) | Task 23 V2-only CLI utility; unsupported V1 capabilities fail before HTTP. |
| retain | `cli/track_anywhere_cli/click_common.py` | [V2 CLI composition](../../cli/track_anywhere_cli/click_app.py) | Task 23 V2-only CLI utility; unsupported V1 capabilities fail before HTTP. |
| retain | `cli/track_anywhere_cli/click_investment.py` | [V2 CLI composition](../../cli/track_anywhere_cli/click_app.py) | Task 23 V2-only CLI utility; unsupported V1 capabilities fail before HTTP. |
| retain | `cli/track_anywhere_cli/click_ledger.py` | [V2 CLI composition](../../cli/track_anywhere_cli/click_app.py) | Task 23 V2-only CLI utility; unsupported V1 capabilities fail before HTTP. |
| retain | `cli/track_anywhere_cli/click_payment.py` | [V2 CLI composition](../../cli/track_anywhere_cli/click_app.py) | Task 23 V2-only CLI utility; unsupported V1 capabilities fail before HTTP. |
| retain | `cli/track_anywhere_cli/click_recurring.py` | [V2 CLI composition](../../cli/track_anywhere_cli/click_app.py) | Task 23 V2-only CLI utility; unsupported V1 capabilities fail before HTTP. |
| retain | `cli/track_anywhere_cli/click_system.py` | [V2 CLI composition](../../cli/track_anywhere_cli/click_app.py) | Task 23 V2-only CLI utility; unsupported V1 capabilities fail before HTTP. |
| retain | `cli/track_anywhere_cli/command_catalog.py` | [V2 CLI composition](../../cli/track_anywhere_cli/click_app.py) | Task 23 V2-only CLI utility; unsupported V1 capabilities fail before HTTP. |
| retain | `cli/track_anywhere_cli/command_investment.py` | [V2 CLI composition](../../cli/track_anywhere_cli/click_app.py) | Task 23 V2-only CLI utility; unsupported V1 capabilities fail before HTTP. |
| retain | `cli/track_anywhere_cli/command_ledger.py` | [V2 CLI composition](../../cli/track_anywhere_cli/click_app.py) | Task 23 V2-only CLI utility; unsupported V1 capabilities fail before HTTP. |
| retain | `cli/track_anywhere_cli/command_payment.py` | [V2 CLI composition](../../cli/track_anywhere_cli/click_app.py) | Task 23 V2-only CLI utility; unsupported V1 capabilities fail before HTTP. |
| retain | `cli/track_anywhere_cli/command_recurring.py` | [V2 CLI composition](../../cli/track_anywhere_cli/click_app.py) | Task 23 V2-only CLI utility; unsupported V1 capabilities fail before HTTP. |
| retain | `cli/track_anywhere_cli/command_system.py` | [V2 CLI composition](../../cli/track_anywhere_cli/click_app.py) | Task 23 V2-only CLI utility; unsupported V1 capabilities fail before HTTP. |
| retain | `cli/track_anywhere_cli/commands.py` | [V2 CLI composition](../../cli/track_anywhere_cli/click_app.py) | Task 23 V2-only CLI utility; unsupported V1 capabilities fail before HTTP. |
| retain | `cli/track_anywhere_cli/config.py` | [V2 CLI composition](../../cli/track_anywhere_cli/click_app.py) | Task 23 V2-only CLI utility; unsupported V1 capabilities fail before HTTP. |
| retain | `cli/track_anywhere_cli/data_backup.py` | [V2 CLI composition](../../cli/track_anywhere_cli/click_app.py) | Task 23 V2-only CLI utility; unsupported V1 capabilities fail before HTTP. |
| retain | `cli/track_anywhere_cli/device_login.py` | [V2 CLI composition](../../cli/track_anywhere_cli/click_app.py) | Task 23 V2-only CLI utility; unsupported V1 capabilities fail before HTTP. |
| retain | `cli/track_anywhere_cli/exit_codes.py` | [V2 CLI composition](../../cli/track_anywhere_cli/click_app.py) | Task 23 V2-only CLI utility; unsupported V1 capabilities fail before HTTP. |
| retain | `cli/track_anywhere_cli/http.py` | [V2 CLI composition](../../cli/track_anywhere_cli/click_app.py) | Task 23 V2-only CLI utility; unsupported V1 capabilities fail before HTTP. |
| retain | `cli/track_anywhere_cli/interaction.py` | [V2 CLI composition](../../cli/track_anywhere_cli/click_app.py) | Task 23 V2-only CLI utility; unsupported V1 capabilities fail before HTTP. |
| retain | `cli/track_anywhere_cli/main.py` | [packaged console script](../../pyproject.toml) | Task 23 V2-only CLI utility; unsupported V1 capabilities fail before HTTP. |
| retain | `cli/track_anywhere_cli/oauth_login.py` | [V2 CLI composition](../../cli/track_anywhere_cli/click_app.py) | Task 23 V2-only CLI utility; unsupported V1 capabilities fail before HTTP. |
| retain | `cli/track_anywhere_cli/output.py` | [V2 CLI composition](../../cli/track_anywhere_cli/click_app.py) | Task 23 V2-only CLI utility; unsupported V1 capabilities fail before HTTP. |
| retain | `cli/track_anywhere_cli/pkce_callback.py` | [V2 CLI composition](../../cli/track_anywhere_cli/click_app.py) | Task 23 V2-only CLI utility; unsupported V1 capabilities fail before HTTP. |
| retain | `cli/track_anywhere_cli/presenter_base.py` | [V2 CLI composition](../../cli/track_anywhere_cli/click_app.py) | Task 23 V2-only CLI utility; unsupported V1 capabilities fail before HTTP. |
| delete | `cli/track_anywhere_cli/presenter_catalog.py` | — | Unreferenced V1 presenter; the V2 registry uses presenter_operations.py only. |
| retain | `cli/track_anywhere_cli/presenter_operations.py` | [V2 CLI composition](../../cli/track_anywhere_cli/click_app.py) | Task 23 V2-only CLI utility; unsupported V1 capabilities fail before HTTP. |
| retain | `cli/track_anywhere_cli/presenters.py` | [V2 CLI composition](../../cli/track_anywhere_cli/click_app.py) | Task 23 V2-only CLI utility; unsupported V1 capabilities fail before HTTP. |
| retain | `cli/track_anywhere_cli/protocol.py` | [V2 CLI composition](../../cli/track_anywhere_cli/click_app.py) | Task 23 V2-only CLI utility; unsupported V1 capabilities fail before HTTP. |
| retain | `cli/track_anywhere_cli/release_version.py` | [V2 CLI composition](../../cli/track_anywhere_cli/click_app.py) | Task 23 V2-only CLI utility; unsupported V1 capabilities fail before HTTP. |
| retain | `cli/track_anywhere_cli/renderers.py` | [V2 CLI composition](../../cli/track_anywhere_cli/click_app.py) | Task 23 V2-only CLI utility; unsupported V1 capabilities fail before HTTP. |
| retain | `cli/track_anywhere_cli/runtime.py` | [V2 CLI composition](../../cli/track_anywhere_cli/click_app.py) | Task 23 V2-only CLI utility; unsupported V1 capabilities fail before HTTP. |
| rewrite | `conftest.py` | [V2 PostgreSQL test boundary](../../backend/tests/v2/conftest.py) | Remove the root SQLite default before V1 deletion. |
| rewrite | `contract_tests/api_clients.py` | [V2 contract tests](../../contract_tests/test_api_conformance.py) | Retain only V2 FastAPI client behavior and no SQLite fallback. |
| rewrite | `contract_tests/conftest.py` | [V2 contract client](../../contract_tests/api_clients.py) | Retain only isolated PG17 V2 fixture composition. |
| delete | `scripts/benchmark-write-performance.py` | — | V1 FinanceService and SQLite benchmark with no V2 consumer. |

## Execution constraints

1. Delete only rows marked `delete`; rewrite only rows marked `rewrite`.
2. Retained directories do not authorize new root modules. The post-retirement
   module allowlist is the retained root entries above.
3. The retained auth package and CLI modules must continue to have the named V2
   consumer. If that consumer disappears, delete the utility or amend this
   matrix through review.
4. Do not delete V1 until the gate decision in
   [v2-pre-retirement-verification.md](v2-pre-retirement-verification.md) is
   `APPROVED`.
