from __future__ import annotations

from typing import Any


BACKUP_UNAVAILABLE = (
    "data.backup is not implemented for the V2 event ledger; "
    "the CLI did not access a database or contact the server."
)


def create_data_backup(
    database_url: str | None = None,
    output_dir: str | None = None,
    label: str | None = None,
    *,
    transaction_id: str | None = None,
) -> dict[str, Any]:
    del database_url, output_dir, label, transaction_id
    raise RuntimeError(BACKUP_UNAVAILABLE)
