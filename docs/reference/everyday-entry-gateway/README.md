# Everyday Entry Gateway Reference

This directory is the version-controlled source of truth for the Everyday Entry
Gateway program.

Required context for every implementation, review, integration, and migration
task:

1. Read `design-2026-07-24.md` completely.
2. Read `parallel-worktree-plan-2026-07-24.md` completely.
3. Read the nearest applicable `AGENTS.md` before changing code.
4. State the assigned branch, owned files, dependencies, and acceptance gate
   before editing.
5. Do not weaken the invariants in the design to make an adapter or test easier.

The integration branch owns these reference documents. Feature branches may
propose corrections, but must not independently redefine the contract.

## Non-negotiable invariants

- External callers submit business facts, never debit/credit postings, internal
  clearing accounts, category versions, or canonical ledger units.
- A normal expense and its reporting classification commit atomically.
- Bare numeric amounts are asset-unit amounts unless `minor_unit` is explicit.
- Prepared intents are short-lived mutable workflow state, not ledger events.
- Commit accepts only the intent identity, opaque token, and request identity.
- Credit-card payments are balance-sheet transfers and have no expense category.
- Refunds preserve an auditable relationship to the original transaction.
- Sensitive narrative remains in encrypted protected-content storage.
- Corrections use reversal and replacement; immutable history is not edited.

