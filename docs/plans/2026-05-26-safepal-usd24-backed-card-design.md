# SafePal USD24-Backed Card Design

## Context

SafePal Card USD(5964) currently behaves like an ordinary USD asset account. When a card purchase is recorded, the card account goes negative until a separate manual balance adjustment or settlement is entered.

That is not the real user model. The user action is simply "I paid with SafePal." The card is backed by SafePal USD24, and for the first version we intentionally approximate `1 USD = 1 USD24` with no FX spread, fee, slippage, or rate difference.

The product should expose a simple payment interface while preserving detailed ledger facts underneath.

## Decision

Model SafePal Card as a payment instrument backed by a token account, not as a user-managed standalone cash balance.

Daily use should be:

```bash
ta expense record --payment safepal --amount 3.40 --currency USD --category-id <category> --purpose "Meituan"
```

or an agent instruction such as:

```text
SafePal paid 3.40 USD for food delivery.
```

The system resolves the `safepal` payment profile and records one confirmed transaction containing both:

- the user-facing expense in USD;
- the immediate backing settlement from USD24 to the card clearing account.

## Payment Profile

Add a persistent payment profile for backed cards:

```text
payment_profile
  profile_id
  book_id
  slug
  display_name
  kind = token_backed_card
  instrument_account_id = SafePal Card USD(5964)
  instrument_currency = USD
  backing_account_id = SafePal USD24 (Arbitrum)
  backing_currency = USD24
  settlement_mode = immediate
  settlement_rate = 1
  status = active
```

The profile is setup/admin configuration. It should not be part of the normal daily spending flow.

## Ledger Shape

For a SafePal payment of `3.40 USD`, the transaction should balance per asset while keeping both user-visible and audit-visible details:

```text
SafePal Card USD clearing      -3.40 USD
System expense USD             +3.40 USD
SafePal USD24 (Arbitrum)       -3.40 USD24
System FX clearing USD24       +3.40 USD24
SafePal Card USD clearing      +3.40 USD
System FX clearing USD         -3.40 USD
```

Net effect:

- expense reports include `3.40 USD`;
- SafePal USD24 decreases by `3.40 USD24`;
- SafePal Card USD clearing returns to zero;
- system FX clearing accounts carry the cross-asset bridge, consistent with existing FX exchange architecture.

The transaction should include:

- an `expense` line for reporting and category classification;
- an `fx_exchange` or settlement line for audit context;
- audit details with profile id, instrument account, backing account, fixed rate, and settlement mode.

## User-Facing Balance

The default SafePal view should not lead with the raw card clearing balance. It should show a composite view:

```text
SafePal
  spendable backing balance: 277.44 USD24
  effective card balance: approx 277.44 USD
  card clearing balance: 0.00 USD
```

The raw card clearing account remains available for audit, but it should normally be zero after each immediate-settlement purchase.

## Error Handling

- If the payment profile is missing or inactive, return a clear setup error.
- If the spending currency does not match the instrument currency, reject the command.
- If the backing account has insufficient USD24 for the 1:1 settlement, reject the command before writing.
- If the instrument and backing accounts are in different books, reject the profile or command.
- Idempotency must cover both expense and settlement legs as one atomic operation.

## Non-Goals

- No live FX rate lookup.
- No spread, slippage, gas, or fee modeling in the first version.
- No automatic reconciliation from SafePal statements.
- No automatic conversion in global summaries.
- No hardcoded real account ids in source code.

## Acceptance Criteria

- A single SafePal payment command records both the expense and backing settlement.
- SafePal USD24 decreases by the payment amount at 1:1.
- SafePal Card USD clearing does not remain negative after immediate-settlement payments.
- Expense category reports include the payment amount once.
- `ta tx show` exposes all postings and lines needed to audit the settlement.
- Replaying the same idempotency key does not duplicate the expense or the USD24 deduction.
- Insufficient USD24 rejects the command without partial writes.
