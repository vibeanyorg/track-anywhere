# Payment instrument lifecycle (v0.2.8)

A physical card is one PaymentInstrument with any number of currency bindings.
Accounts remain single-currency. Expense and same-currency repayment select the
binding using `amount.asset_code`; FX repayment uses `target_amount.asset_code`.
Missing currency returns an explicit error: callers must clarify actual billing
currency and exact amount. No account creation or exchange-rate inference occurs.

## Model and migration

`v2_0017_card_lifecycle` adds instrument version/effective dates and the
`payment_instrument_mutations` append-only receipt/audit table. Each receipt saves
the actor, exact validated command, before/after views and recording time. Runtime
roles can select/insert receipts but cannot update/delete them. Instrument rows
are current projections; prior metadata and binding configurations remain in
receipts. Existing instrument IDs, binding IDs, accounts, transaction links,
postings and amounts remain unchanged. Existing effective_from is initialized
from the earliest binding, falling back to creation time.

Binding settlement_policy is exposed explicitly but remains the card's immutable
policy, preserving the existing database policy validator. All bindings on a
statement card must reference a non-system, active credit-card liability account
in the same book with the same currency. Immediate/prepaid bindings require
ordinary asset accounts. Mixed settlement policies on one card are unsupported.

Configuration operations and financial commits serialize on the existing book
lock. A database trigger also rejects intersecting `[effective_from,effective_to)`
binding windows for the same book/instrument/currency. Closed historical windows
are protected against backdated overlapping replacement. Binding close requires
an end after its start and no later than now. It prevents new preparations even
for backdated entries. Instrument close is immediate; reopening starts a new
instrument validity interval and does not reopen closed bindings.

## Tools and HTTP

All five new tools require `book_id`, `request_id`, `payment_instrument_id` and the
existing `book:write` grant:

- `ledger_update_payment_instrument`: current_name, network, provider_code,
  form_factor, last4. Omitted MCP fields remain unchanged.
- `ledger_close_payment_instrument`: stop new preparations and reject stale
  prepared commits; retain account balances and history.
- `ledger_reopen_payment_instrument`: resume preparations for the new interval.
- `ledger_add_payment_instrument_binding`: settlement_account_id, asset_code,
  settlement_policy, effective_from; returns the new binding ID in bindings.
- `ledger_close_payment_instrument_binding`: binding_id, effective_to.

HTTP clients use
`POST /api/v2/books/{book_id}/payment-instruments/{instrument_id}/mutations`
with a typed command containing matching book_id/instrument_id, request_id,
operation (`update`, `close`, `reopen`, `add_binding`, `close_binding`) and only the
fields allowed for that operation. HTTP metadata update can explicitly clear last4
with null. Path/command mismatch and inappropriate fields are rejected.

Results contain committed, replayed, verification_status and the instrument view.
Exact retries return the recorded result even after subsequent changes. Reusing a
request ID with different arguments or actor fails. Successful mutations verify
the durable receipt in a fresh transaction. For uncertain failures, retry exactly
the same request ID. New card creation also records its initial configuration and
verifies readback; its existing derived IDs preserve the legacy MCP contract.

List/get returns one record per card, with all bindings, account names, currency,
policy, status, validity and version. `status=active`, `inactive`, `closed`,
`frozen`, and `all` are supported. Old flat binding fields are preserved when
exactly one active binding exists and are null when there is no unique binding.
Consumers of multicurrency cards must use `bindings` and the amount currency.

## Existing cards and migration limits

There is no automatic production consolidation. For a known duplicate, correct
the chosen card's metadata, add the missing currency binding, then close the
redundant card. Historical references remain on the old IDs; never move existing
transaction links, reparent binding IDs, or generate accounting adjustments for
metadata corrections. These actions need the user's explicit identification of
the cards and confirmation that they represent the same physical card.

New creation rejects an active card with the same provider_code and non-null
last4, even when network or currency differs. Metadata identity changes and reopen
also reject newly introduced collisions. This is conservative: two genuinely
different active cards sharing provider and last4 need future stronger identity
support. Legacy duplicates can still correct metadata without changing identity.

TODO: an explicit consolidation command with superseded_by/aliases and a reviewed
plan should append new target bindings, close sources and preserve every old
transaction/binding ID. Never merge by last4 alone. Also defer original transaction
currency versus exact billing amount fields: current expense amount is the ledger
billing amount. A future extension must retain original amount in protected
narrative and use only the independently supplied billing amount for postings.

## Verification

PostgreSQL 17 integration covers multiple currency routes, currency mismatch,
missing currency, overlap validation at both service and database boundaries,
expense commit, credit-card repayment preparation, unchanged postings after
metadata changes, append-only audit grants, exact retry after later mutations,
close/reopen, binding close and stale prepared commit rejection. Existing
single-currency prepaid and statement orchestration remains covered.

Unit tests reject invalid operation fields. OAuth MCP contract tests check the
five tool schemas, write scopes, annotations and request IDs. The reviewed HTTP
snapshot includes the typed mutation endpoint. The full pytest suite uses
`--import-mode=importlib` because existing test filenames collide across folders.

Local verification: 1808 tests passed, 16 environment-gated tests skipped; 30 focused
entry/lifecycle tests passed after adding prepared metadata snapshots. Frontend
tests, lint and production build passed. Alembic check reports no model drift.
