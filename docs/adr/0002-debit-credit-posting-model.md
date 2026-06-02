# ADR 0002: Debit/Credit Posting Model

## Status

Accepted for migration planning.

## Decision

Upgrade confirmed ledger postings from signed raw amounts to explicit debit/credit
postings:

```text
Posting(account_id, side, amount, currency)
side = debit | credit
amount > 0
```

The ledger invariant becomes:

```text
sum(debit amounts by currency) == sum(credit amounts by currency)
```

Account balances are derived from posting side plus account type, not from a
globally signed posting amount.

## Drivers

- Credit-card liabilities must use user-facing semantics: a larger outstanding
  card balance is a larger positive liability, not an inverted raw negative.
- Agents must express business intent without guessing sign conventions.
- Financial truth must remain auditable, reversible, and derivable from
  immutable postings.
- Reports, account balances, credit-card summaries, and category lines must use
  one shared accounting model.
- Existing signed postings have created ambiguity between balancing signs,
  account balance deltas, and display semantics.

## Problem With The Current Model

Current postings are modeled as:

```text
Posting(account_id, signed_amount, currency)
```

and validated with:

```text
sum(signed_amount by currency) == 0
```

That works for simple asset-to-asset transfers, but it makes liability and
income semantics ambiguous. A credit-card purchase has two natural increases:

```text
expense increases
credit-card liability increases
```

The signed-sum model can only balance that transaction by making one side
negative, which forces the credit-card account to expose an inverted or unclear
raw balance. This is unsafe for agent-led bookkeeping because successful writes
can be directionally wrong while still passing ledger balancing.

## Target Accounting Semantics

Posting side controls accounting direction:

```text
asset:     debit increases, credit decreases
expense:   debit increases, credit decreases
liability: credit increases, debit decreases
income:    credit increases, debit decreases
equity:    credit increases, debit decreases
fund:      debit increases, credit decreases
system:    role-specific; must be explicit at the transaction builder boundary
```

Credit-card purchase:

```text
Debit  expense account       11.08 USD
Credit credit-card liability 11.08 USD
```

Credit-card payment:

```text
Debit  credit-card liability 11.08 USD
Credit bank asset            11.08 USD
```

Cash/card expense from asset:

```text
Debit  expense account       11.08 USD
Credit bank asset            11.08 USD
```

Income deposit:

```text
Debit  bank asset            100.00 USD
Credit income account        100.00 USD
```

## Balance Derivation

Expose account balances as natural balances:

```text
asset balance     = debits - credits
expense balance   = debits - credits
fund balance      = debits - credits
system balance    = debits - credits
liability balance = credits - debits
income balance    = credits - debits
equity balance    = credits - debits
```

Read models must expose these semantics explicitly:

```text
asset     -> natural_asset_balance
fund      -> natural_fund_balance
system    -> natural_system_balance
liability -> natural_liability_balance
expense   -> natural_expense_balance
income    -> natural_income_balance
equity    -> natural_equity_balance
```

Credit-card display fields must not expose raw accounting internals:

```text
outstanding_balance = max(liability_balance, 0)
outstanding_balance_semantics = natural_liability_balance_positive_owed
overpayment_balance = max(-liability_balance, 0)
overpayment_balance_semantics = natural_liability_balance_negative_overpayment
natural_balance = liability_balance
natural_balance_semantics = natural_liability_balance
balance_semantics = natural_liability_balance
current_balance_semantics = natural_liability_balance
derived_available_credit = credit_limit - outstanding_balance + overpayment_balance
derived_available_credit_semantics = credit_limit_minus_outstanding_balance_plus_overpayment_balance
```

If `current_balance` remains in credit-card APIs for compatibility, it is only a
legacy alias of `natural_balance`; clients must use `natural_balance_semantics`,
`current_balance_semantics`, `compatibility_aliases`, `outstanding_balance`,
`outstanding_balance_semantics`, `overpayment_balance`,
`overpayment_balance_semantics`, or `derived_available_credit_semantics`
instead of inferring meaning from the sign.

Net worth should use natural semantics:

```text
net_worth = assets + funds - liabilities
```

## API And Agent Contract

