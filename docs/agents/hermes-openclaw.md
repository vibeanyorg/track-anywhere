# Hermes/OpenClaw Agent Guide

Use this guide when a Hermes/OpenClaw agent operates a local Track Anywhere checkout through the `ta` CLI.

The canonical in-repo skill lives at:

```text
skills/track-anywhere-ledger
```

Keep agent guidance in this repository so it evolves with the `ta` CLI and API.

## Operating Contract

- NEVER write to SQLite directly. Use `ta` or the HTTP API.
- ALWAYS request JSON when the command supports it.
- Back up before every write.
- Pass a stable idempotency key on every write and reuse it on retry.
- Treat screenshots, OCR, and spoken input as uncertain. Record a draft or balance snapshot when details are incomplete.
- Verify affected balances and transactions before reporting success.
- If the user asked to keep the API running, do not stop it.

## Start Here

```bash
ta --help
ta account --help
ta tx --help
ta summary --help
```

Read current state:

```bash
ta account list --json
ta account find --name <text> --currency CNY --json
ta account show <account_id> --json
ta account balance <account_id> --json
ta tx list --account-id <account_id> --limit 10 --json
ta tx show <transaction_id> --json
```

Back up before a write:

```bash
ta data backup --label before-<short-change-name> --json
```

Write through CLI commands:

```bash
ta account create "<name>" --type asset --currency CNY --institution-type bank --subtype debit_card --institution "<institution>" --idempotency-key <key> --json
ta account adjust <account_id> --amount <delta> --currency CNY --purpose "<why>" --occurred-at <iso8601> --idempotency-key <key> --json
ta tx record --amount <amount> --currency CNY --from-account-id <source> --to-account-id <target> --purpose "<why>" --occurred-at <iso8601> --idempotency-key <key> --json
```

## High-Risk Rules

### Balance Snapshots

`ta account adjust` takes a delta, not a target balance.

```text
delta = screenshot_balance - current_official_balance
```

Use snapshots when the user provides only a current balance or explicitly says not to record spending details.

### Credit Card Repayment

Liabilities are positive amounts owed. A normal asset-to-liability transfer would increase the debt.

For repayment with a fee:

1. Record the fee as source asset -> fee expense.
2. Decrease the source asset by the repayment principal with `account adjust`.
3. Decrease the credit-card liability by the same principal with `account adjust`.
4. Verify source, liability, and fee balances.

### Multi-Currency Summaries

Do not sum across currencies unless the user supplies FX rates and explicitly asks for conversion.

Use:

```bash
ta summary accounts --group-by currency --json
ta summary accounts --group-by institution --currency CNY --json
```

## Account Taxonomy

Choose `type` from ledger direction:

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

## Income and Expense Categories

Income and expense categories are explicit user-created records. Do not seed or invent defaults. Create a category only when the user provides a real first-level label and optional second-level label:

```bash
ta category create \
  --kind expense \
  --primary "餐饮" \
  --secondary "外卖" \
  --idempotency-key category-expense-food-delivery \
  --json
```

For day-to-day spending and income, prefer the category-aware helpers:

```bash
ta expense record --amount <amount> --from-account-id <source> --category-id <category_id> --purpose "<description>" --idempotency-key <key> --json
ta expense record --payment safepal --amount <amount> --currency USD --category-id <category_id> --purpose "<description>" --idempotency-key <key> --json
ta income record --amount <amount> --to-account-id <target> --category-id <category_id> --purpose "<description>" --idempotency-key <key> --json
ta summary categories --kind expense --currency CNY --json
```

For SafePal Card USD backed by SafePal USD24, use `--payment safepal` instead of manually creating a card top-up or balance adjustment. The payment-profile expense command records both the USD expense and the immediate USD24 backing settlement in one confirmed transaction. Check the user-facing SafePal balance with:

```bash
ta payment profile status safepal --json
```

The first version treats `1 USD = 1 USD24`. Do not ask the user to manually clear a negative SafePal card balance when the payment profile exists.

## Credit Card Profiles

Credit-card balances are liabilities and mean current amount owed. Record non-ledger metadata such as limit, available credit, statement day, due day, and annual fee through the profile surface:

```bash
ta credit-card update <credit_card_account_id> --credit-limit <limit> --statement-day <day> --due-day <day> --idempotency-key <key> --json
ta credit-card show <credit_card_account_id> --json
```

Do not put limits or due dates in the account name.

## Investment Events

For bank wealth-management, money-market, and fund accounts, use account balances for current value and `ta investment event` for dated cash flows. This preserves holding time, additional purchases, redemptions, and income so `ta investment performance` can compute holding days and money-weighted annualized return.

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

Use `buy` for initial principal, `add` for top-ups, `sell` for redemption proceeds, and `income` for cash distributions. Do not encode these only in account names or free-text balance snapshots when annualized return matters.

## Final Response Template

```text
Recorded: <what changed>
Backup: <backup path>
Accounts: <account ids and verified balances>
Transactions: <transaction ids>
Verification: <commands/results checked>
Notes: <uncertainty, SQL usage, API status if relevant>
```
