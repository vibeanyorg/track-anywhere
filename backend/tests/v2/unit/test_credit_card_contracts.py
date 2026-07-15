from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from track_anywhere.application.credit_cards.record import (
    ChargeCreditCardCommand,
    PaymentCreditCardCommand,
    RefundCreditCardCommand,
)
from track_anywhere.application.idempotency import IdempotencyValidationError
from track_anywhere.domain.credit_cards.events import (
    CreditCardIntent,
    CreditCardTransactionRecorded,
)
from track_anywhere.domain.journal.events import JournalPostingFact
from track_anywhere.domain.journal.models import PostingSide


EFFECTIVE_AT = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)


def _ids() -> tuple[UUID, ...]:
    return tuple(uuid4() for _ in range(8))


def _postings(card_id: UUID, counter_id: UUID) -> tuple[JournalPostingFact, ...]:
    return (
        JournalPostingFact(
            posting_id=uuid4(),
            position=0,
            account_id=counter_id,
            asset_code="USD",
            side=PostingSide.DEBIT,
            units="1234",
        ),
        JournalPostingFact(
            posting_id=uuid4(),
            position=1,
            account_id=card_id,
            asset_code="USD",
            side=PostingSide.CREDIT,
            units="1234",
        ),
    )


def test_credit_card_event_is_immutable_and_requires_refund_provenance() -> None:
    _, transaction_id, card_id, expense_id, *_ = _ids()
    charge = CreditCardTransactionRecorded(
        intent=CreditCardIntent.CHARGE,
        transaction_id=transaction_id,
        card_account_id=card_id,
        counter_account_id=expense_id,
        original_transaction_id=None,
        postings=_postings(card_id, expense_id),
    )

    assert charge.model_dump(mode="json")["intent"] == "charge"
    with pytest.raises(ValidationError):
        CreditCardTransactionRecorded(
            intent=CreditCardIntent.REFUND,
            transaction_id=transaction_id,
            card_account_id=card_id,
            counter_account_id=expense_id,
            original_transaction_id=None,
            postings=_postings(card_id, expense_id),
        )
    with pytest.raises(ValidationError):
        charge.intent = CreditCardIntent.FEE  # type: ignore[misc]


@pytest.mark.parametrize("amount", ["0", "-1", "1e2", " 1", 12.34])
def test_commands_reject_non_positive_or_non_plain_decimal_amounts(
    amount: object,
) -> None:
    book_id, command_id, transaction_id, card_id, expense_id, *_ = _ids()
    with pytest.raises(IdempotencyValidationError):
        ChargeCreditCardCommand(
            book_id=book_id,
            command_id=command_id,
            transaction_id=transaction_id,
            expected_stream_version=0,
            card_account_id=card_id,
            expense_account_id=expense_id,
            asset_code="USD",
            amount=amount,  # type: ignore[arg-type]
            effective_at=EFFECTIVE_AT,
        )


def test_commands_expose_only_semantic_accounts_not_posting_sides() -> None:
    (
        book_id,
        command_id,
        transaction_id,
        card_id,
        source_id,
        original_id,
        *_rest,
    ) = _ids()
    payment = PaymentCreditCardCommand(
        book_id=book_id,
        command_id=command_id,
        transaction_id=transaction_id,
        expected_stream_version=0,
        card_account_id=card_id,
        source_account_id=source_id,
        asset_code="USD",
        amount="10.00",
        effective_at=EFFECTIVE_AT,
    )
    refund = RefundCreditCardCommand(
        book_id=book_id,
        command_id=uuid4(),
        transaction_id=uuid4(),
        expected_stream_version=0,
        card_account_id=card_id,
        original_transaction_id=original_id,
        asset_code="USD",
        amount="2.00",
        effective_at=EFFECTIVE_AT,
    )

    assert "side" not in payment.idempotency_payload()
    assert refund.original_transaction_id == original_id
