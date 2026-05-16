# Track Anywhere Ledger Runbook

## Command Discovery

Use the CLI as source of truth:

```bash
ta --help
ta account --help
ta tx --help
ta summary --help
```

Use JSON whenever supported:

```bash
ta account list --json
ta account find --name <text> --currency CNY --json
ta account show <account_id> --json
ta account balance <account_id> --json
ta tx list --account-id <account_id> --limit 10 --json
ta tx show <transaction_id> --json
ta summary accounts --group-by institution --currency CNY --json
```

## Safety

This ledger contains real personal financial data.

- Never commit `.local/`, SQLite databases, backups, screenshots, tokens, or raw private exports.
- Do not mutate without backup:

```bash
ta data backup --label before-<short-change-name> --json
```

- If a command times out after reaching the API, retry with the same idempotency key before trying a different write.
- Read-only SQL is allowed only when a required CLI read command is missing; report that explicitly.

## Account Taxonomy

Accounting `type`:

| Type | Meaning |
| --- | --- |
| `asset` | Cash, bank balances, wallets, investments, crypto balances. |
| `liability` | Credit cards, loans, payable balances. Positive amount means amount owed. |
| `expense` | Fees and spending categories when explicitly known. |
| `equity` | Opening-balance or balancing accounts. Usually system-owned. |
| `system` | Internal adjustment accounts. Do not present as real assets. |

Provider `institution_type`:

| Institution Type | Examples |
| --- | --- |
| `bank` | 中国银行, 工商银行, 交通银行, 广发银行 |
| `e_wallet` | 微信零钱, 微信零钱通, 支付宝 |
| `fintech` | Wise, Rippling |
| `brokerage` | 雪球, 证券账户 |
| `crypto_wallet` | SafePal and on-chain token balances |
| `cash` | Physical cash |
| `other` | Fees or uncategorized operational accounts |

Common `subtype` slugs: `debit_card`, `credit_card`, `checking`, `ewallet_cash`, `ewallet_money_market`, `money_market`, `wealth_management`, `fund`, `multicurrency_wallet`, `payroll_balance`, `crypto_token`, `fee`.

Model each account as one currency or asset. Wise should be separate `Wise USD`, `Wise EUR`, `Wise CNY` accounts. Crypto should be separate token/network accounts such as `SafePal USDC (Arbitrum)`.

## Common Writes

### Create Account

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

### Balance Snapshot

Use this when a screenshot only gives the current balance or the user says not to record spending.

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

`account adjust` takes a delta, not a target balance. Compute `delta = screenshot_balance - current_official_balance`.

### Transfer Or Explicit Expense

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

### Credit Card Repayment With Fee

Because liabilities are stored as positive amount owed, do not use a normal asset-to-liability transfer for repayment. Use three writes:

1. Record the explicit fee as source asset -> fee expense.
2. Decrease the source asset by the repayment principal with `account adjust`.
3. Decrease the credit-card liability by the same repayment principal with `account adjust`.

Verify:

- Source asset decreased by `principal + fee`.
- Credit-card liability decreased by `principal`.
- Fee expense increased by `fee`.

## Summaries

Use summaries instead of SQL:

```bash
ta summary accounts --group-by institution --currency CNY --json
ta summary accounts --group-by subtype --currency CNY --json
ta summary accounts --group-by currency --json
```

Summary rows expose `amount`, `asset_amount`, `liability_amount`, and `net_amount`.

- Use `asset_amount` for total assets.
- Use `liability_amount` for amount owed.
- Use `net_amount` for net-worth-style reporting.
- Do not add different currencies together unless the user gives explicit FX rates and asks for conversion.

## API Handling

Check API health when needed:

```bash
curl -s http://127.0.0.1:8000/api/v1/health
```

If the API is down and the CLI needs it:

```bash
uv run uvicorn track_anywhere.api:app --app-dir backend/app --host 127.0.0.1 --port 8000
```

If the user asked to keep the API running, leave it running after the task.

## Final Report

Report concisely:

- Backup path.
- What was recorded.
- Account IDs and transaction IDs.
- Verified balances.
- Any direct SQL read and why it was necessary.
- API status when relevant.