Agents and clients must not construct debit/credit postings directly for common
financial intents. They should call semantic commands:

```text
expense.record
income.record
tx.record
credit_card.purchase
credit_card.payment
credit_card.refund
balance.adjust
```

The service layer owns translation from intent to debit/credit postings. A
command may accept a signed natural balance delta only as an input adapter, such
as opening balance or balance adjustment, but that value must be immediately
converted into explicit `side` plus positive `amount`. Signed deltas are not a
posting representation.

For liability accounts, command-level natural deltas are interpreted in natural
liability space: a positive delta increases outstanding debt, and a negative
delta decreases debt or creates overpayment. Credit-card snapshot importers must
first normalize provider displays into that natural liability space: amount owed
is positive, overpayment or credit balance is negative. This rule belongs at the
command adapter/read-model boundary; storage still writes positive debit/credit
postings only.

Generic posting APIs, if retained, must require explicit `side` and positive
`amount`. They must reject signed amounts.

Transaction output carries field-level posting semantics:

```text
posting_amount_field = postings.amount
posting_side_field = postings.side
posting_amount_semantics_field = postings.amount_semantics
debit_credit_amount_rule = posting amount is positive
debit_credit_side_rule = posting side is the only persisted debit/credit direction
```

Public OpenAPI request schemas expose the same contract through
`x-posting-semantics`. Agents that generate API calls from OpenAPI must read
that extension, treat `forbidden_input_fields` as rejected raw posting internals,
and keep using command-level business amounts instead of synthesizing
`postings`, `side`, `amount_semantics`, `signed_amount`, or `raw_amount`.
The extension is a public write guard: it can appear on write schemas that do
not create ledger postings, so clients must not infer posting behavior from its
presence alone.

The domain `Posting` constructor defaults to `amount_semantics=debit_credit`.
Legacy signed construction must be explicit through `legacy_signed_posting(...)`
or storage/migration loaders. This keeps dirty historical rows representable for
audit while preventing new naked `Posting(account, -10, currency)` calls from
silently creating signed raw ledger semantics.
Confirmed transaction builders default to rejecting `legacy_signed` postings;
historical compatibility paths must opt in with an explicit legacy allowance.
That opt-in is for migration/audit fixtures and reversal of already-legacy rows,
not ordinary business writes. Persistence boundaries must carry the same rule:
repository writes for new confirmed postings reject `legacy_signed` unless the
ledger change explicitly marks itself as a legacy-compatibility write.
Repository writes must also re-run transaction-level posting validation before
inserting new posting rows, including account/book/currency checks and
per-currency debit/credit balancing, so code that bypasses domain builders cannot
persist an unbalanced debit/credit transaction.
Draft repository writes carry the same boundary: new draft postings must use
`debit_credit` semantics unless a migration/audit path explicitly opts into
legacy compatibility.
Draft domain and repository validation also reject unbalanced proposed postings
by currency before replacing stored draft rows, so complete agent-created drafts
cannot enter the system with debit/credit rows that would only fail later at
confirmation.
Even explicit draft legacy-compatibility writes must remain internally
consistent: a draft may not mix `legacy_signed` and `debit_credit` rows, and
legacy signed draft rows must still balance by currency.

Credit-card write paths must require explicit intent. A generic transfer or
balance adjustment involving `type=liability, subtype=credit_card` must not rely
on caller-provided signs to infer purchase, payment, refund, fee, or adjustment.

Balance read APIs and CLI presenters must expose account type and balance
semantics so users and agents do not infer meaning from signs:

```text
account_type
balance_semantics
official_balance.amount
official_balance.amount_semantics
liability_balance.outstanding_amount
liability_balance.outstanding_amount_semantics
liability_balance.overpayment_amount
liability_balance.overpayment_amount_semantics
```

## Storage Migration Strategy

Use a staged migration so historical confirmed postings remain auditable:

1. Add `side` to `postings` and `draft_postings`.
2. Add `amount_semantics` to distinguish old signed rows from new
   debit/credit rows during the staged cutover.
3. Backfill `side` mechanically from the legacy signed balancing sign:
   positive legacy amounts become `debit`, negative legacy amounts become
   `credit`, for every account type.
