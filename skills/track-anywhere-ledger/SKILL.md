---
name: track-anywhere-ledger
description: "Use the local Track Anywhere `ta` CLI for personal accounting tasks: create accounts, record balance snapshots, record transfers or expenses, confirm screenshot/OCR-derived financial data, check balances, generate summaries, and operate Hermes/OpenClaw-style ledger agents safely. Trigger when the user asks to 记账, 查账, 创建账户, 更新余额, record card/account/wallet/brokerage/crypto balances, process bank or wallet screenshots, or manage the local Track Anywhere ledger."
---

# Track Anywhere Ledger

Run commands from the Track Anywhere repo root. If `TRACK_ANYWHERE_ROOT` is set, move there first:

```bash
if [ -n "${TRACK_ANYWHERE_ROOT:-}" ]; then
  cd "$TRACK_ANYWHERE_ROOT"
fi
```

## Service Address

Resolve the API base URL before running CLI/API commands:

1. Use `--base-url` when the user explicitly gives one.
2. Otherwise use `TRACK_ANYWHERE_API` if set.
3. Otherwise use `TRACK_ANYWHERE_SERVICE_URL` if set.
4. For Docker local dev, read `TRACK_ANYWHERE_SERVICE_URL` from `deploy/env/dev.env`.
5. For Docker production/VPS, read it from `deploy/env/prod.env` on the host.
6. Fall back to `http://localhost:8000`.

Do not hardcode the VPS address in ledger commands. Export the resolved value:

```bash
export TRACK_ANYWHERE_API="${TRACK_ANYWHERE_API:-${TRACK_ANYWHERE_SERVICE_URL:-http://localhost:8000}}"
```

## Core Rules

- NEVER write to SQLite directly. Use `ta` or the HTTP API.
- ALWAYS pass `--json` when the command supports it.
- Before every write, run `ta data backup --label before-<change> --json`.
- Pass a stable `--idempotency-key` on every write. Reuse the same key on retry.
- Screenshots, OCR, and spoken input are uncertain. When details are missing, record a balance snapshot or a draft. NEVER invent merchants, amounts, or categories.
- After every write, read the affected accounts and transactions. Report only verified results.
- If the user asked to keep the API running, do not stop it at the end of the task.

## Workflow

1. Discover syntax with `ta --help` and relevant subcommand help.
2. Read current state with `ta account find/list/show/balance --json` or `ta tx list/show --json`.
3. Back up with `ta data backup --label before-<change> --json`.
4. Write with `ta account create`, `ta account adjust`, `ta category create`, `ta expense record`, `ta income record`, `ta credit-card update`, `ta tx record`, `ta investment event`, or draft commands.
5. Verify affected balances and transactions with CLI reads.
6. Report backup path, account IDs, transaction IDs, verified balances, and unresolved uncertainty.

For wealth-management, money-market, or fund holdings where annualized return matters, record dated `ta investment event` cash flows (`buy`, `add`, `sell`, `income`) and verify with `ta investment performance <account_id> --json`. Do not rely only on account names or balance-snapshot free text for principal and holding period.

## Runbook Topics

- concrete command examples
- account taxonomy
- balance snapshots from screenshots
- investment holding events and annualized-return queries
- income/expense categories and category summaries
- credit-card repayment and fee handling
- credit-card limits and billing profile metadata
- summaries and multi-currency rules
- API health and startup handling

Reference: [references/ledger-runbook.md](references/ledger-runbook.md)
