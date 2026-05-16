# ADR 0001: Draft-First Capture With Strict Confirmed Ledger

## Decision

Use draft-first capture for low-friction input and a strict double-entry-inspired ledger for confirmed financial truth.

## Drivers

- Daily capture must be fast enough for personal use.
- Confirmed balances must be trustworthy and derivable from postings.
- Agent/OCR inputs are useful but uncertain and must not silently create authoritative facts.
- Personal financial data requires audit, rollback/reversal, and default-on redaction.

## Consequences

- Draft and confirmed state must stay visibly separate.
- The command governance layer is part of the core architecture.
- Security, idempotency, optimistic concurrency, and audit are implemented before broad Agent/OCR expansion.

