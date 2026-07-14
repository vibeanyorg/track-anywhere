---
name: track-anywhere-ledger
description: "Operate the local Track Anywhere V2 event ledger through its supported ta CLI and HTTP API."
---

# Track Anywhere V2 Ledger

Run from the repository root. Resolve the API URL from an explicit `--base-url`,
then `TRACK_ANYWHERE_API`, then `TRACK_ANYWHERE_SERVICE_URL`, and otherwise use
`http://localhost:8000`.

## Mandatory discovery

The V2 CLI intentionally rejects retired command groups. Before planning a
workflow, inspect the live command contract:

```bash
ta capabilities --json
ta schema --json
ta <group> <command> --help
```

Use only commands advertised by `ta capabilities`. Never infer that an old
capture, recurring, payment-profile, attachment, or backup command still has a
network implementation.

## Write rules

- Never write ledger or projection tables directly.
- Pass `--json` or `--agent` for machine-readable operation.
- Pass an explicit stable idempotency key for every financial command and reuse
  it for a retry of the same logical request.
- Send amounts as exact decimal strings; never use floats or signed values to
  imply debit/credit direction.
- Use explicit Book, account, asset, transaction, posting, and category IDs.
- Do not invent missing merchants, categories, accounts, assets, timestamps, or
  posting direction. Ask the user when a required fact is uncertain.
- After a write, re-read the transaction and affected Book balances from a fresh
  request before reporting success.

## Supported workflow

1. Check `ta system health --json` and `ta system ready --json`.
2. Check `ta auth status --json`; authenticate only when needed.
3. Run `ta capabilities --json` and command-specific help.
4. Read current journal/balances with `ta tx list`, `ta book balances`, or
   `ta book reporting-lines`.
5. Execute a supported Book/asset/account/category, journal,
   reversal/correction, classification, FX, or investment-lot command with a
   stable idempotency key.
6. Re-read affected facts and report IDs, exact units/amounts, and any remaining
   uncertainty.

If readiness fails, do not fall back to another database or an older API. Fix
the local PostgreSQL 17 V2 runtime or report the blocker.

Reference: [references/ledger-runbook.md](references/ledger-runbook.md)
