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

