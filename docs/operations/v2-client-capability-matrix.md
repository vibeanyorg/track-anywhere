# V2 client capability matrix

This matrix is the reviewed boundary for the greenfield V2 clients. A client
must not infer a V1 operation from a similarly named V2 command. Anything not
listed as implemented fails locally or is absent from the client surface.

Status meanings:

- **Implemented**: the V2 API route exists and the client may call it.
- **Deferred**: intentionally outside the current V2 contract; no fallback.
- **Removed**: the V1-only behavior is not part of V2 and must fail fast.

## CLI

| Capability | Status | V2 boundary |
| --- | --- | --- |
| System health and readiness | Implemented | `GET /api/v2/health`, `GET /api/v2/ready` |
| Book creation and listing | Implemented | `POST/GET /api/v2/books`; listing is restricted to readable Books |
| Asset creation and listing | Implemented | `POST/GET /api/v2/books/{book_id}/assets` |
| Account creation, listing, detail, balance, close, and reopen | Implemented | Book-scoped account routes; list/detail include zero-balance accounts and credit cards must be exactly zero before close |
| Category creation and listing | Implemented | `POST/GET /api/v2/books/{book_id}/categories` |
| Journal post, list, and transaction detail | Implemented | Book-scoped journal command/query routes with optional as-of positions |
| Credit-card charge, payment, refund, and fee | Implemented | Book-scoped semantic card routes; positive amounts only and no caller-selected posting sides |
| Reverse and correct | Implemented | Explicit reversal/correction command routes |
| External-reference correction | Implemented | Explicit journal reference correction route |
| FX | Implemented | `POST /api/v2/books/{book_id}/journal/fx` |
| Classification assign/clear | Implemented | Reporting-line command routes |
| Balances and reporting-line queries | Implemented | Book-scoped V2 query routes with an optional/required as-of position; balances expose current account status and natural liability semantics |
| Investment lot acquire/dispose | Implemented | Book-scoped lot command routes |
| OAuth status, logout, refresh, device, and PKCE login | Implemented | OAuth discovery first; authorization-code + S256 PKCE is the interactive default and device flow is explicit |
| Machine API key | Implemented | `--api-key-file` sends only `X-API-Key`; env use requires `--insecure-automation`; `--token` is OAuth-only |
| Protected descriptions and import archive list/export | Implemented | Owner-authorized explicit reads; archive export decrypts only on explicit request and has no MCP fallback |
| Frozen V1 financial-history apply | Removed | Operator-only one-shot process; there is no client command, HTTP route, MCP tool, or general import fallback. See the [runbook](v1-financial-backfill.md). |
| Local version, schema, and capability output | Implemented | Local CLI metadata; no server fallback |
| Payment instruments and payment profiles | Removed | No V2 route or CLI command group |
| Recurring items, reminders, and draft generation | Removed | No V2 route or CLI command group |
| SQLite/data backup command | Removed | No CLI command group; V2 backup is not a client-side SQLite copy |
| V1 draft capture/confirm/reject/supersede | Removed | Replaced by explicit V2 journal commands |
| Budgets, reconciliation, attachments, backoffice, and broad search | Deferred | Add only with a reviewed V2 contract |
| Valuation/performance reports outside lot commands | Deferred | No V2 route yet |

Financial CLI commands preserve caller-provided decimal strings byte-for-byte
at the JSON boundary and preserve an explicit idempotency key. The CLI may
generate a key only when the caller omitted one; it never derives an amount or
silently maps an unsupported command to another operation.

## Web frontend

| Capability | Status | V2 boundary |
| --- | --- | --- |
| Same-origin protocol proxy | Implemented | `/api/v2/*`, `/.well-known/*`, and Streamable HTTP `/mcp` are forwarded to the configured backend origin |
| API-key to browser-session exchange | Implemented | `POST /api/v2/auth/session/api-key` |
| Session status and logout | Implemented | V2 session routes with CSRF/same-origin enforcement |
| OAuth metadata, PKCE callback, and device approval | Implemented | V2 auth/OAuth routes only |
| ChatGPT MCP connector | Implemented | OAuth-only; resource-bound read scopes, optional `book:write` for three idempotent catalog bootstrap tools, and optional `ledger:write` for four semantic ledger tools; no API-key fallback |
| Ledger entry, query, classify, and reverse UI | Deferred | HTTP contract is implemented; product UI is not yet shipped |
| Payment, recurring, backup, budget, and V1 draft UI | Removed | No V1 proxy or fallback exists |

## Runtime and test boundary

- Docker health checks and stable smoke use V2 health/readiness only.
- The isolated E2E lane covers V2 post, journal/balance query, classification,
  reporting query, reversal, CLI credit-card account creation, and all four
  semantic card writes against PostgreSQL 17.
- Public OpenAPI is pinned in `backend/tests/snapshots/public-api-v2.json`.
- Contract tests provision a fresh migrated PostgreSQL 17 database and install
  its runtime-role URL before app construction. SQLite is not a supported test
  substitute for the V2 contract.
- The frozen historical import is an offline, hash-pinned one-shot operation
  described in the [operator runbook](v1-financial-backfill.md). Clients can
  read the resulting V2 ledger and owner-authorized archive, but cannot invoke
  or generalize the importer.
