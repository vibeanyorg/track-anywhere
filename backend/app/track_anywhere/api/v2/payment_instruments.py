from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import AwareDatetime, BaseModel, Field, StrictStr
from sqlalchemy.orm import Session

from ...application.payment_instruments import (
    CardFormFactor,
    CardNetwork,
    CreatePaymentInstrument,
    PaymentInstrumentView,
    SettlementPolicy,
    create_payment_instrument,
    get_payment_instrument,
    list_payment_instruments,
)
from ...application.payment_instruments.contracts import PaymentInstrumentMutation
from ...application.payment_instruments.service import mutate_payment_instrument
from ..dependencies import SessionDependency, UnitOfWorkFactory
from .query_routes.authorization import authorize_book_read
from .schemas import (
    AssetCode,
    NonBlankText,
    ProviderCode,
    RequestActor,
    StrictRequest,
    call_application,
    create_actor_dependency,
)


class PaymentInstrumentMutationResponse(BaseModel):
    request_id: UUID
    committed: Literal[True] = True
    replayed: bool
    verification_status: Literal["verified"] = "verified"
    instrument: PaymentInstrumentView


class CreatePaymentInstrumentRequest(StrictRequest):
    instrument_id: UUID
    binding_id: UUID
    current_name: NonBlankText
    form_factor: CardFormFactor
    network: CardNetwork
    provider_code: ProviderCode
    settlement_policy: SettlementPolicy
    settlement_account_id: UUID
    asset_code: AssetCode
    last4: Annotated[StrictStr, Field(pattern=r"^[0-9]{4}$")] | None = None
    effective_from: AwareDatetime


def create_payment_instrument_router(
    *,
    get_session: SessionDependency,
    uow_factory: UnitOfWorkFactory,
) -> APIRouter:
    router = APIRouter(tags=["payment-instruments"])
    request_actor = create_actor_dependency(get_session)

    @router.post(
        "/books/{book_id}/payment-instruments",
        response_model=PaymentInstrumentView,
        status_code=201,
    )
    def create(
        book_id: UUID,
        payload: CreatePaymentInstrumentRequest,
        actor: RequestActor = Depends(request_actor),
    ) -> PaymentInstrumentView:
        command_actor = actor.require_book_scope(book_id, "book:write")
        return call_application(
            lambda: create_payment_instrument(
                CreatePaymentInstrument(
                    book_id=book_id,
                    instrument_id=payload.instrument_id,
                    binding_id=payload.binding_id,
                    current_name=payload.current_name,
                    form_factor=payload.form_factor,
                    network=payload.network,
                    provider_code=payload.provider_code,
                    settlement_policy=payload.settlement_policy,
                    settlement_account_id=payload.settlement_account_id,
                    asset_code=payload.asset_code,
                    last4=payload.last4,
                    effective_from=payload.effective_from,
                ),
                actor=command_actor,
                uow_factory=uow_factory,
            )
        )

    @router.post(
        "/books/{book_id}/payment-instruments/{instrument_id}/mutations",
        response_model=PaymentInstrumentMutationResponse,
    )
    def mutate(
        book_id: UUID,
        instrument_id: UUID,
        payload: PaymentInstrumentMutation,
        actor: RequestActor = Depends(request_actor),
    ) -> PaymentInstrumentMutationResponse:
        command_actor = actor.require_book_scope(book_id, "book:write")
        if payload.book_id != book_id or payload.instrument_id != instrument_id:
            raise HTTPException(
                status_code=422, detail="path and command identifiers must match"
            )
        instrument, replayed = call_application(
            lambda: mutate_payment_instrument(
                payload, actor=command_actor, uow_factory=uow_factory
            )
        )
        return PaymentInstrumentMutationResponse(
            request_id=payload.request_id, replayed=replayed, instrument=instrument
        )

    @router.get(
        "/books/{book_id}/payment-instruments",
        response_model=tuple[PaymentInstrumentView, ...],
    )
    def list_all(
        book_id: UUID,
        request: Request,
        status: Literal["active", "frozen", "closed", "inactive", "all"] | None = None,
        asset_code: AssetCode | None = None,
        name: str | None = None,
        session: Session = Depends(get_session),
    ) -> tuple[PaymentInstrumentView, ...]:
        authorize_book_read(session, request, book_id)
        return call_application(
            lambda: list_payment_instruments(
                session,
                book_id=book_id,
                status=status,
                asset_code=asset_code,
                name=name,
            )
        )

    @router.get(
        "/books/{book_id}/payment-instruments/{instrument_id}",
        response_model=PaymentInstrumentView,
    )
    def get_one(
        book_id: UUID,
        instrument_id: UUID,
        request: Request,
        session: Session = Depends(get_session),
    ) -> PaymentInstrumentView:
        authorize_book_read(session, request, book_id)
        return call_application(
            lambda: get_payment_instrument(
                session,
                book_id=book_id,
                instrument_id=instrument_id,
            )
        )

    return router


__all__ = [
    "CreatePaymentInstrumentRequest",
    "create_payment_instrument_router",
]
