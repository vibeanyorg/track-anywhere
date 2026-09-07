# Account rename (v0.2.9)

Use `ledger_rename_account(book_id, account_id, current_name, request_id)`
with `book:write` authorization to rename an existing ordinary account.
The REST equivalent is
`POST /api/v2/books/{book_id}/accounts/{account_id}/rename` with
`current_name` and `request_id` in the JSON body.

Names are trimmed, nonblank, and limited to 512 characters. Closed ordinary
accounts are supported; system-managed accounts are rejected. Duplicate names
remain allowed, as with account creation, so use account IDs to disambiguate.

Migration `v2_0018_account_rename` adds an account metadata version and an
append-only `account_mutations` receipt containing the actor, command, and
before/after snapshots. Runtime access to receipts is SELECT/INSERT only.
Account updates are restricted to name, status, version, and update timestamp.
No existing account is renamed by this migration.

Renaming preserves the account ID, currency, type, bindings, balances, journal
postings, and event hashes. Existing entry readback displays the current account
name. Pending entry previews that contain the old name must be prepared again
before confirmation, because commit revalidates the exact preview.

Retry an uncertain request with the same request ID and arguments. A conflicting
reuse is rejected. Replaying an old rename after a later rename never reverts
the current name: REST returns the original receipt snapshot, while MCP returns
a fresh account readback with the `replayed` flag.

Deploy the migration before the new application. To fix an incorrect card-network
label in an account name, rename that existing currency account; do not create
another account or reverse/rebook its transactions.
