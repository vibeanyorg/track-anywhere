from __future__ import annotations

import re
from types import MappingProxyType
from uuid import UUID


_LOWER_SHA256 = re.compile(r"^[0-9a-f]{64}$")

FROZEN_SOURCE_ARTIFACT = (
    "neon-track_anywhere-20260713-095634-before-ledger-kernel-refactor.dump"
)
EXPECTED_DUMP_SHA256 = (
    "a125b857a317e8c017d7028a26f78cf664ff6d57f6c0e698b3c229acf5d6cf9e"
)
EXPECTED_FULL_MANIFEST_SHA256 = (
    "f1cb565c646dc759b203efad3a5584492f3c824a16abb728bbce83150413597f"
)
EXPECTED_CREDIT_CARD_REVIEW_SHA256 = (
    "237f964a990018eb7fc91b9e45ba001ebb319456577f14263e3a444dc4d54430"
)
EXPECTED_DUMP_BYTES = 193_256
EXPECTED_SOURCE_REVISION = "0019_posting_constraints"
FROZEN_UUID_NAMESPACE = UUID("3f021172-6aa9-5b36-9208-f238bc35c596")
TARGET_BOOK_ID = UUID("a682ddd2-f26a-5ad1-8f0a-f2fc1f75fa3d")

EXPECTED_SOURCE_TABLE_COUNTS = MappingProxyType(
    {
        "accounts": 121,
        "assets": 20,
        "categories": 37,
        "category_versions": 37,
        "classification_events": 43,
        "counterparties": 2,
        "investment_events": 6,
        "investment_valuations": 0,
        "ledger_books": 1,
        "postings": 284,
        "transaction_lines": 43,
        "transactions": 135,
    }
)
EXPECTED_SOURCE_RECEIPTS = 729
EXPECTED_SOURCE_PRIMARY_KEYS = MappingProxyType(
    {
        "accounts": ("account_id",),
        "assets": ("asset_code",),
        "categories": ("category_id",),
        "category_versions": ("category_version_id",),
        "classification_events": ("classification_event_id",),
        "counterparties": ("counterparty_id",),
        "investment_events": ("event_id",),
        "investment_valuations": ("valuation_id",),
        "ledger_books": ("book_id",),
        "postings": ("transaction_id", "position", "id"),
        "transaction_lines": ("transaction_id", "position", "line_id"),
        "transactions": ("transaction_id",),
    }
)

# This is the old application's receipt-table size from the backup sidecar. It is
# deliberately not the number of frozen financial source rows.
EXPECTED_SIMPLE_MANIFEST_COUNTS = MappingProxyType(
    {
        "accounts": 121,
        "transactions": 135,
        "postings": 284,
        "transaction_lines": 43,
        "categories": 37,
        "category_versions": 37,
        "idempotency_receipts": 238,
    }
)

for _name, _value in (
    ("dump", EXPECTED_DUMP_SHA256),
    ("full manifest", EXPECTED_FULL_MANIFEST_SHA256),
    ("credit-card review", EXPECTED_CREDIT_CARD_REVIEW_SHA256),
):
    if _LOWER_SHA256.fullmatch(_value) is None:  # pragma: no cover - import invariant
        raise RuntimeError(f"invalid frozen {_name} SHA-256 constant")

if sum(EXPECTED_SOURCE_TABLE_COUNTS.values()) != EXPECTED_SOURCE_RECEIPTS:
    raise RuntimeError("frozen source receipt count does not equal source table rows")


__all__ = [
    "EXPECTED_CREDIT_CARD_REVIEW_SHA256",
    "EXPECTED_DUMP_BYTES",
    "EXPECTED_DUMP_SHA256",
    "EXPECTED_FULL_MANIFEST_SHA256",
    "EXPECTED_SIMPLE_MANIFEST_COUNTS",
    "EXPECTED_SOURCE_RECEIPTS",
    "EXPECTED_SOURCE_PRIMARY_KEYS",
    "EXPECTED_SOURCE_REVISION",
    "EXPECTED_SOURCE_TABLE_COUNTS",
    "FROZEN_SOURCE_ARTIFACT",
    "FROZEN_UUID_NAMESPACE",
    "TARGET_BOOK_ID",
]
