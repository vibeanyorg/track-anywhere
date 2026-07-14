# V2 capability matrix

This is the retirement boundary for the greenfield V2 ledger. A row is
`implemented` only when a V2 owner, executable test, and production-code
evidence all exist. `deferred` means no V2 contract exists yet and callers must
fail closed. `removed` means the V1 concept is deliberately not carried into
V2. There is no compatibility fallback and no unreviewed or unknown status.

The scope named by each implemented row is exact. For example, Book membership
means atomic owner membership at Book creation plus membership authorization;
it does not imply an invitation or membership-administration API.

| Capability | Status | Owner | Test | Evidence | Reason |
| --- | --- | --- | --- | --- | --- |
| Auth | implemented | V2 platform | [V2 auth API contract](../../backend/tests/v2/contract/test_v2_auth_api.py) | [persistent V2 auth router](../../backend/app/track_anywhere/api/v2/auth.py) | — |
| Book and Book membership | implemented | V2 ledger | [atomic Book owner membership](../../backend/tests/v2/postgres/test_catalog_commands.py) | [Book creation command](../../backend/app/track_anywhere/application/catalogs/create_book.py) | Scope is Book creation, atomic owner membership, and membership guards; later membership administration requires a new reviewed row. |
| Assets, Accounts, and category versions | implemented | V2 ledger | [catalog command integration](../../backend/tests/v2/postgres/test_catalog_commands.py) | [V2 catalog models](../../backend/app/track_anywhere/infrastructure/db/models/catalog.py) | — |
| Drafts | removed | — | — | — | Draft-first V1 capture is replaced by explicit, idempotent V2 journal commands. |
| Counterparties | deferred | — | — | — | V2 reserves the reporting dimension but has no immutable counterparty catalog contract; classification fails closed without one. |
| Projects | deferred | — | — | — | V2 reserves the reporting dimension but has no immutable project catalog contract; classification fails closed without one. |
| Journal | implemented | V2 ledger | [journal API contract](../../backend/tests/v2/contract/test_v2_journal_api.py) | [post transaction command](../../backend/app/track_anywhere/application/journal/post_transaction.py) | — |
| Reversal and correction | implemented | V2 ledger | [reversal integration](../../backend/tests/v2/postgres/test_reverse_transaction.py) | [explicit correction command](../../backend/app/track_anywhere/application/journal/correct_transaction.py) | — |
| External references | implemented | V2 ledger | [external-reference correction integration](../../backend/tests/v2/postgres/test_external_reference_correction.py) | [external-reference correction command](../../backend/app/track_anywhere/application/journal/correct_external_reference.py) | — |
| Classification | implemented | V2 ledger | [reporting-line command integration](../../backend/tests/v2/postgres/test_reporting_line_commands.py) | [reporting-line V2 API](../../backend/app/track_anywhere/api/v2/reporting.py) | Category-version classification only; unsupported dimensions fail closed. |
| FX | implemented | V2 ledger | [FX command integration](../../backend/tests/v2/postgres/test_record_fx.py) | [FX journal command](../../backend/app/track_anywhere/application/journal/record_fx.py) | — |
| Investment lots | implemented | V2 investments | [lot projection integration](../../backend/tests/v2/postgres/test_investment_lot_projection.py) | [investment lot V2 API](../../backend/app/track_anywhere/api/v2/investments.py) | — |
| Valuations | deferred | — | — | — | Lot acquisition and disposal exist, but no V2 valuation event, projection, or public query contract has been approved. |
| Monthly reports | implemented | V2 projections | [monthly projection and integrity tests](../../backend/tests/v2/postgres/test_monthly_summary_projection.py) | [monthly summary projection](../../backend/app/track_anywhere/infrastructure/projections/monthly_summary.py) | Scope is deterministic per-Book monthly category summaries and replay parity; a broader analytics API is not implied. |
| Budgets | deferred | — | — | — | No V2 budget aggregate, event schema, or API contract exists. |
| Search | deferred | — | — | — | No V2 search index, privacy policy, or as-of query contract exists. |
| CLI | implemented | V2 clients | [CLI V2 boundary tests](../../cli/tests/test_cli_v2_boundaries.py) | [V2 CLI composition](../../cli/track_anywhere_cli/click_app.py) | Only advertised V2 commands are implemented; unsupported V1 command groups fail before HTTP. |
| Attachments | deferred | — | — | — | Event privacy forbids attachment content and no V2 object-storage, metadata, authorization, or retention contract exists. |
| Imports and quarantine | implemented | V2 migration | [quarantine seal gate](../../backend/tests/v2/backfill/test_quarantine_gate.py) | [deterministic quarantine implementation](../../backend/tools/backfill_v1/quarantine.py) | Scope is the deterministic V1 snapshot backfill path; it is not a general-purpose online import API. |
| Recurring rules | removed | — | — | — | V1 recurring schedulers and generated drafts are intentionally absent; any future rules engine needs a new event contract. |
| Payment instruments and tools | removed | — | — | — | V1 payment-profile and payment-instrument helpers are not ledger primitives and have no V2 fallback. |
| Backup and restore | deferred | — | — | — | The V1 client-side SQLite backup command is removed; PostgreSQL backup and restore require a separately validated operations runbook. |
| System and operations configuration | implemented | V2 operations | [PG17 readiness and identity tests](../../backend/tests/v2/postgres/test_v2_readiness.py) | [fail-closed V2 system router](../../backend/app/track_anywhere/api/v2/system.py) | Scope is health, exact PG17 readiness, schema head, and non-owner runtime identity validation. |

## Retirement rule

Only rows marked `implemented` may retain reachable V2 runtime code. Deferred
and removed rows must have no hidden V1 route, adapter, SQLite fallback, or CLI
network call. The exact file decision is frozen in
[the retirement manifest](v2-retirement-manifest.md). The user-authorized
greenfield deletion is complete; the remaining fixed-dump and exact-image gates
in [local verification evidence](v2-pre-retirement-verification.md) block release
and cutover, not source cleanup.
