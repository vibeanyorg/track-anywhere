from __future__ import annotations

from typing import Any

from .ledger import Transaction
from .posting_semantics import canonical_posting_semantics_metadata


def transaction_posting_semantics(transaction: Transaction) -> dict[str, Any]:
    amount_semantics = sorted({posting.amount_semantics for posting in transaction.postings})
    if amount_semantics == ["debit_credit"]:
        row_model = "debit_credit"
    elif amount_semantics == ["legacy_signed"]:
        row_model = "legacy_signed"
    else:
        row_model = "mixed_or_unknown"
    return {
        **canonical_posting_semantics_metadata(),
        "row_model": row_model,
        "amount_semantics": amount_semantics,
    }
