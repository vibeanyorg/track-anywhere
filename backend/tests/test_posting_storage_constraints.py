from __future__ import annotations

from sqlalchemy import CheckConstraint

from track_anywhere.storage_models import DraftPostingRecord, PostingRecord


def test_posting_records_define_debit_credit_shape_constraints():
    posting_constraints = _check_constraint_names(PostingRecord)
    draft_constraints = _check_constraint_names(DraftPostingRecord)

    assert {
        "ck_postings_amount_semantics",
        "ck_postings_side",
        "ck_postings_debit_credit_shape",
        "ck_postings_legacy_nonzero",
    }.issubset(posting_constraints)
    assert {
        "ck_draft_postings_amount_semantics",
        "ck_draft_postings_side",
        "ck_draft_postings_debit_credit_shape",
        "ck_draft_postings_legacy_nonzero",
    }.issubset(draft_constraints)


def test_fresh_posting_records_default_to_debit_credit_semantics():
    assert PostingRecord.__table__.c.amount_semantics.nullable is False
    assert DraftPostingRecord.__table__.c.amount_semantics.nullable is False
    assert PostingRecord.__table__.c.amount_semantics.default.arg == "debit_credit"
    assert DraftPostingRecord.__table__.c.amount_semantics.default.arg == "debit_credit"
    assert "debit_credit" in str(PostingRecord.__table__.c.amount_semantics.server_default.arg)
    assert "debit_credit" in str(DraftPostingRecord.__table__.c.amount_semantics.server_default.arg)


def _check_constraint_names(record_type) -> set[str]:
    return {
        constraint.name or ""
        for constraint in record_type.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    }
