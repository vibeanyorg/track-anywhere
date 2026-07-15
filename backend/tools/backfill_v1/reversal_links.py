from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Mapping, Sequence


Row = Mapping[str, object]


@dataclass(frozen=True, order=True, slots=True)
class InferredReversalLink:
    reversal_transaction_id: str
    original_transaction_id: str


@dataclass(frozen=True, slots=True)
class ResolvedReversalLinks:
    links: tuple[tuple[str, str], ...]
    inferred: tuple[InferredReversalLink, ...]

    def target_for(self, transaction_id: object) -> str | None:
        source = str(transaction_id)
        return next(
            (target for candidate, target in self.links if candidate == source), None
        )


def _posting_fact(row: Row) -> tuple[str, str, str, Decimal] | None:
    try:
        amount = Decimal(str(row.get("amount")))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if not amount.is_finite() or amount == 0:
        return None

    semantics = row.get("amount_semantics")
    if semantics in {None, "legacy_signed"}:
        side = "debit" if amount > 0 else "credit"
    elif semantics == "debit_credit":
        side = str(row.get("side"))
        if amount <= 0 or side not in {"debit", "credit"}:
            return None
    else:
        return None
    return (
        str(row.get("account_id")),
        str(row.get("currency")),
        side,
        abs(amount),
    )


def _are_exact_inverses(original: Sequence[Row], reversal: Sequence[Row]) -> bool:
    original_facts = [_posting_fact(row) for row in original]
    reversal_facts = [_posting_fact(row) for row in reversal]
    if (
        not original_facts
        or len(original_facts) != len(reversal_facts)
        or any(fact is None for fact in (*original_facts, *reversal_facts))
    ):
        return False
    expected = sorted(
        (
            account_id,
            asset_code,
            "credit" if side == "debit" else "debit",
            amount,
        )
        for account_id, asset_code, side, amount in original_facts
        if account_id is not None
    )
    actual = sorted(fact for fact in reversal_facts if fact is not None)
    return expected == actual


def resolve_reversal_links(
    transactions: Sequence[Row], postings: Sequence[Row]
) -> ResolvedReversalLinks:
    """Resolve only uniquely provable missing reverse-to-original pointers.

    V1 sometimes persisted ``original.reversed_by`` without the reciprocal
    ``reversal.reverses_transaction_id``.  The missing edge is inferred only
    when the pointer is unique, both rows share a book, no explicit edge
    conflicts, and every posting is the exact debit/credit inverse.
    """

    transactions_by_id = {
        str(row["transaction_id"]): row
        for row in transactions
        if row.get("transaction_id") is not None
    }
    postings_by_transaction: dict[str, list[Row]] = defaultdict(list)
    for posting in postings:
        postings_by_transaction[str(posting.get("transaction_id"))].append(posting)

    explicit = {
        str(row["transaction_id"]): str(row["reverses_transaction_id"])
        for row in transactions
        if row.get("transaction_id") is not None
        and row.get("reverses_transaction_id") is not None
    }
    originals_by_reverse: dict[str, list[str]] = defaultdict(list)
    for row in transactions:
        if row.get("transaction_id") is not None and row.get("reversed_by") is not None:
            originals_by_reverse[str(row["reversed_by"])].append(
                str(row["transaction_id"])
            )

    explicit_reversals_by_original: dict[str, list[str]] = defaultdict(list)
    for reverse_id, original_id in explicit.items():
        explicit_reversals_by_original[original_id].append(reverse_id)

    inferred: list[InferredReversalLink] = []
    for reverse_id, original_ids in sorted(originals_by_reverse.items()):
        if len(original_ids) != 1 or reverse_id in explicit:
            continue
        original_id = original_ids[0]
        original = transactions_by_id.get(original_id)
        reversal = transactions_by_id.get(reverse_id)
        if original is None or reversal is None:
            continue
        if str(original.get("book_id")) != str(reversal.get("book_id")):
            continue
        if explicit_reversals_by_original.get(original_id):
            continue
        if not _are_exact_inverses(
            postings_by_transaction.get(original_id, ()),
            postings_by_transaction.get(reverse_id, ()),
        ):
            continue
        inferred.append(
            InferredReversalLink(
                reversal_transaction_id=reverse_id,
                original_transaction_id=original_id,
            )
        )

    links = dict(explicit)
    links.update(
        (item.reversal_transaction_id, item.original_transaction_id)
        for item in inferred
    )
    return ResolvedReversalLinks(
        links=tuple(sorted(links.items())),
        inferred=tuple(sorted(inferred)),
    )


__all__ = [
    "InferredReversalLink",
    "ResolvedReversalLinks",
    "resolve_reversal_links",
]
