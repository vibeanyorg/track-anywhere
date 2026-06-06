# Financial accounts API cleanup plan

## Resource boundary

- Keep `/api/v1/accounts` as the legacy catalog-compatible ledger account endpoint. Its behavior stays equivalent to the current storage-backed account list/get/create/update contract.
- Add `/api/v1/ledger-accounts` as an explicit read alias for accounting ledger accounts. It may expose `asset`, `liability`, `income`, `expense`, `equity`, `fund`, and `system` accounts because that is the double-entry resource.
- Add `/api/v1/financial-accounts` as a product-facing read model for user-visible financial accounts. It is an adapter over existing `Account` records, not a schema migration. Default visibility includes `asset`, `liability`, and `fund` accounts that are not system/opening/adjustment/clearing/category accounts; it excludes `income`, `expense`, `equity`, and `system` accounts.

## Compatibility strategy

- Do not change existing `/api/v1/accounts` route shapes, filters, envelopes, or CLI call paths.
- Reuse existing service/storage accessors instead of adding new persistence tables or changing account write paths.
- Return `account_id` and `ledger_account_id` with the same value in `financial-accounts` so the current identifier is explicit without inventing a second ID.

## API shape

- `GET /api/v1/ledger-accounts` returns `{ "ledger_accounts": [...] }` and supports the same filters as legacy `/accounts`.
- `GET /api/v1/ledger-accounts/{account_id}` returns `{ "ledger_account": ... }`.
- `GET /api/v1/financial-accounts` returns `{ "financial_accounts": [...] }` and supports `q`, `type`, `currency`, `institution_type`, `subtype`, `institution`, `status`, and `include=balance`.
- `GET /api/v1/financial-accounts/{account_id}` returns `{ "financial_account": ... }`; non-financial/internal accounts are hidden behind 404.
- `GET /api/v1/financial-accounts/{account_id}/balance` returns the same balance contract as the existing account balance query, but only for visible financial accounts.

## Tests to add/update

- API tests for `financial-accounts` default exclusion of accounting/internal accounts.
- API tests for `q`, `type`, `currency`, `institution_type`, `subtype`, and `institution` filters.
- API tests for `include=balance` and liability balance semantics.
- API tests for `ledger-accounts` exposing ledger-level accounting/system accounts.
- Compatibility test that legacy `/accounts` still returns ledger/accounting accounts.
- Update the public API snapshot for intentional new read routes.

## Migration risk

- No database migration and no write-path change.
- Main risk is semantic drift in the user-visible account classifier because current metadata is sparse. Keep the classifier conservative and unit/API tested; future work can add an explicit `visibility` or `financial_account_kind` field if product semantics need more precision.
