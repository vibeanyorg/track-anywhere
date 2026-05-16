# Track Anywhere Ledger Runbook

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
ta tx list --account-id <account_id> --limit 10 --json
ta tx show <transaction_id> --json
ta summary accounts --group-by institution --currency CNY --json
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

## Balance Snapshot

Use this when a screenshot only gives the current balance or the user says not to record spending.

**`account adjust` takes a delta, not a target.** Compute this first:

```text
delta = screenshot_balance - current_official_balance
```

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

## Credit Card Repayment With Fee

Liabilities are positive amounts owed, so a normal asset-to-liability transfer would increase the debt. Repay with three separate writes:

1. Record the explicit fee as source asset -> fee expense.
2. Decrease the source asset by the repayment principal with `account adjust`.
3. Decrease the credit-card liability by the same repayment principal with `account adjust`.

After all three writes, verify:

- Source asset decreased by `principal + fee`.
- Credit-card liability decreased by `principal`.
- Fee expense increased by `fee`.

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
