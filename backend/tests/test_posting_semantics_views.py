from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from track_anywhere.ledger import Posting, credit_posting, debit_posting, legacy_signed_posting
from track_anywhere.posting_semantics import (
    DEBIT_CREDIT_AMOUNT_RULE,
    DEBIT_CREDIT_SIDE_RULE,
    LEGACY_SIGNED_AMOUNT_RULE,
    LEGACY_SIGNED_SCOPE,
    POSTING_CANONICAL_MODEL,
    POSTING_AMOUNT_FIELD,
    POSTING_AMOUNT_SEMANTICS_FIELD,
    POSTING_SIDE_FIELD,
    POSTING_SEMANTICS_REVIEW_DECISION_DERIVED_FIELDS,
    POSTING_SEMANTICS_REVIEW_DECISION_FORBIDDEN_FIELDS,
    POSTING_SEMANTICS_REVIEW_DECISION_INPUT_FIELDS,
    POSTING_SEMANTICS_REVIEW_RECOMMENDATION_READ_ONLY_FIELDS,
    PUBLIC_WRITE_FORBIDDEN_POSTING_FIELDS,
    backup_posting_semantics_metadata,
    canonical_posting_semantics_metadata,
)
from track_anywhere.posting_semantics_views import transaction_posting_semantics


REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND = REPO_ROOT / "backend/app/track_anywhere"


def _transaction(postings: list[Posting]) -> Any:
    return SimpleNamespace(postings=postings)


def test_posting_semantics_views_imports_shared_contract_constants():
    source = (BACKEND / "posting_semantics_views.py").read_text()

    assert "from .posting_semantics import" in source
    assert 'POSTING_CANONICAL_MODEL = "debit_credit"' not in source
    assert "DEBIT_CREDIT_AMOUNT_RULE =" not in source
    assert "LEGACY_SIGNED_SCOPE =" not in source


def test_shared_posting_semantics_metadata_builders_define_canonical_contract():
    assert canonical_posting_semantics_metadata() == {
        "canonical_model": POSTING_CANONICAL_MODEL,
        "debit_credit_amount_rule": DEBIT_CREDIT_AMOUNT_RULE,
        "debit_credit_side_rule": DEBIT_CREDIT_SIDE_RULE,
        "posting_amount_field": POSTING_AMOUNT_FIELD,
        "posting_side_field": POSTING_SIDE_FIELD,
        "posting_amount_semantics_field": POSTING_AMOUNT_SEMANTICS_FIELD,
        "legacy_signed_scope": LEGACY_SIGNED_SCOPE,
    }
    assert backup_posting_semantics_metadata() == {
        **canonical_posting_semantics_metadata(),
        "side_field": POSTING_SIDE_FIELD,
        "amount_semantics_field": POSTING_AMOUNT_SEMANTICS_FIELD,
        "legacy_signed_amount_rule": LEGACY_SIGNED_AMOUNT_RULE,
    }
    assert PUBLIC_WRITE_FORBIDDEN_POSTING_FIELDS == (
        "postings",
        "side",
        "amount_semantics",
        "signed_amount",
        "raw_amount",
    )
    assert POSTING_SEMANTICS_REVIEW_DECISION_INPUT_FIELDS == (
        "record_ref",
        "transaction_id",
        "position",
        "account_id",
        "currency",
        "legacy_amount",
        "action",
    )
    assert POSTING_SEMANTICS_REVIEW_DECISION_DERIVED_FIELDS == ("target_side", "target_amount")
    assert POSTING_SEMANTICS_REVIEW_RECOMMENDATION_READ_ONLY_FIELDS == (
        "amount_semantics",
        *POSTING_SEMANTICS_REVIEW_DECISION_DERIVED_FIELDS,
    )
    assert set(POSTING_SEMANTICS_REVIEW_RECOMMENDATION_READ_ONLY_FIELDS).issubset(
        POSTING_SEMANTICS_REVIEW_DECISION_FORBIDDEN_FIELDS
    )
    assert POSTING_SEMANTICS_REVIEW_DECISION_FORBIDDEN_FIELDS == (
        *PUBLIC_WRITE_FORBIDDEN_POSTING_FIELDS,
        *POSTING_SEMANTICS_REVIEW_DECISION_DERIVED_FIELDS,
    )


def test_transaction_posting_semantics_marks_debit_credit_rows_as_canonical():
    transaction = _transaction(
        [
            credit_posting("acc_cash", Decimal("5"), "USD"),
            debit_posting("acc_expense", Decimal("5"), "USD"),
        ]
    )

    assert transaction_posting_semantics(transaction) == {
        "canonical_model": POSTING_CANONICAL_MODEL,
        "row_model": "debit_credit",
        "amount_semantics": ["debit_credit"],
        "debit_credit_amount_rule": DEBIT_CREDIT_AMOUNT_RULE,
        "debit_credit_side_rule": DEBIT_CREDIT_SIDE_RULE,
        "posting_amount_field": POSTING_AMOUNT_FIELD,
        "posting_side_field": POSTING_SIDE_FIELD,
        "posting_amount_semantics_field": POSTING_AMOUNT_SEMANTICS_FIELD,
        "legacy_signed_scope": LEGACY_SIGNED_SCOPE,
    }


def test_transaction_posting_semantics_marks_legacy_signed_rows_as_historical():
    transaction = _transaction(
        [
            legacy_signed_posting("acc_cash", Decimal("-5"), "USD"),
            legacy_signed_posting("acc_expense", Decimal("5"), "USD"),
        ]
    )

    assert transaction_posting_semantics(transaction)["row_model"] == "legacy_signed"
    assert transaction_posting_semantics(transaction)["amount_semantics"] == ["legacy_signed"]
    assert (
        transaction_posting_semantics(transaction)["legacy_signed_scope"]
        == LEGACY_SIGNED_SCOPE
    )


def test_transaction_posting_semantics_marks_mixed_or_unknown_rows_as_noncanonical():
    transaction = _transaction(
        [
            legacy_signed_posting("acc_cash", Decimal("-5"), "USD"),
            Posting("acc_expense", Decimal("5"), "USD", side="debit", amount_semantics="unknown"),  # type: ignore[arg-type]
        ]
    )

    assert transaction_posting_semantics(transaction)["row_model"] == "mixed_or_unknown"
    assert transaction_posting_semantics(transaction)["amount_semantics"] == ["legacy_signed", "unknown"]
