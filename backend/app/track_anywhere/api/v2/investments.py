from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from ...application.investments.acquire_lot import (
    AcquireLotCommand,
    execute_acquire_lot,
)
from ...application.investments.dispose_lot import (
    DisposeLotCommand,
    execute_dispose_lot,
)
from ...application.ledger_committer import LedgerCommitter
from ...domain.investments.allocation import SpecificLotRequest
from ..dependencies import SessionDependency, UnitOfWorkFactory
from .schemas import (
    AcquireLotRequest,
    DisposeLotRequest,
    RequestActor,
    call_application,
    command_response,
    create_actor_dependency,
    require_idempotency_key,
)


def create_investment_router(
    *,
    get_session: SessionDependency,
    uow_factory: UnitOfWorkFactory,
    ledger_committer: LedgerCommitter,
) -> APIRouter:
    router = APIRouter(tags=["investments"])
    request_actor = create_actor_dependency(get_session)

    @router.post("/books/{book_id}/investments/lots/acquire", status_code=201)
    def acquire_lot(
        book_id: UUID,
        payload: AcquireLotRequest,
        actor: RequestActor = Depends(request_actor),
        raw_key: str = Depends(require_idempotency_key),
    ) -> JSONResponse:
        command_actor = actor.require_book_scope(book_id, "ledger:write")
        outcome = call_application(
            lambda: execute_acquire_lot(
                AcquireLotCommand(
                    book_id=book_id,
                    command_id=payload.command_id,
                    transaction_id=payload.transaction_id,
                    lot_id=payload.lot_id,
                    instrument_asset_code=payload.instrument_asset_code,
                    settlement_asset_code=payload.settlement_asset_code,
                    quantity_units=payload.quantity_units,
                    cost_units=payload.cost_units,
                    effective_at=payload.effective_at,
                    fee_units=payload.fee_units,
                    expected_stream_version=payload.expected_stream_version,
                ),
                raw_key=raw_key,
                actor=command_actor,
                uow_factory=uow_factory,
                ledger_committer=ledger_committer,
            )
        )
        return command_response(outcome)

    @router.post("/books/{book_id}/investments/lots/dispose", status_code=201)
    def dispose_lot(
        book_id: UUID,
        payload: DisposeLotRequest,
        actor: RequestActor = Depends(request_actor),
        raw_key: str = Depends(require_idempotency_key),
    ) -> JSONResponse:
        command_actor = actor.require_book_scope(book_id, "ledger:write")
        outcome = call_application(
            lambda: execute_dispose_lot(
                DisposeLotCommand(
                    book_id=book_id,
                    command_id=payload.command_id,
                    transaction_id=payload.transaction_id,
                    instrument_asset_code=payload.instrument_asset_code,
                    settlement_asset_code=payload.settlement_asset_code,
                    quantity_units=payload.quantity_units,
                    proceeds_units=payload.proceeds_units,
                    allocation_method=payload.allocation_method,
                    effective_at=payload.effective_at,
                    fee_units=payload.fee_units,
                    specific_lots=tuple(
                        SpecificLotRequest(
                            lot_id=lot.lot_id,
                            quantity_units=int(lot.quantity_units),
                        )
                        for lot in payload.specific_lots
                    ),
                    expected_stream_version=payload.expected_stream_version,
                ),
                raw_key=raw_key,
                actor=command_actor,
                uow_factory=uow_factory,
                ledger_committer=ledger_committer,
            )
        )
        return command_response(outcome)

    return router


__all__ = ["create_investment_router"]