4. Set legacy rows to `amount_semantics = legacy_signed`; new rows use
   `amount_semantics = debit_credit`.
5. Teach balance reads to interpret both semantics:
   legacy rows use the old signed amount, debit/credit rows derive natural
   balance from account type and side.
6. Rewrite service write paths to emit positive `amount` plus explicit `side`.
7. Rewrite stored legacy amounts to absolute positive values only after all
   read/write paths understand debit/credit semantics.
8. Update immutable-posting integrity checks to compare `(account_id, side,
   amount_semantics, amount, currency, book_id)`.
9. Update serializers and CLI output to expose `side` plus positive `amount`,
   and catalog, book-scoped, and backoffice account payloads to expose
   `balance_semantics` so liability balances are not interpreted as old signed
   raw balances.
10. Remove or quarantine compatibility paths that accept signed posting amounts.

Fresh schemas must define database check constraints for posting semantic shape:

```text
amount_semantics in (legacy_signed, debit_credit)
amount_semantics is not null
side is null or side in (debit, credit)
debit_credit rows require side and amount > 0
legacy_signed rows require amount != 0
```

Do not add a historical migration constraint before legacy databases can boot
far enough to run the audit. Existing rows are first classified by the posting
semantics audit, then rewritten or manually resolved. Repository writes still
enforce the same semantic shape before persisting new confirmed or draft
postings. After legacy columns are added and future defaults are switched back
to `debit_credit`, Alembic must add DB-level checks for canonical
`debit_credit` row shape. It must not block dirty `legacy_signed` rows, unknown
historical semantics, or zero legacy amounts from loading into the audit as
manual blockers.
For that reason the migrated-schema semantic constraint is intentionally
narrower than the fresh ORM schema: it blocks malformed canonical
`debit_credit` rows, while fresh schema and repository write guards enforce the
full enum, non-null, and legacy nonzero contracts for new data.
Application code must not hard-code `allow_legacy_signed_postings=True` or
`allow_legacy_signed=True` in public write paths. The legacy allowance is an
internal migration/audit compatibility boundary, not a business write option.
Likewise, production application code outside the domain helper that defines
`legacy_signed_posting(...)` must not call that constructor directly; tests and
audit fixtures may use it to represent historical rows.
Business write code must also avoid naked `Posting(...)` construction. New
business postings should be created through debit/credit helper constructors,
account-type-aware balance-delta adapters, or transaction builders; storage
read/load code may construct `Posting` objects only to rehydrate already
persisted rows.

Fresh schemas default `postings.amount_semantics` and
`draft_postings.amount_semantics` to `debit_credit`. Legacy SQLite adoption and
Alembic backfill paths may use `legacy_signed` defaults only while adding the
new columns to pre-existing tables, because those rows must remain classified as
historical signed data until audit/rewrite resolves them. After the backfill,
the migration must switch database defaults back to `debit_credit` so future
rows cannot silently inherit legacy signed semantics.
Storage readers may only treat an absent `amount_semantics` attribute as the
pre-cutover legacy bridge. If the column exists and the value is `NULL`, that is
dirty current storage and must fail validation instead of silently becoming
`legacy_signed`.

Balance SQL must use an explicit semantic allowlist. It may count
`legacy_signed` rows with the old signed amount and valid `debit_credit` rows
with account-type-aware natural balance math. A valid `debit_credit` row has
`side in (debit, credit)` and `amount > 0`; malformed debit/credit rows must
not contribute to balances. Read paths must not treat unknown `amount_semantics`
values as signed amounts. Cached read paths must follow the same rule and skip
postings whose account or semantic shape cannot be resolved.
Draft projected balances and pending impact must follow the same rule. In-memory
draft impact helpers must require the target account type; they must not compute
impact from `posting.amount` alone. New draft creation and draft supersede
operations must validate proposed posting semantic shape and require
`debit_credit` semantics before the draft enters domain state; projection
tolerates malformed or legacy signed loaded rows only as a
historical-data quarantine behavior.
Category line generation and reporting helpers must also use explicit
`legacy_signed` / `debit_credit` handling. Unknown `amount_semantics` values
must not be treated as legacy signed expense or income lines.

The required preflight gate before step 7 is the posting semantics audit:

