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
- Treat screenshots, OCR, and spoken input as uncertain. If a credit-card write cannot be mapped to a typed charge, payment, refund, fee, or exact reversal, do not write it.
- Verify affected balances and transactions before reporting success.
- If the user asked to keep the API running, do not stop it.

## Start Here

```bash
ta --help
ta account --help
ta card --help
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
ta account create <book_id> <credit_card_account_id> \
  --asset-code CNY \
  --type liability \
  --account-subtype credit_card \
  --name "<card name>" \
  --json

ta card charge <book_id> <transaction_id> \
  --command-id <command_id> \
  --card-account-id <credit_card_account_id> \
  --expense-account-id <expense_account_id> \
  --asset-code CNY \
  --amount <positive_amount> \
  --effective-at <iso8601> \
  --idempotency-key <key> \
  --json

ta card payment <book_id> <transaction_id> \
  --command-id <command_id> \
  --card-account-id <credit_card_account_id> \
  --source-account-id <asset_account_id> \
  --asset-code CNY \
  --amount <positive_amount> \
  --effective-at <iso8601> \
  --idempotency-key <key> \
  --json

ta card refund <book_id> <transaction_id> \
  --command-id <command_id> \
  --card-account-id <credit_card_account_id> \
  --original-transaction-id <typed_charge_transaction_id> \
  --asset-code CNY \
  --amount <positive_amount> \
  --effective-at <iso8601> \
  --idempotency-key <key> \
  --json

ta card fee <book_id> <transaction_id> \
  --command-id <command_id> \
  --card-account-id <credit_card_account_id> \
  --expense-account-id <fee_expense_account_id> \
  --asset-code CNY \
  --amount <positive_amount> \
  --effective-at <iso8601> \
  --idempotency-key <key> \
  --json
```

## High-Risk Rules

### Balance Snapshots

The V2 credit-card contract has no generic balance-adjust command. A provider
screenshot is evidence for reconciliation, not permission to synthesize a
journal entry. Read the Book balance and normalize the display mentally:
amount owed is a positive natural liability balance, while an overpayment or
credit balance is negative.

If the difference can be proven as a typed charge, payment, refund, or fee,
record that fact with the matching `ta card` command. If only a target balance
is known, stop and report that reconciliation/import support is deferred. Do
not force the target with raw posting sides or a generic card adjustment.

### Credit Card Repayment

Liabilities use natural balance semantics: a positive credit-card balance means
amount owed, and a negative balance means overpayment. A normal
asset-to-liability transfer is the repayment flow: it credits the source asset
and debits the credit-card liability, so the debt goes down.

For a repayment and a card-billed fee:

1. Record the repayment principal with `ta card payment`, using the paying asset as `--source-account-id`.
2. Record a fee charged to the card with `ta card fee`, using its expense account as `--expense-account-id`.
3. Verify the source asset, card liability, and fee expense through Book balances and journal results.

All amounts are positive. The service owns the directions: payment is Dr Card /
Cr Asset, while a card fee is Dr Expense / Cr Card. Do not replace either with
manual debit/credit fields.

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

Create every credit card as an explicit liability subtype:

```bash
ta account create <book_id> <credit_card_account_id> \
  --asset-code <ASSET_CODE> \
  --type liability \
  --account-subtype credit_card \
  --name "<card name>" \
  --json
```

`account_subtype` uses lowercase slugs. For the semantic card commands, the
account must have exactly `account_type=liability` and
`account_subtype=credit_card`.

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
ta income record --amount <amount> --to-account-id <target> --category-id <category_id> --purpose "<description>" --idempotency-key <key> --json
ta summary categories --kind expense --currency CNY --json
```

Credit-card purchases, repayments, refunds, and fees all use positive amounts:

- purchase: `ta card charge` with the expense account;
- repayment: `ta card payment` with the source asset account;
- partial or full merchant refund: `ta card refund` linked to the original typed charge;
- card-billed fee or interest: `ta card fee` with the fee/interest expense account.

The service generates the debit/credit legs. Never use a generic expense,
transfer, raw posting, or balance-adjust command to imitate a card operation.
To replace an incorrect card fact, exact-reverse it and record a new typed card
transaction. A refund can point only at a typed V2 charge event.

SafePal USD/USD24 profile automation and automatic backing settlement are not
part of the V2 card ledger contract. Do not assume `1 USD = 1 USD24`, invent a
top-up, or call a payment-profile command. Record only facts that fit the typed
card commands; provider-specific settlement needs a separately approved design.

## Deferred Credit-Card Product Layer

Credit-card balances are natural liability balances: positive means current
amount owed and negative means overpayment. The V2 ledger does not yet expose a
card profile, credit limit, available credit, statement close date, payment
deadline/due date, minimum due, statement-item matching, or reconciliation
surface.

Treat all of these as deferred. Do not put them in the account name, emulate
them with generic journal adjustments, or call removed profile commands. They
require a separate read model/aggregate and an explicitly approved rollout.

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
