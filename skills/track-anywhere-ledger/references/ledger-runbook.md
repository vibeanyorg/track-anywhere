# Track Anywhere Ledger Runbook

## Service Address

Use `TRACK_ANYWHERE_API` or `TRACK_ANYWHERE_SERVICE_URL` to point `ta` at the
right API service. Prefer this order:

1. Explicit `--base-url`
2. `TRACK_ANYWHERE_API`
3. `TRACK_ANYWHERE_SERVICE_URL`
4. `deploy/env/dev.env` for local Docker development
5. `deploy/env/prod.env` on production/VPS hosts
6. `http://localhost:8000`

Development and production Docker services are intentionally separate:
`track-anywhere-dev-*` for local development and `track-anywhere-prod-*` for
production.

## Read Commands

Discover syntax from the CLI:

```bash
ta --help
ta account --help
ta tx --help
ta summary --help
```

Always request JSON:

```bash
ta account list --json
ta account find --name <text> --currency CNY --json
ta account show <account_id> --json
ta account balance <account_id> --json
ta category list --kind expense --json
ta credit-card list --json
ta tx list --account-id <account_id> --limit 10 --json
ta tx show <transaction_id> --json
ta summary accounts --group-by institution --currency CNY --json
ta summary categories --kind expense --currency CNY --json
```

Use SQL only when no CLI read covers the question. SQL must be read-only. Always report that SQL was used and why.

## Write Safety

Back up before every write:

```bash
ta data backup --label before-<short-change-name> --json
```

On timeout after the API received the request, retry with the same `--idempotency-key`. Do not change the payload.

## Account Taxonomy

Choose accounting `type` from ledger direction:

| Type | Meaning |
| --- | --- |
| `asset` | Cash, bank balances, wallets, investments, crypto balances. |
| `liability` | Credit cards, loans, payable balances. Positive amount means amount owed. |
| `expense` | Fees and spending categories when explicitly known. |
| `equity` | Opening-balance or balancing accounts. Usually system-owned. |
| `system` | Internal adjustment accounts. Do not present as real assets. |

Choose `institution_type` from provider category:

| Institution Type | Examples |
| --- | --- |
| `bank` | 中国银行, 工商银行, 交通银行, 广发银行 |
| `e_wallet` | 微信零钱, 微信零钱通, 支付宝 |
| `fintech` | Wise, Rippling |
| `brokerage` | 雪球, 证券账户 |
| `crypto_wallet` | SafePal and on-chain token balances |
| `cash` | Physical cash |
| `other` | Fees or uncategorized operational accounts |

Use lowercase `subtype` slugs such as `debit_card`, `credit_card`, `checking`, `ewallet_cash`, `ewallet_money_market`, `money_market`, `wealth_management`, `fund`, `multicurrency_wallet`, `payroll_balance`, `crypto_token`, or `fee`.

Model each account as one currency or asset. Wise should be separate `Wise USD`, `Wise EUR`, and `Wise CNY` accounts. Crypto should be separate token/network accounts such as `SafePal USDC (Arbitrum)`.

## Investment Events

Use investment events when a wealth-management, money-market, or fund account needs holding-period and annualized-return analytics. The account balance remains the current value; investment events record dated cash flows for performance.

Event types:

- `buy`: initial purchase
- `add`: additional purchase or top-up
- `sell`: redemption proceeds
- `income`: cash income or distribution received

```bash
ta investment event <account_id> \
  --type buy \
  --amount 35000 \
  --currency CNY \
  --occurred-at 2026-04-24T00:00:00+08:00 \
  --memo "initial purchase" \
  --idempotency-key investment-buy-<account-id>-20260424 \
  --json

ta investment performance <account_id> \
  --as-of 2026-05-15T00:00:00+08:00 \
  --json
```

Performance uses XIRR over dated `buy`/`add`/`sell`/`income` events plus the current confirmed account balance. If historical trade data is missing, backfill one dated `buy` event for known principal and keep the existing balance snapshot as the current value.

## Create Account

```bash
ta account create "<name>" \
  --type asset \
  --currency CNY \
  --opening-balance 0 \
  --institution-type bank \
  --subtype debit_card \
  --institution "<institution>" \
  --idempotency-key <key> \
  --json
```

For credit cards, use `--type liability --subtype credit_card`.

## Credit Card Profiles

Credit-card account balances are liabilities and represent current amount owed. Do not encode credit limits or billing dates in the account name. Use the credit-card profile API/CLI for non-ledger metadata:

```bash
ta credit-card update <credit_card_account_id> \
  --credit-limit <limit> \
  --available-credit <available> \
  --statement-day <1-31> \
  --due-day <1-31> \
  --annual-fee <fee> \
  --idempotency-key credit-card-profile-<account-id> \
  --json

ta credit-card show <credit_card_account_id> --json
ta credit-card list --json
```

Profile updates do not change balances. The overview reports the natural
liability balance, explicit outstanding and overpayment balances, recorded
limit, recorded available credit, derived available credit, and utilization
rate. Positive liability balance means amount owed; negative liability balance
means overpayment.

