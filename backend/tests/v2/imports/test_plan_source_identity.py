from __future__ import annotations

import pytest

from backend.tools.frozen_v1_history.namespaces import deterministic_uuid
from backend.tools.frozen_v1_history.normalize import HistoricalAssetScale
from backend.tools.frozen_v1_history.planner import (
    FrozenPlanCompilationError,
    _journal_posting_facts,
    _required_source_identity,
)
from track_anywhere.domain.journal.models import PostingSide


def test_integer_source_posting_ids_use_canonical_decimal_review_and_uuid_identity() -> (
    None
):
    postings = (
        {
            "id": 17,
            "transaction_id": "fixture-transaction",
            "position": 0,
            "account_id": "fixture-account-a",
            "currency": "TST",
            "amount": "1.00",
            "amount_semantics": "debit_credit",
            "side": "debit",
        },
        {
            "id": 18,
            "transaction_id": "fixture-transaction",
            "position": 1,
            "account_id": "fixture-account-b",
            "currency": "TST",
            "amount": "1.00",
            "amount_semantics": "debit_credit",
            "side": "credit",
        },
    )

    facts = _journal_posting_facts(
        transaction={
            "book_id": "fixture-book",
            "transaction_id": "fixture-transaction",
        },
        postings=postings,
        policies={
            "TST": HistoricalAssetScale.for_source(
                asset_code="TST",
                source_scale=2,
                source_display_scale=2,
            )
        },
        posting_decisions={"17": "fixture-reviewed-account\0debit"},
    )

    assert facts[0].posting_id == deterministic_uuid(
        "posting",
        "fixture-book",
        "fixture-transaction",
        "17",
    )
    assert facts[0].account_id == deterministic_uuid(
        "account", "fixture-book", "fixture-reviewed-account"
    )
    assert facts[0].side is PostingSide.DEBIT
    assert facts[1].posting_id == deterministic_uuid(
        "posting",
        "fixture-book",
        "fixture-transaction",
        "18",
    )


@pytest.mark.parametrize("value", (True, -1, 1.5, None, ""))
def test_source_identity_rejects_bool_negative_and_noncanonical_types(
    value: object,
) -> None:
    with pytest.raises(FrozenPlanCompilationError, match="source identity") as captured:
        _required_source_identity({"id": value}, "id")

    assert repr(value) not in str(captured.value)
