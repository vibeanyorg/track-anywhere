# Everyday Entry Gateway Implementation Decisions

This log records integration decisions discovered while implementing the frozen
design and worktree plan. It may clarify seams but must not weaken or silently
replace either locked reference document.

## D001: Prepared intent actor scope

**Status:** Accepted
**Date:** 2026-07-24

Prepared intent lookup, claim, cancellation, and payload access are repository-
scoped by `book_id + actor_subject_id + intent_id`. The public commit payload
remains exactly `intent_id + commit_token + request_id`; the authenticated actor
is bound by the request-scoped application service and is never supplied as
untrusted payload.

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
Duplicate lookup is Book-wide so another authorized actor cannot submit the same
source twice. It returns only minimal duplicate evidence and never exposes the
other actor's prepared payload, token, or narrative.

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

## D006: REST prepare and commit paths

**Status:** Accepted
**Date:** 2026-07-24

The adapter contract uses:

- `POST /api/v2/books/{book_id}/entries/prepare`, whose body is the
  discriminated entry input;
- `POST /api/v2/books/{book_id}/entries/commit`, whose body is exactly
  `intent_id + commit_token + request_id`.

Commit also sends the same `request_id` as the existing idempotency header.
Prepare does not invent a second request identifier outside the frozen entry
contract.

## D007: Source text is protected narrative, not prepared payload

**Status:** Accepted
**Date:** 2026-07-24

Every original `MoneyInput.source_text` and `BalanceInput.source_text` is
retained for consistency audit inside encrypted `transaction_narrative_v2` as a
typed, deterministic field-path-to-text tuple. Text is private and redacted from
`repr`; field paths are bounded non-sensitive schema coordinates. Neither is
stored in the prepared intent's canonical JSON, immutable ledger events, logs,
errors, or tokens.

The prepared canonical payload stores only validated, resolved, non-sensitive
compiler inputs. Commit restores each exact source text to its matching field
path before recompilation. It must reject a missing, duplicate, or unexpected
path and must not copy one primary text across split allocations, invent a
replacement marker, or silently discard an original audit fact.

## D008: Duplicate detection uses a separate stable secret

**Status:** Accepted
**Date:** 2026-07-24

Duplicate HMACs use a dedicated stable secret loaded from
`TRACK_ANYWHERE_DUPLICATE_DETECTION_KEY_FILE` through a narrow provider. The
provider exposes only domain-separated external-reference and source-fingerprint
digest operations; adapters do not receive or log raw key bytes.

The secret is not derived from the protected-content active key because content
key rotation would make existing duplicate evidence unsearchable. Secret files
must reject symlinks, non-regular files, unsafe group/other permissions,
non-canonical encoding, undersized keys, and oversized input. Missing or invalid
configuration fails closed.

This version does not support in-place duplicate-key rotation. Future rotation
requires a persisted key reference plus a reviewed dual-read/backfill/dual-write
migration.

## D009: Duplicate provider is optional at app composition, required by Gateway

**Status:** Accepted
**Date:** 2026-07-24

`RuntimeDependencies` carries an optional `DuplicateDetectionKeyProvider`.
Runtime composition loads it only when the dedicated environment variable is
present and passes the provider through the app and V2 router composition roots.

An installation without the secret can still start and serve existing non-
Gateway APIs, but Everyday Entry prepare fails closed with a stable unavailable
response. If the environment variable is present but its secret file is
invalid, runtime construction fails closed without exposing the path or key.

## D010: Last-four hints derive from the account display name

**Status:** Accepted
**Date:** 2026-07-24

The initial Gateway derives an account's optional `last4` resolver hint from an
independent trailing four-ASCII-digit token in the account display name, such as
`工商银行信用卡 1242`. This makes the frozen `AccountRef.last4` contract usable
without adding an unpopulated accounting column or a premature identifier
catalog.

The derived value is only a resolver filter and is not an accounting identity or
uniqueness guarantee. Missing or colliding hints still produce the existing
not-found or structured-clarification results. A future first-class institution
identifier catalog may replace this derivation through a separate reviewed
migration.
