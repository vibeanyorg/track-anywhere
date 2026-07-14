from __future__ import annotations

from uuid import UUID

import pytest

from backend.tools.backfill_v1.normalize import (
    normalize_category_version,
    normalize_legacy_signed_posting,
)


def test_legacy_sign_becomes_explicit_side_and_positive_exact_units() -> None:
    debit = normalize_legacy_signed_posting(
        source_book_id="book-a",
        source_transaction_id="tx-1",
        source_posting_id="1",
        source_account_id="cash",
        asset_code="CNY",
        amount="12.34",
        ledger_scale=2,
        backfill_mode=True,
    )
    credit = normalize_legacy_signed_posting(
        source_book_id="book-a",
        source_transaction_id="tx-1",
        source_posting_id="2",
        source_account_id="expense",
        asset_code="CNY",
        amount="-12.34",
        ledger_scale=2,
        backfill_mode=True,
    )

    assert (debit.side, debit.units) == ("debit", 1234)
    assert (credit.side, credit.units) == ("credit", 1234)
    assert debit.posting_id != credit.posting_id
    assert debit.account_id != credit.account_id


@pytest.mark.parametrize("amount", ["0", "0.00", "-0.00"])
def test_zero_legacy_postings_are_rejected(amount: str) -> None:
    with pytest.raises(ValueError, match="nonzero"):
        normalize_legacy_signed_posting(
            source_book_id="book-a",
            source_transaction_id="tx-1",
            source_posting_id="1",
            source_account_id="cash",
            asset_code="CNY",
            amount=amount,
            ledger_scale=2,
            backfill_mode=True,
        )


def test_category_version_identity_and_snapshot_are_preserved_exactly() -> None:
    version_id = UUID("4b1c47df-985d-44f0-aa98-a4ee4ddbd897")
    normalized = normalize_category_version(
        {
            "category_version_id": str(version_id),
            "name": "Food at import",
            "parent_category_id": None,
            "status": "archived",
        }
    )

    assert normalized.category_version_id == version_id
    assert normalized.name == "Food at import"
    assert normalized.status == "archived"
