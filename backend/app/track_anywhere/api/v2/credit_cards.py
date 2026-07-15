from __future__ import annotations

from collections.abc import Callable
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import AwareDatetime, ConfigDict, Field, StrictInt, StrictStr

from ...application.credit_cards.record import (
    ChargeCreditCardCommand,
    FeeCreditCardCommand,
    PaymentCreditCardCommand,
    RefundCreditCardCommand,
    execute_charge_credit_card,
    execute_fee_credit_card,
    execute_payment_credit_card,
    execute_refund_credit_card,
)
from ...application.ledger_committer import LedgerCommitter
from ...application.unit_of_work import UnitOfWork
from ...domain.journal.events import FinancialExternalReference
from ..dependencies import SessionDependency
from .schemas import (
    ExternalReferenceInput,
    RequestActor,
    StrictRequest,
    call_application,
    command_response,
    create_actor_dependency,
    require_idempotency_key,
)


PlainDecimal = Annotated[
    StrictStr,
    Field(pattern=r"^[0-9]+(?:\.[0-9]+)?$", min_length=1, max_length=96),
]
AssetCode = Annotated[
    StrictStr,
    Field(pattern=r"^[A-Z][A-Z0-9._-]{0,15}$"),
]
ZeroStreamVersion = Annotated[StrictInt, Field(ge=0, le=0)]
UnitOfWorkFactory = Callable[[], UnitOfWork]


class CreditCardStrictRequest(StrictRequest):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
    )

    command_id: UUID
    transaction_id: UUID
    expected_stream_version: ZeroStreamVersion = 0
    card_account_id: UUID
    asset_code: AssetCode
    amount: PlainDecimal
    effective_at: AwareDatetime
    description_ref: UUID | None = None
    external_references: tuple[ExternalReferenceInput, ...] = ()


class CreditCardChargeRequest(CreditCardStrictRequest):
    expense_account_id: UUID


class CreditCardPaymentRequest(CreditCardStrictRequest):
    source_account_id: UUID


class CreditCardRefundRequest(CreditCardStrictRequest):
    original_transaction_id: UUID


class CreditCardFeeRequest(CreditCardStrictRequest):
    expense_account_id: UUID


def create_credit_card_router(
    *,
    get_session: SessionDependency,
    uow_factory: UnitOfWorkFactory,
    ledger_committer: LedgerCommitter,
) -> APIRouter:
    router = APIRouter()
    request_actor = create_actor_dependency(get_session)

    @router.post("/books/{book_id}/credit-cards/charges", status_code=201)
    def record_charge(
        book_id: UUID,
        payload: CreditCardChargeRequest,
        actor: RequestActor = Depends(request_actor),
        raw_key: str = Depends(require_idempotency_key),
    ):
        command_actor = actor.require_book_scope(book_id, "ledger:write")
        outcome = call_application(
            lambda: execute_charge_credit_card(
                ChargeCreditCardCommand(
                    book_id=book_id,
                    command_id=payload.command_id,
                    transaction_id=payload.transaction_id,
                    expected_stream_version=payload.expected_stream_version,
                    card_account_id=payload.card_account_id,
                    expense_account_id=payload.expense_account_id,
                    asset_code=payload.asset_code,
                    amount=payload.amount,
                    effective_at=payload.effective_at,
                    description_ref=payload.description_ref,
                    external_references=_external_references(
                        payload.external_references
                    ),
                ),
                raw_key=raw_key,
                actor=command_actor,
                uow_factory=uow_factory,
                ledger_committer=ledger_committer,
            )
        )
        return command_response(outcome)

    @router.post("/books/{book_id}/credit-cards/payments", status_code=201)
    def record_payment(
        book_id: UUID,
        payload: CreditCardPaymentRequest,
        actor: RequestActor = Depends(request_actor),
        raw_key: str = Depends(require_idempotency_key),
    ):
        command_actor = actor.require_book_scope(book_id, "ledger:write")
        outcome = call_application(
            lambda: execute_payment_credit_card(
                PaymentCreditCardCommand(
                    book_id=book_id,
                    command_id=payload.command_id,
                    transaction_id=payload.transaction_id,
                    expected_stream_version=payload.expected_stream_version,
                    card_account_id=payload.card_account_id,
                    source_account_id=payload.source_account_id,
                    asset_code=payload.asset_code,
                    amount=payload.amount,
                    effective_at=payload.effective_at,
                    description_ref=payload.description_ref,
                    external_references=_external_references(
                        payload.external_references
                    ),
                ),
                raw_key=raw_key,
                actor=command_actor,
                uow_factory=uow_factory,
                ledger_committer=ledger_committer,
            )
        )
        return command_response(outcome)

    @router.post("/books/{book_id}/credit-cards/refunds", status_code=201)
    def record_refund(
        book_id: UUID,
        payload: CreditCardRefundRequest,
        actor: RequestActor = Depends(request_actor),
        raw_key: str = Depends(require_idempotency_key),
    ):
        command_actor = actor.require_book_scope(book_id, "ledger:write")
        outcome = call_application(
            lambda: execute_refund_credit_card(
                RefundCreditCardCommand(
                    book_id=book_id,
                    command_id=payload.command_id,
                    transaction_id=payload.transaction_id,
                    expected_stream_version=payload.expected_stream_version,
                    card_account_id=payload.card_account_id,
                    original_transaction_id=payload.original_transaction_id,
                    asset_code=payload.asset_code,
                    amount=payload.amount,
                    effective_at=payload.effective_at,
                    description_ref=payload.description_ref,
                    external_references=_external_references(
                        payload.external_references
                    ),
                ),
                raw_key=raw_key,
                actor=command_actor,
                uow_factory=uow_factory,
                ledger_committer=ledger_committer,
            )
        )
        return command_response(outcome)

    @router.post("/books/{book_id}/credit-cards/fees", status_code=201)
    def record_fee(
        book_id: UUID,
        payload: CreditCardFeeRequest,
        actor: RequestActor = Depends(request_actor),
        raw_key: str = Depends(require_idempotency_key),
    ):
        command_actor = actor.require_book_scope(book_id, "ledger:write")
        outcome = call_application(
            lambda: execute_fee_credit_card(
                FeeCreditCardCommand(
                    book_id=book_id,
                    command_id=payload.command_id,
                    transaction_id=payload.transaction_id,
                    expected_stream_version=payload.expected_stream_version,
                    card_account_id=payload.card_account_id,
                    expense_account_id=payload.expense_account_id,
                    asset_code=payload.asset_code,
                    amount=payload.amount,
                    effective_at=payload.effective_at,
                    description_ref=payload.description_ref,
                    external_references=_external_references(
                        payload.external_references
                    ),
                ),
                raw_key=raw_key,
                actor=command_actor,
                uow_factory=uow_factory,
                ledger_committer=ledger_committer,
            )
        )
        return command_response(outcome)

    return router


def _external_references(
    values: tuple[ExternalReferenceInput, ...],
) -> tuple[FinancialExternalReference, ...]:
    return tuple(
        FinancialExternalReference(
            provider_code=value.provider_code,
            kind=value.kind,
            reference=value.reference,
        )
        for value in values
    )


__all__ = [
    "CreditCardChargeRequest",
    "CreditCardFeeRequest",
    "CreditCardPaymentRequest",
    "CreditCardRefundRequest",
    "create_credit_card_router",
]
