---
name: track-anywhere-ledger
description: "Use the local Track Anywhere `ta` CLI for personal accounting tasks: creating accounts, recording balance snapshots, recording transfers or expenses, confirming screenshot/OCR-derived financial data, checking balances, generating summaries, and operating Hermes/OpenClaw-style ledger agents safely. Trigger when the user asks to 记账, 查账, 创建账户, 更新余额, record card/account/wallet/brokerage/crypto balances, process bank or wallet screenshots, or manage the local Track Anywhere ledger."
---

# Track Anywhere Ledger

Use this skill to operate the local Track Anywhere ledger through the `ta` CLI.

Run commands from the Track Anywhere project root. Prefer `TRACK_ANYWHERE_ROOT` when it is set:

```bash
cd "${TRACK_ANYWHERE_ROOT:?Set TRACK_ANYWHERE_ROOT to the local track-anywhere repo}"
```

## Core Rules

- Use `ta` or the HTTP API for mutations. Do not write directly to SQLite.
- Prefer `--json` for all agent workflows.
- Before any mutation, run `ta data backup --label before-<change> --json`.
- Use stable `--idempotency-key` values on every write.
- Treat screenshots/OCR/oral input as uncertain. If details are incomplete, create a draft or record a balance snapshot; do not invent merchants or categories.
- Verify every write with CLI reads before reporting success.
- Keep a running local API alive if the user asked for it; do not stop it.

## Workflow

1. Discover syntax with `ta --help` and relevant subcommand help.
2. Read current accounts/balances with `ta account find/list/show/balance --json`.
3. Back up before writes.
4. Write via `ta account create`, `ta account adjust`, `ta tx record`, or draft commands.
5. Verify affected balances/transactions.
6. Report backup path, account IDs, transaction IDs, verified balances, and any uncertainty.

## Detailed Reference

Read [references/ledger-runbook.md](references/ledger-runbook.md) when you need concrete command examples, account taxonomy, credit-card repayment handling, fee handling, summaries, or screenshot balance-snapshot patterns.
