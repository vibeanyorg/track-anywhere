# Hermes/OpenClaw Agent Guide

This guide is the operating prompt for Hermes/OpenClaw agents that manage the local Track Anywhere ledger through the `ta` CLI.

## OpenCLI Pattern Adopted

`jackwener/opencli` treats agent usability as a command contract, not as a hidden prompt. The useful patterns to follow here are:

- Discover commands from the CLI itself. Use `ta --help`, `ta account --help`, and subcommand help before assuming syntax.
- Prefer structured output. Pass `--json` whenever the command supports it.
- Separate read and write paths. Reads can run directly; writes require backup, idempotency, and verification.
- Keep examples canonical. A good agent command is copyable, explicit, and includes the account IDs, timestamp, amount, currency, and purpose.
- Make failures recoverable. Reuse the same idempotency key after a timeout, inspect the returned transaction/account IDs, then verify state.

## Hard Rules

- This ledger contains real personal financial data. Never commit `.local/`, SQLite databases, backups, screenshots, tokens, or raw private exports.
- Do not write directly to SQLite. Use `ta` or the HTTP API for all mutations. Read-only SQL is allowed only when a required CLI read command is missing, and the final report must say why.
- Before any mutation, create a backup:

```bash
ta data backup --label before-<short-change-name> --json
```

- Use a stable `--idempotency-key` for every write. Use a human-readable pattern such as `balance-update-wechat-lqt-20260516-220924`.
- Treat OCR, screenshots, and oral descriptions as uncertain. When details are incomplete, create a draft or record a balance snapshot; do not invent merchants, categories, or consumption details.
- After every write, verify with `ta account balance ... --json`, `ta tx show ... --json`, `ta tx list ... --json`, or `ta summary accounts ... --json`.
- Keep the API running if it is already running. If it is down and a CLI command needs it, start it and leave it running:

```bash
uv run uvicorn track_anywhere.api:app --app-dir backend/app --host 127.0.0.1 --port 8000
```

## Command Discovery

Start each unfamiliar task with the command surface:

```bash
ta --help
ta account --help
ta tx --help
ta summary --help
```

Useful read commands:

```bash
ta account list --json
ta account find --name <text> --currency CNY --json
ta account show <account_id> --json
ta account balance <account_id> --json
ta tx list --account-id <account_id> --limit 10 --json
ta tx show <transaction_id> --json
ta summary accounts --group-by institution --currency CNY --json
```

Useful write commands:

```bash
ta account create "<name>" --type asset --currency CNY --institution-type bank --subtype debit_card --institution "<institution>" --idempotency-key <key> --json
ta account adjust <account_id> --amount <delta> --currency CNY --purpose "<why>" --occurred-at <iso8601> --idempotency-key <key> --json
ta tx record --amount <amount> --currency CNY --from-account-id <source> --to-account-id <target> --purpose "<why>" --occurred-at <iso8601> --idempotency-key <key> --json
```

## Account Taxonomy

Use accounting `type` for ledger direction:

| Type | Meaning |
| --- | --- |
| `asset` | Cash, bank balances, wallets, investments, crypto balances. |
| `liability` | Credit cards, loans, payable balances. Store amount owed as positive. |
| `expense` | Fees and spending categories when an expense is explicitly known. |
| `equity` | Opening balance / balancing accounts. Usually system-owned. |
| `system` | Internal adjustment accounts. Do not present as real assets. |

Use `institution_type` for provider category:

| Institution Type | Examples |
| --- | --- |
| `bank` | 中国银行, 工商银行, 交通银行, 广发银行 |
| `e_wallet` | 微信零钱, 微信零钱通, 支付宝余额宝 |
| `fintech` | Wise, Rippling |
| `brokerage` | 雪球, 证券账户 |
| `crypto_wallet` | SafePal, on-chain token balances |
| `cash` | Physical cash |
| `other` | Fees or uncategorized operational accounts |

Use lowercase `subtype` slugs for product shape: `debit_card`, `credit_card`, `checking`, `ewallet_cash`, `ewallet_money_market`, `money_market`, `wealth_management`, `fund`, `multicurrency_wallet`, `payroll_balance`, `crypto_token`, `fee`.

Model each account as single-currency or single-asset. Wise should have separate `Wise USD`, `Wise EUR`, `Wise CNY` accounts. Crypto should use separate token/network accounts such as `SafePal USDC (Arbitrum)`.

## Common Workflows

### Balance Snapshot

Use this for screenshots that show only current balance, or when the user says not to record spending details.

```bash
ta data backup --label before-<account>-snapshot --json
ta account find --name <tail-or-provider> --currency CNY --json
ta account adjust <account_id> --amount <delta-to-reach-screenshot-balance> --currency CNY --occurred-at <iso8601> --purpose "<provider/account> balance snapshot; source screenshot; do not record spending details" --idempotency-key <key> --json
ta account balance <account_id> --json
```

`account adjust` takes a delta, not a target balance. Always compute the delta from the current official balance.

### New Account From Screenshot

```bash
ta data backup --label before-create-<provider>-<tail> --json
ta account create "<provider><product>(<tail>)" --type asset --currency CNY --opening-balance 0 --institution-type bank --subtype debit_card --institution "<provider>" --idempotency-key <key> --json
ta account adjust <new_account_id> --amount <balance> --currency CNY --purpose "<provider><product>(<tail>) opening balance from screenshot" --idempotency-key <key> --json
ta account balance <new_account_id> --json
```

For credit cards, use `--type liability --subtype credit_card`; positive balance means amount owed.

### Simple Transfer Or Expense

Use `ta tx record` when there is a clear source and target. Examples: wallet to fee expense, bank to cash, Rippling to Wise.

```bash
ta tx record --amount 42.70 --currency CNY --from-account-id <source> --to-account-id <target> --purpose "<description>" --occurred-at <iso8601> --idempotency-key <key> --json
```

For a known fee, create or reuse an `expense` account such as `费用-手续费`.

### Credit Card Repayment With Fee

Current CLI transaction semantics are asset-to-target. Because credit card liabilities are stored as positive amounts owed, use this safe three-step pattern:

1. Record the explicit fee as an expense transfer from the payment source.
2. Decrease the payment source by the repayment principal with `account adjust`.
3. Decrease the credit card liability by the same repayment principal with `account adjust`.

Verify that the source decreased by principal plus fee, and the liability decreased by principal.

### Summaries

Use summaries for reporting, not direct SQL:

```bash
ta summary accounts --group-by institution --currency CNY --json
ta summary accounts --group-by subtype --currency CNY --json
ta summary accounts --group-by currency --json
```

Summary rows expose `amount`, `asset_amount`, `liability_amount`, and `net_amount`. Use:

- `asset_amount` for total assets.
- `liability_amount` for amount owed.
- `net_amount` for net-worth style reporting.

Do not add different currencies together unless an explicit FX rate source is provided.

## Verification Checklist

Before reporting success:

- Backup path captured.
- All write commands returned `idempotent_replay: false` or an intentional replay.
- Every created account ID and transaction ID is recorded.
- Account balances were re-read from the CLI.
- Any uncertainty from OCR/screenshot is stated.
- API health is checked if the API was used:

```bash
curl -s http://127.0.0.1:8000/api/v1/health
```

## Final Response Contract

Keep final reports short and factual:

- State what was recorded.
- Include backup path.
- Include account IDs and transaction IDs.
- Include verified balances.
- Mention if a direct SQL read was used and why.
- Mention API status if the user asked to keep it running.
