from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from ...application.journal.assign_reporting_lines import (
    AssignReportingLinesCommand,
    ReportingLineInput,
    execute_assign_reporting_lines,
)
from ...application.journal.clear_reporting_lines import (
    ClearReportingLinesCommand,
    execute_clear_reporting_lines,
)
from ...application.ledger_committer import LedgerCommitter
from ..dependencies import SessionDependency, UnitOfWorkFactory
from .schemas import (
    AssignReportingLinesRequest,
    ClearReportingLinesRequest,
    RequestActor,
    call_application,
    command_response,
    create_actor_dependency,
    require_idempotency_key,
)


def create_reporting_router(
    *,
    get_session: SessionDependency,
    uow_factory: UnitOfWorkFactory,
    ledger_committer: LedgerCommitter,
) -> APIRouter:
    router = APIRouter(tags=["reporting"])
    request_actor = create_actor_dependency(get_session)

    @router.post(
        "/books/{book_id}/journal/transactions/{transaction_id}/reporting-lines/assign",
        status_code=201,
    )
    def assign_reporting_lines(
        book_id: UUID,
        transaction_id: UUID,
        payload: AssignReportingLinesRequest,
        actor: RequestActor = Depends(request_actor),
        raw_key: str = Depends(require_idempotency_key),
    ) -> JSONResponse:
        command_actor = actor.require_book_scope(book_id, "ledger:write")
        lines = tuple(
            ReportingLineInput(
                line_id=line.line_id,
                line_version_id=line.line_version_id,
                catalog_id=line.catalog_id,
                asset_code=line.asset_code,
                units=line.units,
                line_kind=line.line_kind,
                dimension=line.dimension,
                dimension_id=line.dimension_id,
                description_ref=line.description_ref,
            )
            for line in payload.lines
        )
        outcome = call_application(
            lambda: execute_assign_reporting_lines(
                AssignReportingLinesCommand(
                    book_id=book_id,
                    command_id=payload.command_id,
                    transaction_id=transaction_id,
                    expected_revision=payload.expected_revision,
                    lines=lines,
                    effective_at=payload.effective_at,
                ),
                raw_key=raw_key,
                actor=command_actor,
                uow_factory=uow_factory,
                ledger_committer=ledger_committer,
            )
        )
        return command_response(outcome)

    @router.post(
        "/books/{book_id}/journal/transactions/{transaction_id}/reporting-lines/clear",
        status_code=201,
    )
    def clear_reporting_lines(
        book_id: UUID,
        transaction_id: UUID,
        payload: ClearReportingLinesRequest,
        actor: RequestActor = Depends(request_actor),
        raw_key: str = Depends(require_idempotency_key),
    ) -> JSONResponse:
        command_actor = actor.require_book_scope(book_id, "ledger:write")
        outcome = call_application(
            lambda: execute_clear_reporting_lines(
                ClearReportingLinesCommand(
                    book_id=book_id,
                    command_id=payload.command_id,
                    transaction_id=transaction_id,
                    expected_revision=payload.expected_revision,
                    effective_at=payload.effective_at,
                ),
                raw_key=raw_key,
                actor=command_actor,
                uow_factory=uow_factory,
                ledger_committer=ledger_committer,
            )
        )
        return command_response(outcome)

    return router


__all__ = ["create_reporting_router"]