## Balance Snapshot

Use this when a screenshot only gives the current balance or the user says not to record spending.

**`account adjust` takes a delta, not a target.** Compute this first:

```text
delta = screenshot_balance - current_official_balance
```

For liability and credit-card screenshots, convert the provider display into
natural liability balance before computing the delta. Amount owed is positive.
Overpayment or credit balance is negative. Do not use provider signs or legacy
posting signs directly.

Then write the adjustment:

```bash
ta account balance <account_id> --json
ta account adjust <account_id> \
  --amount <delta-to-reach-screenshot-balance> \
  --currency CNY \
  --occurred-at <iso8601> \
  --purpose "<provider/account> balance snapshot from screenshot; do not record spending details" \
  --idempotency-key <key> \
  --json
ta account balance <account_id> --json
```

## Transfer Or Explicit Expense

Use `ta tx record` when source and target are clear:

```bash
ta tx record \
  --amount <amount> \
  --currency CNY \
  --from-account-id <source_account_id> \
  --to-account-id <target_account_id> \
  --purpose "<description>" \
  --occurred-at <iso8601> \
  --idempotency-key <key> \
  --json
```

For explicit fees, create or reuse an `expense` account such as `费用-手续费` with `institution_type=other` and `subtype=fee`.

## Income and Expense Categories

Categories are not preset. Create them only when the user provides a real category need. A category has `kind` (`expense` or `income`), a first-level `primary` label, and an optional second-level `secondary` label.

```bash
ta category create \
  --kind expense \
  --primary "餐饮" \
  --secondary "外卖" \
  --idempotency-key category-expense-food-delivery \
  --json

ta category find \
  --kind expense \
  --primary "餐饮" \
  --secondary "外卖" \
  --json
```

For normal spending, prefer `expense record` over creating one expense account per category:

```bash
ta expense record \
  --amount <amount> \
  --currency CNY \
  --from-account-id <payment_account_id> \
  --category-id <expense_category_id> \
  --purpose "<description>" \
  --occurred-at <iso8601> \
  --idempotency-key <key> \
  --json
```

Credit-card purchases also use `expense record` with a positive amount. If
`--from-account-id` is a credit-card liability account, the ledger credits the
liability and increases outstanding debt. Credit-card repayment is a transfer:
`ta tx record --from-account-id <source_asset> --to-account-id
<credit_card_liability>` debits the liability and decreases outstanding debt.
Do not use negative amounts or raw posting fields to force either direction.

For income:

```bash
ta income record \
  --amount <amount> \
  --currency CNY \
  --to-account-id <receiving_account_id> \
  --category-id <income_category_id> \
  --purpose "<description>" \
  --occurred-at <iso8601> \
  --idempotency-key <key> \
  --json
```

Read classified totals with:

```bash
ta summary categories --kind expense --currency CNY --json
ta summary categories --kind income --currency CNY --json
```

If the category is unknown, do not invent it. Ask the user or record a draft/snapshot instead.

## Credit Card Repayment With Fee

Liabilities use natural balance semantics: a positive credit-card balance means
amount owed, and a negative balance means overpayment. A normal
asset-to-liability transfer is the repayment flow: it credits the source asset
and debits the credit-card liability, so the debt goes down.

Repay with two writes when the fee is paid by the source asset:

1. Record the explicit fee as source asset -> fee expense.
2. Record the repayment principal with `ta tx record --from-account-id <source_asset> --to-account-id <credit_card_liability>`.

After both writes, verify:

- Source asset decreased by `principal + fee`.
- Credit-card liability decreased by `principal`.
- Fee expense increased by `fee`.

Do not use negative signs to force credit-card direction. Use the account types:
payment to a liability reduces the liability; purchase from a liability
increases the liability.

## Summaries

Read aggregates with `ta summary`, not SQL:

```bash
ta summary accounts --group-by institution --currency CNY --json
ta summary accounts --group-by subtype --currency CNY --json
ta summary accounts --group-by currency --json
```

Summary rows expose `amount`, `asset_amount`, `liability_amount`, and `net_amount`.

- Use `asset_amount` for total assets.
- Use `liability_amount` for amount owed.
- Use `net_amount` for net-worth-style reporting.

NEVER sum across currencies. If the user supplies FX rates and requests conversion, do it explicitly and label the result.

## API Handling

Health check:

```bash
curl -s http://127.0.0.1:8000/api/v1/health
```

If a CLI call fails with connection refused, start the API:

```bash
uv run uvicorn track_anywhere.api:app --app-dir backend/app --host 127.0.0.1 --port 8000
```

If the user asked to keep the API running, leave it running after the task.

## Final Report Template

```text
Recorded: <what changed>
Backup: <backup path>
Accounts: <account ids and verified balances>
Transactions: <transaction ids>
Verification: <commands/results checked>
Notes: <uncertainty, SQL usage, API status if relevant>
```
