from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from track_anywhere.drafts import DraftBook, DraftTransaction
from track_anywhere.errors import ValidationError
from track_anywhere.ledger import Posting, credit_posting, debit_posting, legacy_signed_posting


def test_draft_book_projected_impact_uses_account_type_aware_debit_credit_math():
    drafts = DraftBook()
    drafts.create(
        memo="card purchase draft",
        proposed_postings=[
            credit_posting("acc_card", Decimal("11.08"), "USD"),
            debit_posting("acc_expense", Decimal("11.08"), "USD"),
        ],
        missing_fields=[],
        source="agent",
        confidence=0.9,
    )

    assert drafts.projected_impact("acc_card", account_type="liability") == {"USD": Decimal("11.08")}
    assert drafts.projected_impact("acc_card", account_type="asset") == {"USD": Decimal("-11.08")}


def test_draft_book_create_rejects_invalid_debit_credit_shape():
    drafts = DraftBook()

    with pytest.raises(ValidationError, match="debit/credit posting requires side"):
        drafts.create(
            memo="invalid draft",
            proposed_postings=[Posting("acc_card", Decimal("11.08"), "USD")],
            missing_fields=[],
            source="agent",
            confidence=0.9,
        )


def test_draft_book_create_rejects_new_legacy_signed_postings():
    drafts = DraftBook()

    with pytest.raises(ValidationError, match="new draft postings must use debit_credit semantics"):
        drafts.create(
            memo="legacy signed draft",
            proposed_postings=[legacy_signed_posting("acc_cash", Decimal("-5"), "USD")],
            missing_fields=[],
            source="agent",
            confidence=0.9,
        )


def test_draft_book_create_rejects_unbalanced_debit_credit_postings():
    drafts = DraftBook()

    with pytest.raises(ValidationError, match="draft postings must balance by currency"):
        drafts.create(
            memo="unbalanced draft",
            proposed_postings=[
                credit_posting("acc_card", Decimal("11.08"), "USD"),
                debit_posting("acc_expense", Decimal("10.00"), "USD"),
            ],
            missing_fields=[],
            source="agent",
            confidence=0.9,
        )


def test_draft_book_supersede_rejects_invalid_debit_credit_shape():
    drafts = DraftBook()
    draft = drafts.create(
        memo="card purchase draft",
        proposed_postings=[
            credit_posting("acc_card", Decimal("11.08"), "USD"),
            debit_posting("acc_expense", Decimal("11.08"), "USD"),
        ],
        missing_fields=[],
        source="agent",
        confidence=0.9,
    )
    replacement = replace(draft, proposed_postings=[Posting("acc_card", Decimal("11.08"), "USD")])

    with pytest.raises(ValidationError, match="debit/credit posting requires side"):
        drafts.supersede(draft.draft_id, draft.version, replacement)


def test_draft_book_supersede_rejects_new_legacy_signed_postings():
    drafts = DraftBook()
    draft = drafts.create(
        memo="card purchase draft",
        proposed_postings=[
            credit_posting("acc_card", Decimal("11.08"), "USD"),
            debit_posting("acc_expense", Decimal("11.08"), "USD"),
        ],
        missing_fields=[],
        source="agent",
        confidence=0.9,
    )
    replacement = replace(draft, proposed_postings=[legacy_signed_posting("acc_cash", Decimal("-5"), "USD")])

    with pytest.raises(ValidationError, match="new draft postings must use debit_credit semantics"):
        drafts.supersede(draft.draft_id, draft.version, replacement)


def test_draft_book_supersede_rejects_unbalanced_debit_credit_postings():
    drafts = DraftBook()
    draft = drafts.create(
        memo="card purchase draft",
        proposed_postings=[
            credit_posting("acc_card", Decimal("11.08"), "USD"),
            debit_posting("acc_expense", Decimal("11.08"), "USD"),
        ],
        missing_fields=[],
        source="agent",
        confidence=0.9,
    )
    replacement = replace(
        draft,
        proposed_postings=[
            credit_posting("acc_card", Decimal("11.08"), "USD"),
            debit_posting("acc_expense", Decimal("10.00"), "USD"),
        ],
    )

    with pytest.raises(ValidationError, match="draft postings must balance by currency"):
        drafts.supersede(draft.draft_id, draft.version, replacement)


def test_draft_book_projected_impact_skips_corrupted_loaded_debit_credit_shape():
    drafts = DraftBook()
    drafts.drafts["draft_dirty"] = DraftTransaction(
        draft_id="draft_dirty",
        memo="dirty loaded draft",
        state="ready_to_confirm",
        proposed_postings=[Posting("acc_card", Decimal("11.08"), "USD")],
        missing_fields=[],
        source="agent",
        confidence=0.9,
    )

    assert drafts.projected_impact("acc_card", account_type="liability") == {}
