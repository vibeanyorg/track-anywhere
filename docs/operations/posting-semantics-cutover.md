# Posting Semantics Cutover Runbook

Track Anywhere stores new postings as explicit debit/credit rows:

```text
side = debit | credit
amount > 0
amount_semantics = debit_credit
```

`legacy_signed` is only a historical migration and audit representation. Do not
create new business writes with signed raw posting amounts.

Mechanical rewrite maps the old raw balancing sign to side the same way for all
account types:

```text
legacy amount > 0 -> debit + abs(amount)
legacy amount < 0 -> credit + abs(amount)
```

This is not account-type natural-delta preservation. Credit-normal accounts use
the new debit/credit natural balance semantics after cutover, where credits
increase the natural balance. Credit-card and other liability rows with
ambiguous economic meaning must go through manual review.

## Safety invariants

- Take a backup before every rewrite or manual resolution attempt.
- Never update `postings` or `draft_postings` directly with ad-hoc SQL.
- Use the posting-semantics audit before and after every mutation.
- Migrated databases intentionally keep dirty historical `legacy_signed` or
  unknown-semantics rows loadable so the audit can report them. Database checks
  protect canonical `debit_credit` row shape; the audit and cutover gate are
  responsible for clearing historical semantic blockers.
- Treat non-empty `manual_review_blockers` or
  `manual_review_recommendations` as a hard stop for automatic rewrite.
- A cutover is complete only when the audit reports:

```text
legacy_signed_postings = 0
invalid_amount_semantics_postings = 0
invalid_debit_credit_postings = 0
mixed_semantics_transactions = 0
unbalanced_transactions = 0
manual_review_blockers = 0
cutover_ready = true
```

The `issues` array must also be empty. In particular, it must not contain
`invalid_legacy_signed_shape`, `missing_account`,
`legacy_liability_review_required`, or any unbalanced transaction issue.
Draft posting blockers use the same issue types with transaction references
prefixed as `draft:<draft_id>`.
Each issue includes `amount_semantics` so operators can tell whether the
blocker came from a historical `legacy_signed` row, canonical `debit_credit`
row, or mixed/unknown transaction-level state without re-querying raw rows.
Auto-rewrite candidates and manual review recommendations also include
`amount_semantics`; today they should always identify historical
`legacy_signed` rows as the source model being rewritten or reviewed.

## Local CLI flow

Run from the repository root.

1. Back up the current database.

```bash
ta data backup --label before-posting-semantics-cutover --json
```

2. Inspect the audit.

```bash
ta system posting-semantics audit --json
```

3. Inspect the cutover plan.

```bash
ta system posting-semantics cutover-plan --json
```

4. If the plan is auto-rewrite ready and has no manual liability reviews, run
   the rewrite with a stable idempotency key.

```bash
ta system posting-semantics rewrite \
  --idempotency-key posting-semantics-rewrite-$(date +%Y%m%d) \
  --json
```

5. Re-run the audit and cutover plan.

```bash
ta system posting-semantics audit --json
ta system posting-semantics cutover-plan --json
```

Successful rewrite and resolve responses include a `posting_semantics` block.
It must say `canonical_model = debit_credit` and
`legacy_signed_scope = historical migration and posting-semantics audit only`.

## Manual credit-card review flow

Legacy signed liability postings are ambiguous. A negative legacy row on a
credit-card account might mean a charge/outstanding liability, or it might mean
a payment/overpayment depending on the historical import. The system must not
guess.

1. Generate the plan and extract `liability_review_recommendations`.

```bash
ta system posting-semantics cutover-plan --json
```

2. For each recommendation, choose exactly one business action:

```text
confirm_as_outstanding_liability
confirm_as_liability_reduction_or_overpayment
```

3. Submit decisions with the row identity from the recommendation. Include
   `record_ref` or `transaction_id`, `position`, `account_id`, `currency`, and
   `legacy_amount`. Do not submit `target_side`, `target_amount`, `side`,
   `amount_semantics`, `postings`, `signed_amount`, or `raw_amount`.

```json
{
  "decisions": [
    {
      "transaction_id": "txn_example",
      "position": 0,
      "account_id": "acc_credit_card",
      "currency": "USD",
      "legacy_amount": "-9.36",
      "action": "confirm_as_outstanding_liability"
    }
  ]
}
```

```bash
ta system posting-semantics resolve \
  --decision-file decisions.json \
  --idempotency-key posting-semantics-resolve-$(date +%Y%m%d) \
  --json
```

4. Re-run the audit. If `cutover_ready` is still false, do not continue with
   normal operations until the remaining issues are understood.

## API flow

Use the same sequence through HTTP:

```text
GET  /api/v1/system/posting-semantics-audit
GET  /api/v1/system/posting-semantics-cutover-plan
POST /api/v1/system/posting-semantics-rewrite
POST /api/v1/system/posting-semantics-review-resolutions
```

All mutation requests require `X-Idempotency-Key`.

## Failure handling

- If a rewrite or resolve request fails before changing data, fix the input and
  retry with the same idempotency key.
- If a request reports a partial storage error, stop and restore from the backup
  before retrying. The expected successful path rewrites exact audited rows by
  `(record_ref or transaction_id, position, account_id, currency,
  legacy_amount)`.
- If the post-mutation audit reports any invalid or unbalanced debit/credit
  transaction, stop. That indicates either dirty historical data or a bug in the
  rewrite path.

## Agent-facing rules

- Public write commands accept business amounts, not postings.
- OpenAPI request schemas expose `x-posting-semantics`; agents must treat its
  `forbidden_input_fields` as rejected raw posting internals. The extension is
  a write guard and does not imply that every schema creates postings.
- CLI schema output for mutating commands exposes the same canonical posting
  metadata, including `posting_amount_field`, `posting_side_field`, and
  `debit_credit_side_rule`.
- Agents must not pass raw posting fields into normal write commands.
- Commands such as `draft.confirm`, `tx.reverse`, and recurring draft generation
  do not accept new amount input even though they create or materialize
  postings; agents must rely on the existing draft, transaction, or recurring
  item semantics instead of inventing signed amounts.
- Agents may read `side` and `amount_semantics` from transaction output for
  explanation and audit. Under `debit_credit`, `postings.amount` is positive
  and `postings.side` is the only persisted direction, so agents must not infer
  direction from amount sign.
- `credit_card.update.available_credit` is provider-reported profile metadata,
  not a ledger posting amount or natural liability balance. Use
  `derived_available_credit` fields for ledger-derived availability.
- Agents may only use `legacy_signed` concepts while running audit, cutover
  planning, rewrite, or manual review resolution.
