# Everyday entry historical repair

This runbook repairs legacy expenses that used a business category as an
ordinary expense account. It is intentionally an operator-only, append-only
workflow. It never edits ledger events, postings, balances, reporting lines, or
monthly summaries with direct SQL.

## What the runner guarantees

- The input plan is canonical-JSON hash locked.
- Dry-run is the default and opens a read-only transaction.
- Apply requires the same SHA-256 value twice.
- Each repair uses stable command, reversal, and replacement identifiers.
- The original transaction is reversed at its original effective time.
- The replacement preserves transaction kind, effective time, protected
  description reference, payment account, amount, and external references.
- The replacement posts through the canonical internal expense-clearing
  account and receives an expense category reporting line.
- The payment-account balance is unchanged and the mistaken account must reach
  exactly zero before it can be closed.
- Canonical internal accounts are provisioned for every active asset.
- The monthly category projection is caught up and affected months are compared
  with a full event cold replay before success is returned.
- Re-running the exact plan is an idempotent verification pass.

The runner accepts only two-leg, unclassified `standard` expenses and
`credit_card_charge` transactions whose metadata matches the journal postings.
Ambiguous investment, FX, fee, refund, or already-classified history must be
reviewed separately.

## Release order

Use an immutable image containing the repair code and migration
`v2_0014_everyday_entry_gateway`.

1. Create and verify a fresh production backup.
2. Pull the immutable candidate image without switching the live service.
3. Run the migration as the migrator role.
4. Run the repair dry-run from the candidate image.
5. Review the exact candidates, totals, account names, and plan hash.
6. Obtain a separate explicit production-apply authorization.
7. Run apply with the reviewed hash repeated as confirmation.
8. Re-run the same apply command and require every candidate to report
   `state=applied` and every repair to report as replayed.
9. Switch the application service to the same immutable image.
10. Verify readiness plus an everyday-entry prepare/commit smoke test.

The migration and repair are backward compatible with the currently running
application, so the live service does not need a maintenance window while
steps 3-8 run.

## Plan contract

Store the plan in a mode-`0600` file outside the repository:

```json
{
  "actor_subject_id": "human:<owner>",
  "book_id": "<book-uuid>",
  "close_account_ids": ["<mistaken-account-uuid>"],
  "create_category_paths": [["Parent"], ["Parent", "Child"]],
  "provision_all_active_internal_accounts": true,
  "repairs": [
    {
      "category_id": "<category-uuid>",
      "original_transaction_id": "<transaction-uuid>",
      "wrong_expense_account_id": "<mistaken-account-uuid>"
    }
  ],
  "version": 1
}
```

The close-account set must exactly equal the set of mistaken accounts in the
repair list. Category paths are needed only for deterministic categories that
do not already exist.

Create a separate mode-`0600` `/etc/track-anywhere/repair-runtime.env`
containing only the production runtime-role DSN:

```dotenv
TRACK_ANYWHERE_DATABASE_URL=postgresql+psycopg://track_anywhere_runtime:<url-encoded-password>@<internal-host>:5432/track_anywhere
```

Do not reuse the migrator or database-owner role for this workflow.

Compute the hash with the application canonicalizer, not with a formatter whose
number or Unicode rules may differ:

```bash
PLAN_FILE=/run/secrets/everyday-entry-repair.json
PLAN_SHA256="$(
  python -c \
    'import hashlib,json,sys; from track_anywhere.serialization.canonical_json import canonical_json_bytes; print(hashlib.sha256(canonical_json_bytes(json.load(open(sys.argv[1], "rb")))).hexdigest())' \
    "$PLAN_FILE"
)"
```

## Dry-run

Run inside the Dokploy network with the runtime database environment and the
candidate image:

```bash
docker run --rm --network dokploy-network \
  --env-file /etc/track-anywhere/repair-runtime.env \
  -i 'ghcr.io/vibeanyorg/track-anywhere-api@sha256:<digest>' \
  python -m track_anywhere.offline.repair_misclassified_expenses \
  --plan-sha256 "$PLAN_SHA256" --stdin < "$PLAN_FILE"
```

Dry-run must report the expected candidate count, account names, transaction
kinds, asset totals, category IDs, and internal-account count. Any
`candidate_state_mismatch` blocks apply.

## Apply and verification replay

After explicit approval of that exact dry-run:

```bash
docker run --rm --network dokploy-network \
  --env-file /etc/track-anywhere/repair-runtime.env \
  -i 'ghcr.io/vibeanyorg/track-anywhere-api@sha256:<digest>' \
  python -m track_anywhere.offline.repair_misclassified_expenses \
  --plan-sha256 "$PLAN_SHA256" --stdin --apply \
  --confirm-plan-sha256 "$PLAN_SHA256" < "$PLAN_FILE"
```

Success requires all verification flags to be true, including unchanged source
balances, zero mistaken-account balances, preserved descriptions and external
references, and verified monthly periods. Run the exact command once more; it
must append no new financial events.

There is no destructive rollback. If a reviewed repair is semantically wrong,
correct it with another explicit reversal/replacement command. Restore from the
pre-apply backup only for infrastructure-level disaster recovery.
