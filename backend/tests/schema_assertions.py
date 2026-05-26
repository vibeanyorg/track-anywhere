from __future__ import annotations

import sqlite3


PAYMENT_PROFILE_COLUMNS = {
    "profile_id",
    "book_id",
    "slug",
    "display_name",
    "kind",
    "instrument_account_id",
    "instrument_currency",
    "backing_account_id",
    "backing_currency",
    "settlement_mode",
    "settlement_rate",
    "status",
    "version",
}
PAYMENT_INSTRUMENT_COLUMNS = {
    "instrument_id",
    "book_id",
    "slug",
    "display_name",
    "kind",
    "account_id",
    "last4",
    "status",
    "version",
}


def index_columns(connection: sqlite3.Connection, table_name: str) -> dict[str, tuple[bool, tuple[str, ...]]]:
    indexes: dict[str, tuple[bool, tuple[str, ...]]] = {}
    for _, name, unique, *_ in connection.execute(f"pragma index_list({table_name})").fetchall():
        columns = tuple(row[2] for row in connection.execute(f"pragma index_info({name})").fetchall())
        indexes[name] = (bool(unique), columns)
    return indexes