```text
GET /api/v1/system/posting-semantics-audit?book_id=book_default
GET /api/v1/system/posting-semantics-cutover-plan?book_id=book_default
POST /api/v1/system/posting-semantics-rewrite?book_id=book_default
POST /api/v1/system/posting-semantics-review-resolutions?book_id=book_default
```

Both `POST` endpoints require `X-Idempotency-Key`. Replaying the same key with
the same payload returns the originally recorded migration result; reusing the
key for a different payload is an idempotency conflict.

CLI equivalents must expose the same `book_id` boundary:

```text
ta system posting-semantics audit --book-id book_default
ta system posting-semantics cutover-plan --book-id book_default
ta system posting-semantics rewrite --book-id book_default --idempotency-key <key>
ta system posting-semantics resolve --book-id book_default --decision-json '<decision>' --idempotency-key <key>
```

Review resolution input accepts business actions only. Clients must submit
`record_ref` or `transaction_id`, `position`, `account_id`, `currency`,
`legacy_amount`, and `action`; they must not submit `target_side`,
`target_amount`, `side`,
`amount_semantics`, or other raw posting fields. The resolver maps the approved
action to debit/credit internally. If both `record_ref` and `transaction_id`
are supplied, they must match exactly; otherwise the request is rejected instead
of silently choosing one identifier. Submitted identity fields must be stable:
`position` is a non-negative JSON integer, and `legacy_amount` must be a decimal
string, not a JSON number, so idempotency hashes and exact legacy-row matches
remain stable.

The automatic positive-only rewrite may proceed only when:

```text
auto_rewrite_ready = true
manual_review_blockers = []
mixed_semantics_transactions = 0
unbalanced_transactions = 0
```

`cutover_ready = true` is the terminal condition after rewrite, not the
precondition for rewrite:

```text
cutover_ready = true
legacy_signed_postings = 0
issues = []
debit totals equal credit totals by currency for every debit_credit transaction
```

If the audit reports legacy liability or credit-card postings, those rows must
be reviewed economically before any automated rewrite. This prevents a technical
schema migration from silently changing the meaning of historical debt. Legacy
non-liability rows can be emitted as `auto_rewrite_candidates` with explicit
target side, positive target amount, and row `position`.

Audit issues must expose stable `issue_type` and, for posting-level issues,
`position` fields so agents do not parse English strings or guess row identity
to decide migration behavior. Required issue types include:

```text
legacy_liability_review_required
missing_account
invalid_amount_semantics
invalid_legacy_signed_shape
invalid_debit_credit_shape
mixed_transaction_semantics
unbalanced_legacy_signed_transaction
unbalanced_debit_credit_transaction
```

Legacy liability blockers must include `manual_review_recommendations` with
explicit choices:

```text
confirm_as_outstanding_liability -> target_side = credit
confirm_as_liability_reduction_or_overpayment -> target_side = debit
```

The reviewer chooses the economic meaning. The system must not infer it from
the old signed amount.

Manual review resolutions must include the original row identity and legacy
amount. The resolver rewrites only rows that still match the reviewed legacy
state:

```text
transaction_id or record_ref
position
account_id
currency
legacy_amount
action
```

Supported actions:

```text
confirm_as_outstanding_liability -> writes credit + abs(legacy_amount)
confirm_as_liability_reduction_or_overpayment -> writes debit + abs(legacy_amount)
```

If the positioned row no longer matches exactly one posting, resolution fails
instead of guessing. Position is part of the safe row identity; callers still
must not submit raw write fields such as `target_side`, `target_amount`, `side`,
or `amount_semantics`.
The review decision schema must expose recommendation-only metadata such as
`amount_semantics`, `target_side`, and `target_amount` as read-only fields:
agents may inspect them in audit recommendations, but must not copy them into
resolver input.

The resolver must cover every current `manual_review_recommendations` row exactly
once. Partial manual resolution is rejected because it can leave a confirmed
transaction with mixed `legacy_signed` and `debit_credit` postings. After all
manual liability rows are resolved, the resolver may immediately run the
mechanical non-liability rewrite when the cutover plan reports
`auto_rewrite_ready = true`.

The rewrite endpoint is deliberately conservative:

