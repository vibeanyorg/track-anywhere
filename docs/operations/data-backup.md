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

For a non-default local SQLite database:

```bash
ta data backup --database-url sqlite:////absolute/path/to/track-anywhere.sqlite3 --output-dir /absolute/path/to/backups
```

This command uses SQLite's online backup API, so it is safer than raw file copy while the app may be running. PostgreSQL backup support is intentionally not implemented yet; use `pg_dump` when the first PostgreSQL deployment is introduced.
