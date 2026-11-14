# Correct a card binding that starts too late

When a historical expense or correction reports that no active currency binding
exists at the requested time, inspect both the card and currency binding start
dates. If the card was valid on the expense date, correct its configuration with
`ledger_backdate_payment_instrument_binding` using the book, card, binding,
request ID and an explicit timezone-aware `effective_from`.

The HTTP equivalent is the existing
`POST /api/v2/books/{book_id}/payment-instruments/{instrument_id}/mutations`
with operation `backdate_binding`, matching `book_id` and `instrument_id`,
`request_id`, `binding_id`, and `effective_from`.

The operation requires `book:write`, an active card and binding, and an earlier,
nonfuture date. It rejects overlap with any other binding for that card and
currency, including closed historical bindings. The card start is also moved
earlier when needed, because historical resolution checks both dates. The
binding's account, currency and ID remain unchanged. The mutation stores before
and after views, increments the card version, and supports exact request-ID
retries.

Apply migration `v2_0019_binding_backdate` before deploying the updated backend;
it grants the runtime role permission to update the binding start date. Refresh
the MCP client's tool list after deployment.

This changes configuration only. Prepare the transaction correction again after
a successful, verified response, then commit through the normal entry workflow
and verify the resulting transaction and monthly report. Do not separately
reverse the original transaction before preparing a valid replacement.
