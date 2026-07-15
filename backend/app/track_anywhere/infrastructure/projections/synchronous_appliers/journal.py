from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from ....domain.credit_cards.events import CreditCardTransactionRecorded
from ....domain.journal.events import (
    FinancialExternalReference,
    FinancialExternalReferenceCorrected,
    JournalPostingFact,
    JournalTransactionPosted,
    JournalTransactionReversed,
)
from ....domain.journal.models import PostingSide
from ....serialization.event_registry import PRODUCTION_EVENT_REGISTRY
from ...db.models.event_store import LedgerEventRecord
from ...db.models.projections import (
    AccountBalanceRecord,
    JournalPostingRecord,
    JournalTransactionRecord,
    TransactionExternalReferenceRecord,
    TransactionReversalRecord,
)
from .contracts import SynchronousProjectionError


def add_external_references(
    session: Session,
    stored: LedgerEventRecord,
    *,
    transaction_id: UUID,
    references: Sequence[FinancialExternalReference],
) -> None:
    session.add_all(
        [
            TransactionExternalReferenceRecord(
                book_id=stored.book_id,
                transaction_id=transaction_id,
                provider_code=reference.provider_code,
                reference_kind=reference.kind.value,
                reference_value=reference.reference,
                source_event_id=stored.event_id,
            )
            for reference in references
        ]
    )


def apply_financial_transaction(
    session: Session,
    stored: LedgerEventRecord,
    *,
    transaction_id: UUID,
    transaction_kind: str,
    description_ref: UUID | None,
    postings: Sequence[JournalPostingFact],
) -> None:
    transaction = JournalTransactionRecord(
        book_id=stored.book_id,
        transaction_id=transaction_id,
        source_event_id=stored.event_id,
        source_position=stored.book_position,
        effective_at=stored.effective_at,
        transaction_kind=transaction_kind,
        description_ref=description_ref,
    )
    session.add(transaction)
    session.flush([transaction])

    posting_records = [
        JournalPostingRecord(
            book_id=stored.book_id,
            transaction_id=transaction_id,
            posting_id=posting.posting_id,
            posting_position=posting.position,
            account_id=posting.account_id,
            asset_code=posting.asset_code,
            side=posting.side.value,
            units=int(posting.units),
        )
        for posting in postings
    ]
    session.add_all(posting_records)
    session.flush(posting_records)

    for posting in postings:
        signed_units = (
            int(posting.units) if posting.side.value == "debit" else -int(posting.units)
        )
        balance_insert = insert(AccountBalanceRecord).values(
            book_id=stored.book_id,
            account_id=posting.account_id,
            asset_code=posting.asset_code,
            balance_units=signed_units,
            as_of_position=stored.book_position,
        )
        session.execute(
            balance_insert.on_conflict_do_update(
                index_elements=(
                    AccountBalanceRecord.book_id,
                    AccountBalanceRecord.account_id,
                    AccountBalanceRecord.asset_code,
                ),
                set_={
                    "balance_units": AccountBalanceRecord.balance_units
                    + balance_insert.excluded.balance_units,
                    "as_of_position": balance_insert.excluded.as_of_position,
                },
            )
        )


def apply_journal_posted(
    session: Session,
    stored: LedgerEventRecord,
    payload: JournalTransactionPosted,
) -> None:
    apply_financial_transaction(
        session,
        stored,
        transaction_id=payload.transaction_id,
        transaction_kind=payload.kind.value,
        description_ref=payload.description_ref,
        postings=payload.postings,
    )
    add_external_references(
        session,
        stored,
        transaction_id=payload.transaction_id,
        references=payload.external_references,
    )


def _validate_exact_inverse(
    original_postings: Sequence[JournalPostingFact],
    inverse_postings: Sequence[JournalPostingFact],
) -> None:
    if len(original_postings) != len(inverse_postings):
        raise SynchronousProjectionError("reversal posting count does not match")
    if {posting.posting_id for posting in original_postings} & {
        posting.posting_id for posting in inverse_postings
    }:
        raise SynchronousProjectionError("reversal posting identities must be new")
    for original, inverse in zip(
        original_postings,
        inverse_postings,
        strict=True,
    ):
        expected_side = (
            PostingSide.CREDIT
            if original.side is PostingSide.DEBIT
            else PostingSide.DEBIT
        )
        if (
            inverse.position != original.position
            or inverse.account_id != original.account_id
            or inverse.asset_code != original.asset_code
            or inverse.units != original.units
            or inverse.side is not expected_side
        ):
            raise SynchronousProjectionError(
                "reversal postings are not the exact source inverse"
            )


