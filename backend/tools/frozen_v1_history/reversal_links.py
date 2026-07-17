from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation


Row = Mapping[str, object]


class ReversalResolutionError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(f"source reversal graph is invalid ({code})")


@dataclass(frozen=True, order=True, slots=True)
class InferredReversalLink:
    reversal_transaction_id: str = field(repr=False)
    original_transaction_id: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class ResolvedReversalLinks:
    links: tuple[tuple[str, str], ...] = field(repr=False)
    inferred: tuple[InferredReversalLink, ...] = field(repr=False)

    def target_for(self, transaction_id: object) -> str | None:
        source = str(transaction_id)
        return next((target for candidate, target in self.links if candidate == source), None)


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
        side = row.get("side")
        if amount <= 0 or side not in {"debit", "credit"}:
            return None
    else:
        return None
    return str(row.get("account_id")), str(row.get("currency")), str(side), abs(amount)


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
        (account, asset, "credit" if side == "debit" else "debit", amount)
        for account, asset, side, amount in original_facts  # type: ignore[misc]
    )
    actual = sorted(fact for fact in reversal_facts if fact is not None)
    return expected == actual


def _cycle_exists(edges: Mapping[str, str]) -> bool:
    for start in sorted(edges):
        seen: set[str] = set()
        current = start
        while current in edges:
            if current in seen:
                return True
            seen.add(current)
            current = edges[current]
    return False


def resolve_reversal_links(
    transactions: Sequence[Row], postings: Sequence[Row]
) -> ResolvedReversalLinks:
    by_id: dict[str, Row] = {}
    for row in transactions:
        transaction_id = row.get("transaction_id")
        if transaction_id is None or str(transaction_id) in by_id:
            raise ReversalResolutionError("ambiguous")
        by_id[str(transaction_id)] = row

    postings_by_transaction: dict[str, list[Row]] = defaultdict(list)
    for posting in postings:
        postings_by_transaction[str(posting.get("transaction_id"))].append(posting)

    explicit: dict[str, str] = {}
    for transaction_id, row in by_id.items():
        target = row.get("reverses_transaction_id")
        if target is not None:
            explicit[transaction_id] = str(target)
    targets = Counter(explicit.values())
    if any(count > 1 for count in targets.values()):
        raise ReversalResolutionError("multiple")

    originals_by_reverse: dict[str, list[str]] = defaultdict(list)
    for transaction_id, row in by_id.items():
        reverse = row.get("reversed_by")
        if reverse is not None:
            originals_by_reverse[str(reverse)].append(transaction_id)
    if any(len(originals) > 1 for originals in originals_by_reverse.values()):
        raise ReversalResolutionError("ambiguous")

    links = dict(explicit)
    inferred: list[InferredReversalLink] = []
    for reverse_id, original_ids in sorted(originals_by_reverse.items()):
        original_id = original_ids[0]
        if reverse_id in explicit and explicit[reverse_id] != original_id:
            raise ReversalResolutionError("ambiguous")
        if reverse_id not in explicit:
            links[reverse_id] = original_id
            inferred.append(InferredReversalLink(reverse_id, original_id))

    if Counter(links.values()) and any(count > 1 for count in Counter(links.values()).values()):
        raise ReversalResolutionError("multiple")
    for reverse_id, original_id in sorted(links.items()):
        reverse = by_id.get(reverse_id)
        original = by_id.get(original_id)
        if reverse is None or original is None:
            raise ReversalResolutionError("orphan")
        if str(reverse.get("book_id")) != str(original.get("book_id")):
            raise ReversalResolutionError("cross_book")
        if original.get("reversed_by") not in {None, reverse_id}:
            raise ReversalResolutionError("ambiguous")
        if not _are_exact_inverses(
            postings_by_transaction.get(original_id, ()),
            postings_by_transaction.get(reverse_id, ()),
        ):
            raise ReversalResolutionError("noninverse")
    if _cycle_exists(links):
        raise ReversalResolutionError("cycle")
    return ResolvedReversalLinks(
        links=tuple(sorted(links.items())),
        inferred=tuple(sorted(inferred)),
    )


__all__ = [
    "InferredReversalLink",
    "ResolvedReversalLinks",
    "ReversalResolutionError",
    "resolve_reversal_links",
]