```text
confirmed legacy non-liability postings -> rewritten to debit_credit
draft legacy non-liability postings -> rewritten to debit_credit
confirmed legacy liability postings -> rejected until manual review resolves them
draft legacy liability postings -> rejected until manual review resolves them
```

This keeps the irreversible economic part of the migration separate from the
mechanical row-format rewrite.

The mechanical rewrite must execute the audited candidate list, not a broad
book-level update. Each candidate is matched by transaction or draft id,
position, account, currency, and original legacy amount. If any candidate no
longer matches exactly one legacy row, the rewrite fails and rolls back instead
of guessing or rewriting extra rows that were not present in the audit plan.

Backfill rule for legacy signed postings:

```text
legacy amount > 0:
  side = debit
  amount = abs(legacy amount), normalized without a leading plus sign

legacy amount < 0:
  side = credit
  amount = abs(legacy amount), normalized without a leading plus sign
```

Backfill is a technical conversion of the old balancing sign into explicit
debit/credit side. It preserves row evidence and transaction balancing better
than account-type-based natural-delta inference. It deliberately does not
preserve the old signed raw balance display for credit-normal accounts such as
liabilities, income, and equity: natural debit/credit balance semantics make
credit-normal increases positive. It is not a guarantee that every old
credit-card transaction was economically correct. Suspect credit-card
transactions still require a separate audit/reversal workflow.

## Historical Data Integrity

Do not silently rewrite economic meaning. The schema backfill must preserve the
old row identity, old signed amount, inferred balancing side, and auditability;
it must not claim that credit-normal natural balances preserve the old raw sign
display. Separately generate an audit report for:

```text
credit-card expenses recorded through generic expense paths
credit-card transactions with category lines whose side conflicts with intent
credit-card opening balances and balance adjustments
manual reversals followed by re-recorded credit-card expenses
```

Corrections to economic mistakes should use reversal plus corrected transaction,
not in-place mutation of confirmed transactions.
Reversal code must validate `amount_semantics` explicitly. `debit_credit` rows
use opposite-side reversal. Historical `legacy_signed` rows must be converted
through account-type-aware natural delta into new `debit_credit` reversal
postings; public reversal must not create fresh `legacy_signed` rows. Unknown
semantics are data-integrity blockers, not implicit legacy rows.

## Test Matrix

Migration and model tests:

- Legacy signed debit-normal postings migrate to debit/credit without changing
  natural derived balances for asset transfer, expense, reversal, and adjustment
  cases.
- Legacy signed credit-normal income/equity postings migrate to positive natural
  credit balances instead of preserving old negative raw signs.
- New postings reject zero, negative, and missing side.
- New balancing requires equal debit and credit totals per currency.
- Reversals reject unknown posting semantics instead of guessing a legacy or
  debit/credit interpretation.
- Immutable posting checks include side.

Business behavior tests:

- Asset-funded expense debits expense and credits asset.
- Credit-card purchase debits expense and credits liability.
- Credit-card payment debits liability and credits asset.
- Credit-card refund debits liability or asset according to settlement target
  and credits refund/expense-reduction semantics.
- Income deposit debits asset and credits income.
- Reversal swaps debit and credit sides while preserving positive amount.

Reporting tests:

- Account balance derives natural balances by account type.
- Credit-card summary reports positive outstanding balance for purchases.
- Account summary computes net worth as assets plus funds minus liabilities.
- Category lines report positive expense/income amounts independent of posting
  side display.
- Transaction list/show expose debit/credit clearly enough for agent debugging.

Agent safety tests:

- Generic signed posting payloads are rejected.
- Credit-card generic write attempts without explicit intent are rejected or
  routed through semantic commands.
- CLI and API examples never require users or agents to choose liability signs.

## Consequences

- This is a schema and domain migration, not a local credit-card patch.
- Existing service methods that build postings must be rewritten to emit
  semantic debit/credit entries.
- Drafts, recurring drafts, payment profiles, FX, investments, funds, balance
  adjustments, reversals, snapshots, serializers, and storage integrity checks
  are in scope.
- A compatibility bridge may exist during migration, but the final model must
  not expose signed raw posting amounts as authoritative ledger semantics.
