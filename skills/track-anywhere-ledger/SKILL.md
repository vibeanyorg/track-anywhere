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
4. Write with `ta account create`, `ta account adjust`, `ta tx record`, or draft commands.
5. Verify affected balances and transactions with CLI reads.
6. Report backup path, account IDs, transaction IDs, verified balances, and unresolved uncertainty.

## Runbook Topics

- concrete command examples
- account taxonomy
- balance snapshots from screenshots
- credit-card repayment and fee handling
- summaries and multi-currency rules
- API health and startup handling

Reference: [references/ledger-runbook.md](references/ledger-runbook.md)
