# Track Anywhere V2 operator runbook

## Runtime check

```bash
ta system health --json
ta system ready --json
ta auth status --json
ta capabilities --json
```

The HTTP equivalents are:

```bash
curl --fail http://127.0.0.1:8000/api/v2/health
curl --fail http://127.0.0.1:8000/api/v2/ready
```

Readiness must report PostgreSQL 17, the expected Alembic head, and the
non-owner runtime identity. Do not route around a failed readiness check.

## Discover exact syntax

```bash
ta book --help
ta asset --help
ta account --help
ta category --help
ta tx --help
ta investment --help
```

Command flags are the contract. This runbook deliberately avoids duplicating
every flag because the CLI schema is versioned and machine-readable:

```bash
ta schema --json
```

## Catalog setup order

For a new Book, create catalog facts in dependency order:

1. Book
2. asset definitions
3. single-asset accounts
4. reporting categories and immutable category versions

Use IDs returned by each command. A posting's asset must match its account's
asset, and every referenced object must belong to the same Book.

## Journal writes

`ta tx record` accepts explicit postings. Every posting uses an explicit side
and a positive decimal-string amount. For each asset in a transaction, debit
units must equal credit units exactly.

Before a write:

```bash
ta tx record --help
```

After a write, preserve the returned transaction ID and immediately verify:

```bash
ta tx list <book-id> --json
ta book balances <book-id> --json
```

Use the same idempotency key only when retrying the same logical command. A
different payload with the same key is a conflict, not a retry.

## Reversal and correction

Do not edit an existing journal fact. Use the explicit `tx reverse`,
`tx correct`, or `tx correct-reference` command after inspecting its help.
Reversal creates a new immutable inverse transaction and preserves the original
event chain.

## Classification

Classification attaches versioned reporting lines to a posted transaction. Use
`tx classify` with an existing immutable category version and use
`tx clear-classification` to remove the current assignment. Verify with
`ta book reporting-lines <book-id> --as-of-book-position <position> --json`.

## FX

Use `tx fx` only when both asset legs, accounts, exact quantities, and effective
time are known. Never infer an exchange rate by rounding one side. The event
records exact units for both assets.

## Investment lots

V2 investments use explicit lot acquisition and disposal, not generic cash-flow
or valuation events:

```bash
ta investment acquire --help
ta investment dispose --help
```

Instrument and settlement assets must be distinct, units must be exact, and a
disposal must reference an available lot. Valuations are deferred and must not
be fabricated from account names or free text.

## Unsupported workflows

Draft capture, recurring rules, payment-profile helpers, credit-card metadata,
attachments, broad search, and client-side backup/restore have no compatibility
adapter. Their CLI groups fail locally. Stop and report the unsupported
capability instead of calling an older route or writing the database directly.

## Final report

Report only read-back evidence:

```text
Book: <book-id>
Command: <supported V2 command>
Idempotency key: <key>
Transaction/events: <returned ids>
Fresh read: <balances or reporting lines checked>
Uncertainty: <none or exact missing fact>
```
