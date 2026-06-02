# Data Backup

Local data is real user financial data. Back up the SQLite database before any manual data repair, schema change, import, bulk edit, or agent-driven mutation that is not a simple read.

Create a backup:

```bash
ta data backup --label before-change
```

The default backup source is `sqlite:///./.local/track-anywhere.sqlite3`. Backups are written to `.local/backups/`, which is ignored by git.

Use JSON output for agent workflows:

```bash
ta data backup --label before-change --json
```

For SQLite, the backup artifact is a database file. It does not contain a JSON
`posting_semantics` field. The command response includes
`backup.posting_semantics`, which declares `debit_credit` as the canonical model,
points to `postings.side` and `postings.amount_semantics`, and warns that legacy
signed amounts are historical migration data only.

For a non-default local SQLite database:

```bash
ta data backup --database-url sqlite:////absolute/path/to/track-anywhere.sqlite3 --output-dir /absolute/path/to/backups
```

This command uses SQLite's online backup API, so it is safer than raw file copy while the app may be running.

For PostgreSQL, `ta data backup` creates a targeted transaction audit snapshot.
Pass the transaction id explicitly:

```bash
ta data backup --database-url postgresql://... --transaction-id txn_... --label before-posting-repair
```

The PostgreSQL transaction backup artifact is a JSON file. It includes
`transactions`, `transaction_lines`, `postings`, referenced `accounts`, and
referenced category rows. Posting rows include `side` and `amount_semantics`.
Inside that JSON file, the key is top-level `posting_semantics`. CLI schema
guidance refers to this location as
`postgres_transaction_backup_file.posting_semantics` only to distinguish it from
the command response field `backup.posting_semantics`. The metadata says
`canonical_model = debit_credit`, `debit_credit_amount_rule = posting amount is
positive; side carries debit/credit direction`, `debit_credit_side_rule =
posting side is the only persisted debit/credit direction`, and
`legacy_signed_scope = historical migration and posting-semantics audit only`.
It also names `postings.amount`, `postings.side`, and
`postings.amount_semantics` as the canonical output fields. Agents must not
reinterpret debit/credit rows as legacy signed amounts. Use `pg_dump` for full
PostgreSQL database backups.

Before running posting-semantics rewrite or manual liability review resolution,
follow the [Posting Semantics Cutover Runbook](posting-semantics-cutover.md).
