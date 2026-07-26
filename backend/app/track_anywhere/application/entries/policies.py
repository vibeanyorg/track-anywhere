from __future__ import annotations

from dataclasses import dataclass

from .amounts import EntryAsset, NormalizedAmount, normalize_amount
from .contracts import CategoryAllocationInput, MoneyInput
from .errors import EntryErrorCode, EntryGatewayError


@dataclass(frozen=True, slots=True)
class NormalizedAllocation:
    input: CategoryAllocationInput
    amount: NormalizedAmount


def normalize_category_allocations(
    *,
    amount: MoneyInput,
    category_allocations: tuple[CategoryAllocationInput, ...],
    asset: EntryAsset,
) -> tuple[NormalizedAllocation, ...]:
    total = normalize_amount(amount, asset=asset)
    normalized = tuple(
        NormalizedAllocation(
            input=allocation,
            amount=normalize_amount(allocation.amount, asset=asset),
        )
        for allocation in category_allocations
    )
    if normalized and sum(item.amount.units for item in normalized) != total.units:
        raise EntryGatewayError(
            EntryErrorCode.CATEGORY_ALLOCATION_MISMATCH,
            "category allocations must exactly equal the entry amount",
            field="category_allocations",
        )
    return normalized


def require_distinct_accounts(first_id: object, second_id: object) -> None:
    if first_id == second_id:
        raise EntryGatewayError(
            EntryErrorCode.SAME_ACCOUNT,
            "entry accounts must be distinct",
            field="account",
        )


__all__ = [
    "NormalizedAllocation",
    "normalize_category_allocations",
    "require_distinct_accounts",
]
