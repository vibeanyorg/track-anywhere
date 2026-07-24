# Everyday Entry Gateway Implementation Decisions

This log records integration decisions discovered while implementing the frozen
design and worktree plan. It may clarify seams but must not weaken or silently
replace either locked reference document.

## D001: Prepared intent actor scope

**Status:** Accepted
**Date:** 2026-07-24

Prepared intents and source references are repository-scoped by
`book_id + actor_subject_id`. The public commit payload remains exactly
`intent_id + commit_token + request_id`; the authenticated actor is bound by the
request-scoped application service and is never supplied as untrusted payload.

Commit lookup and claim must include Book, actor, and intent identity. An intent
prepared by one actor cannot be observed, cancelled, or committed by another.

## D002: Everyday source references are a separate persistence concept

**Status:** Accepted
**Date:** 2026-07-24

Strong provider/order/import deduplication uses an Everyday Entry source
reference table and repository contract. It does not broaden or overload the
existing immutable-ledger financial external-reference semantics.

The dedicated source-reference uniqueness scope is Book, provider, reference
kind, and normalized protected value/digest as defined by the storage lane.

## D003: Non-card refunds remain an explicit transaction kind

**Status:** Accepted
**Date:** 2026-07-24

The implementation retains the design requirement for an explicit non-credit-
card `refund` transaction kind. It is not downgraded to an unsupported result.
The core lane owns the domain/compiler and event serialization contract; the
storage lane owns the PostgreSQL projection constraints and migration support.

Credit-card refunds continue to use the existing typed credit-card relation.

## D004: Refund reporting sign is defined by semantic transaction kind

**Status:** Accepted
**Date:** 2026-07-24

The monthly reporting projection treats a non-card journal transaction whose
kind is `refund` as a negative expense contribution. The storage lane owns the
database constraint, focused monthly-summary projection change, and the
PostgreSQL/replay tests as one coherent persistence update. The core lane owns
only the domain/compiler and serialization-facing pure tests.

Typed `credit_card_refund` keeps its existing relation-based negative sign. The
implementation must not apply both mechanisms to the same event.

## D005: Non-card refund relationship is an immutable event fact

**Status:** Accepted
**Date:** 2026-07-24

`JournalTransactionPosted` carries an optional `original_transaction_id`.
Domain validation requires the field for `refund`, forbids it for other
transaction kinds, and rejects self-reference. The identifier is a non-sensitive
accounting relationship and belongs in the immutable event; merchant, memo,
provider reference, and other narrative data remain excluded.

The read model derives the non-card refund link from the stored source event.
No mutable ORM relationship column is required.