def apply_journal_reversed(
    session: Session,
    stored: LedgerEventRecord,
    payload: JournalTransactionReversed,
) -> None:
    original_transaction = session.get(
        JournalTransactionRecord,
        (stored.book_id, payload.reverses_transaction_id),
    )
    if original_transaction is None:
        raise SynchronousProjectionError(
            "reversal target is not projected in the same Book"
        )
    if (
        original_transaction.source_event_id != payload.original_event_id
        or original_transaction.source_position >= stored.book_position
    ):
        raise SynchronousProjectionError(
            "reversal target does not precede its compensation event"
        )
    original_event = session.get(LedgerEventRecord, payload.original_event_id)
    if (
        original_event is None
        or original_event.book_id != stored.book_id
        or original_event.event_hash.hex() != payload.original_event_hash
    ):
        raise SynchronousProjectionError(
            "reversal provenance does not match the immutable source event"
        )
    original_payload = PRODUCTION_EVENT_REGISTRY.validate_stored(
        original_event.event_type,
        original_event.event_schema_version,
        original_event.payload,
    )
    if type(original_payload) is CreditCardTransactionRecorded:
        if original_payload.transaction_id != payload.reverses_transaction_id:
            raise SynchronousProjectionError(
                "reversal source transaction identity does not match"
            )
        original_postings = original_payload.postings
    elif type(original_payload) is JournalTransactionPosted:
        if original_payload.transaction_id != payload.reverses_transaction_id:
            raise SynchronousProjectionError(
                "reversal source transaction identity does not match"
            )
        original_postings = original_payload.postings
    elif type(original_payload) is JournalTransactionReversed:
        if original_payload.reversal_transaction_id != payload.reverses_transaction_id:
            raise SynchronousProjectionError(
                "reversal source transaction identity does not match"
            )
        original_postings = original_payload.inverse_postings
    else:
        raise SynchronousProjectionError(
            "reversal source is not a financial transaction event"
        )
    _validate_exact_inverse(original_postings, payload.inverse_postings)
    apply_financial_transaction(
        session,
        stored,
        transaction_id=payload.reversal_transaction_id,
        transaction_kind=original_transaction.transaction_kind,
        description_ref=payload.description_ref,
        postings=payload.inverse_postings,
    )
    session.add(
        TransactionReversalRecord(
            book_id=stored.book_id,
            reversal_transaction_id=payload.reversal_transaction_id,
            original_transaction_id=payload.reverses_transaction_id,
            source_event_id=stored.event_id,
            original_event_id=payload.original_event_id,
            original_event_hash=bytes.fromhex(payload.original_event_hash),
            reason_code=payload.reason_code.value,
        )
    )


def apply_external_reference_corrected(
    session: Session,
    stored: LedgerEventRecord,
    payload: FinancialExternalReferenceCorrected,
) -> None:
    if (
        stored.stream_type != "external_reference"
        or stored.stream_id != payload.transaction_id
    ):
        raise SynchronousProjectionError(
            "external-reference event identity does not match its transaction"
        )
    transaction = session.get(
        JournalTransactionRecord,
        (stored.book_id, payload.transaction_id),
    )
    if transaction is None:
        raise SynchronousProjectionError(
            "external-reference target is not projected in the same Book"
        )
    if transaction.source_position >= stored.book_position:
        raise SynchronousProjectionError(
            "external-reference target does not precede its correction"
        )
    key = (
        stored.book_id,
        payload.transaction_id,
        payload.provider_code,
        payload.reference_kind.value,
    )
    current = session.execute(
        select(TransactionExternalReferenceRecord)
        .where(
            TransactionExternalReferenceRecord.book_id == key[0],
            TransactionExternalReferenceRecord.transaction_id == key[1],
            TransactionExternalReferenceRecord.provider_code == key[2],
            TransactionExternalReferenceRecord.reference_kind == key[3],
        )
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if current is None:
        if payload.previous_reference is not None:
            raise SynchronousProjectionError(
                "external-reference correction previous value is stale"
            )
        session.add(
            TransactionExternalReferenceRecord(
                book_id=stored.book_id,
                transaction_id=payload.transaction_id,
                provider_code=payload.provider_code,
                reference_kind=payload.reference_kind.value,
                reference_value=payload.corrected_reference,
                source_event_id=stored.event_id,
            )
        )
        return
    if current.reference_value != payload.previous_reference:
        raise SynchronousProjectionError(
            "external-reference correction previous value is stale"
        )
    current.reference_value = payload.corrected_reference
    current.source_event_id = stored.event_id


__all__ = [
    "add_external_references",
    "apply_external_reference_corrected",
    "apply_financial_transaction",
    "apply_journal_posted",
    "apply_journal_reversed",
]
