from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal

from .accounting import STORAGE_POSTING_AMOUNT_SEMANTICS_MISSING, storage_posting_amount_semantics
from .ledger import Posting, Transaction
from .storage_models import PostingRecord

PostingEntry = tuple[int, str, str | None, str, Decimal, str, str]


def transaction_posting_entries(
    transaction: Transaction,
    *,
    side_for_posting: Callable[[Posting], str | None] | None = None,
) -> list[PostingEntry]:
    return [
        (
            index,
            posting.account_id,
            side_for_posting(posting) if side_for_posting else posting.side,
            posting.amount_semantics,
            posting.amount,
            posting.currency,
            transaction.book_id,
        )
        for index, posting in enumerate(transaction.postings)
    ]


def stored_posting_entries(postings: list[PostingRecord], fallback_book_id: str) -> list[PostingEntry]:
    return [
        (
            posting.position,
            posting.account_id,
            getattr(posting, "side", None),
            storage_posting_amount_semantics(
                getattr(posting, "amount_semantics", STORAGE_POSTING_AMOUNT_SEMANTICS_MISSING)
            ),
            Decimal(posting.amount),
            posting.currency,
            posting.book_id or fallback_book_id,
        )
        for posting in postings
    ]
