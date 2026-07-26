from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from track_anywhere.application.entries.contracts import (
    AccountRef,
    AdjustmentEntryInput,
    CommitEntryInput,
    ExpenseEntryInput,
    PreparedEntryStatus,
    RefundEntryInput,
)
from track_anywhere.application.entries.errors import (
    EntryErrorCode,
    EntryGatewayError,
)
from track_anywhere.application.entries.prepare import restore_entry
from track_anywhere.application.privacy.protected_content import (
    NarrativeAmountSource,
)
from track_anywhere.api.v2.entries import _entry_status
from track_anywhere.infrastructure.db.repositories.entries import (
    ProposedPreparedIntent,
    hash_commit_token,
)


def test_restore_entry_rehydrates_the_original_protected_source_text() -> None:
    account_id = uuid4()
    restored = restore_entry(
        {
            "kind": "expense",
            "occurred_at": "2026-07-24T12:30:00Z",
            "narrative": None,
            "amount": {
                "value": "660",
                "denomination": "asset_unit",
                "asset_code": "CNY",
            },
            "category": {"category_id": str(uuid4())},
            "category_allocations": [],
            "source_account": {"account_id": str(account_id)},
        },
        amount_sources=(
            NarrativeAmountSource(
                field_path="amount",
                source_text="original private OCR text",
            ),
        ),
    )
    assert isinstance(restored, ExpenseEntryInput)
    assert restored.source_account == AccountRef(account_id=account_id)
    assert restored.amount.source_text == "original private OCR text"


def test_restore_entry_rehydrates_each_nested_amount_source_exactly() -> None:
    restored = restore_entry(
        {
            "kind": "expense",
            "occurred_at": "2026-07-24T12:30:00Z",
            "amount": {
                "value": "10.00",
                "denomination": "asset_unit",
                "asset_code": "USD",
            },
            "narrative": {
                "gross_amount": {
                    "value": "1200",
                    "denomination": "minor_unit",
                    "asset_code": "USD",
                },
                "discount_amount": {
                    "value": "2.00",
                    "denomination": "asset_unit",
                    "asset_code": "USD",
                },
            },
            "category": {"category_id": str(uuid4())},
            "category_allocations": [],
            "source_account": {"account_id": str(uuid4())},
        },
        amount_sources=(
            NarrativeAmountSource(
                field_path="amount",
                source_text="net amount source",
            ),
            NarrativeAmountSource(
                field_path="narrative.gross_amount",
                source_text="gross amount in cents",
            ),
            NarrativeAmountSource(
                field_path="narrative.discount_amount",
                source_text="discount amount source",
            ),
        ),
    )
    assert isinstance(restored, ExpenseEntryInput)
    assert restored.amount.source_text == "net amount source"
    assert restored.narrative is not None
    assert restored.narrative.gross_amount is not None
    assert restored.narrative.gross_amount.denomination.value == "minor_unit"
    assert restored.narrative.gross_amount.source_text == "gross amount in cents"
    assert restored.narrative.discount_amount is not None
    assert (
        restored.narrative.discount_amount.source_text
        == "discount amount source"
    )


def test_restore_adjustment_rehydrates_balance_source_path() -> None:
    restored = restore_entry(
        {
            "kind": "adjustment",
            "occurred_at": "2026-07-24T12:30:00Z",
            "narrative": None,
            "account": {"account_id": str(uuid4())},
            "actual_balance": {
                "value": "0",
                "denomination": "minor_unit",
                "asset_code": "USD",
            },
        },
        amount_sources=(
            NarrativeAmountSource(
                field_path="actual_balance",
                source_text="observed zero cents",
            ),
        ),
    )
    assert isinstance(restored, AdjustmentEntryInput)
    assert restored.actual_balance.source_text == "observed zero cents"


@pytest.mark.parametrize(
    "amount_sources",
    (
        (),
        (
            NarrativeAmountSource(
                field_path="amount",
                source_text="original private OCR text",
            ),
            NarrativeAmountSource(
                field_path="amount",
                source_text="duplicate private OCR text",
            ),
        ),
        (
            NarrativeAmountSource(
                field_path="actual_balance",
                source_text="unexpected private balance text",
            ),
        ),
    ),
)
def test_restore_entry_rejects_missing_duplicate_or_unexpected_source_paths(
    amount_sources: tuple[NarrativeAmountSource, ...],
) -> None:
    with pytest.raises(EntryGatewayError) as raised:
        restore_entry(
            {
                "kind": "transfer",
                "occurred_at": "2026-07-24T12:30:00Z",
                "narrative": None,
                "amount": {
                    "value": "660",
                    "denomination": "minor_unit",
                    "asset_code": "CNY",
                },
                "source_account": {"account_id": str(uuid4())},
                "destination_account": {"account_id": str(uuid4())},
            },
            amount_sources=amount_sources,
        )
    assert raised.value.code is EntryErrorCode.INTENT_STALE


def test_restore_full_refund_requires_no_invented_amount_source() -> None:
    original_id = uuid4()
    restored = restore_entry(
        {
            "kind": "refund",
            "occurred_at": "2026-07-24T12:30:00Z",
            "narrative": None,
            "original_transaction_id": str(original_id),
            "amount": None,
            "category_allocations": [],
        },
        amount_sources=(),
    )
    assert isinstance(restored, RefundEntryInput)
    assert restored.original_transaction_id == original_id
    assert restored.amount is None


def test_prepared_payload_storage_rejects_original_source_text() -> None:
    with pytest.raises(ValueError, match="private entry fields"):
        ProposedPreparedIntent(
            book_id=uuid4(),
            actor_id="human:entry",
            intent_id=uuid4(),
            prepared_status=PreparedEntryStatus.READY.value,
            commit_token_hash=hash_commit_token("x" * 32),
            canonical_payload={
                "entry": {
                    "amount": {
                        "value": "660",
                        "source_text": "private screenshot text",
                    }
                }
            },
            expires_at=datetime(2099, 1, 1, tzinfo=UTC),
        )


@pytest.mark.parametrize(
    ("code", "status"),
    (
        (EntryErrorCode.INTENT_NOT_FOUND, 404),
        (EntryErrorCode.INTENT_EXPIRED, 410),
        (EntryErrorCode.INTENT_STALE, 409),
        (EntryErrorCode.DUPLICATE_SUSPECTED, 409),
        (EntryErrorCode.AMOUNT_INVALID, 422),
    ),
)
def test_entry_errors_have_stable_http_statuses(
    code: EntryErrorCode,
    status: int,
) -> None:
    assert _entry_status(EntryGatewayError(code, "safe")) == status


def test_commit_contract_cannot_retransmit_business_fields() -> None:
    fields = set(CommitEntryInput.model_fields)
    assert fields == {"intent_id", "commit_token", "request_id"}
    assert "amount" not in fields
    assert "account" not in fields
    assert "category" not in fields
