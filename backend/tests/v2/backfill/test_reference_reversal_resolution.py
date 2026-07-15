from __future__ import annotations

from backend.tools.backfill_v1.reference_reducer import _source_reversal_links


def _source(amount: str = "10.00") -> dict[str, list[dict[str, object]]]:
    return {
        "transactions": [
            {
                "book_id": "book-a",
                "transaction_id": "original",
                "reversed_by": "reversal",
                "reverses_transaction_id": None,
            },
            {
                "book_id": "book-a",
                "transaction_id": "reversal",
                "reversed_by": None,
                "reverses_transaction_id": None,
            },
        ],
        "postings": [
            {
                "account_id": "cash",
                "amount": "10.00",
                "amount_semantics": "debit_credit",
                "currency": "CNY",
                "side": "credit",
                "transaction_id": "original",
            },
            {
                "account_id": "expense",
                "amount": "10.00",
                "amount_semantics": "debit_credit",
                "currency": "CNY",
                "side": "debit",
                "transaction_id": "original",
            },
            {
                "account_id": "cash",
                "amount": amount,
                "amount_semantics": "debit_credit",
                "currency": "CNY",
                "side": "debit",
                "transaction_id": "reversal",
            },
            {
                "account_id": "expense",
                "amount": amount,
                "amount_semantics": "debit_credit",
                "currency": "CNY",
                "side": "credit",
                "transaction_id": "reversal",
            },
        ],
    }


def test_independent_reducer_infers_only_exact_one_sided_reversal() -> None:
    assert _source_reversal_links(_source()) == {"reversal": "original"}
    assert _source_reversal_links(_source("9.99")) == {}
