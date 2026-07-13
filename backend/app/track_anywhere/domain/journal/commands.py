from __future__ import annotations

from dataclasses import dataclass

from .models import PostingDraft, TransactionKind


@dataclass(frozen=True, slots=True)
class PostTransaction:
    transaction_id: str
    book_id: str
    kind: TransactionKind
    postings: tuple[PostingDraft, ...]
