from __future__ import annotations

from sqlalchemy.orm import Session

from ....domain.credit_cards.events import CreditCardTransactionRecorded
from ...db.models.credit_cards import CreditCardTransactionRecord
from ...db.models.event_store import LedgerEventRecord
from .journal import add_external_references, apply_financial_transaction


def apply_credit_card_recorded(
    session: Session,
    stored: LedgerEventRecord,
    payload: CreditCardTransactionRecorded,
) -> None:
    apply_financial_transaction(
        session,
        stored,
        transaction_id=payload.transaction_id,
        transaction_kind=payload.intent.transaction_kind,
        description_ref=payload.description_ref,
        postings=payload.postings,
    )
    add_external_references(
        session,
        stored,
        transaction_id=payload.transaction_id,
        references=payload.external_references,
    )
    session.add(
        CreditCardTransactionRecord(
            book_id=stored.book_id,
            transaction_id=payload.transaction_id,
            intent=payload.intent.value,
            card_account_id=payload.card_account_id,
            counter_account_id=payload.counter_account_id,
            asset_code=payload.postings[0].asset_code,
            units=int(payload.postings[0].units),
            original_transaction_id=payload.original_transaction_id,
            source_event_id=stored.event_id,
            source_position=stored.book_position,
        )
    )


__all__ = ["apply_credit_card_recorded"]
