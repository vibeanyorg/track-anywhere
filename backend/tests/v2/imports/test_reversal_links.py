from __future__ import annotations

import pytest

from backend.tools.frozen_v1_history.reversal_links import (
    ReversalResolutionError,
    resolve_reversal_links,
)


def _pair(*, explicit: bool = False, inverse_amount: str = "10.00"):
    transactions = [
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
            "reverses_transaction_id": "original" if explicit else None,
        },
    ]
    postings = [
        {"transaction_id": "original", "account_id": "cash", "currency": "CNY", "amount": "10.00", "amount_semantics": "debit_credit", "side": "credit"},
        {"transaction_id": "original", "account_id": "expense", "currency": "CNY", "amount": "10.00", "amount_semantics": "debit_credit", "side": "debit"},
        {"transaction_id": "reversal", "account_id": "cash", "currency": "CNY", "amount": inverse_amount, "amount_semantics": "debit_credit", "side": "debit"},
        {"transaction_id": "reversal", "account_id": "expense", "currency": "CNY", "amount": inverse_amount, "amount_semantics": "debit_credit", "side": "credit"},
    ]
    return transactions, postings


def test_resolver_accepts_explicit_and_uniquely_provable_missing_link() -> None:
    transactions, postings = _pair()
    inferred = resolve_reversal_links(transactions, postings)
    assert inferred.links == (("reversal", "original"),)
    assert len(inferred.inferred) == 1

    transactions, postings = _pair(explicit=True)
    explicit = resolve_reversal_links(transactions, postings)
    assert explicit.links == (("reversal", "original"),)
    assert explicit.inferred == ()


def test_resolver_never_guesses_from_posting_shape_without_source_pointer() -> None:
    transactions, postings = _pair()
    transactions[0]["reversed_by"] = None
    assert resolve_reversal_links(transactions, postings).links == ()


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        ("noninverse", "noninverse"),
        ("ambiguous", "ambiguous"),
        ("multiple", "multiple"),
        ("cycle", "cycle"),
    ],
)
def test_resolver_fails_closed_for_invalid_reversal_graph(
    mutation: str, code: str
) -> None:
    transactions, postings = _pair(inverse_amount="9.99" if mutation == "noninverse" else "10.00")
    if mutation == "ambiguous":
        transactions.append(
            {"book_id": "book-a", "transaction_id": "other", "reversed_by": "reversal", "reverses_transaction_id": None}
        )
    elif mutation == "multiple":
        transactions.extend(
            [
                {"book_id": "book-a", "transaction_id": "second-reversal", "reversed_by": None, "reverses_transaction_id": "original"},
            ]
        )
        postings.extend(
            [
                {**postings[2], "transaction_id": "second-reversal"},
                {**postings[3], "transaction_id": "second-reversal"},
            ]
        )
    elif mutation == "cycle":
        transactions[1]["reverses_transaction_id"] = "original"
        transactions[0]["reverses_transaction_id"] = "reversal"

    with pytest.raises(ReversalResolutionError) as exc_info:
        resolve_reversal_links(transactions, postings)
    assert exc_info.value.code == code
    assert "original" not in str(exc_info.value)
