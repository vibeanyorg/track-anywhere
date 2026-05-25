from __future__ import annotations

from decimal import Decimal

from .ledger import Transaction
from .storage_models import PostingRecord

PostingEntry = tuple[int, str, Decimal, str, str]


def transaction_posting_entries(transaction: Transaction) -> list[PostingEntry]:
    return [
        (index, posting.account_id, posting.amount, posting.currency, transaction.book_id)
        for index, posting in enumerate(transaction.postings)
    ]


def stored_posting_entries(postings: list[PostingRecord], fallback_book_id: str) -> list[PostingEntry]:
    return [
        (
            posting.position,
            posting.account_id,
            Decimal(posting.amount),
            posting.currency,
            posting.book_id or fallback_book_id,
        )
        for posting in postings
    ]
