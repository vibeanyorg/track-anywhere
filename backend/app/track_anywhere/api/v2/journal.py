from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from ...application.journal.correct_external_reference import (
    CorrectExternalReferenceCommand,
    execute_correct_external_reference,
)
from ...application.journal.correct_transaction import (
    CorrectionReplacement,
    CorrectTransactionCommand,
    execute_correct_transaction,
)
from ...application.journal.post_transaction import (
    PostTransactionCommand,
    PostTransactionPosting,
    execute_post_transaction,
)
from ...application.journal.record_fx import RecordFxCommand, execute_record_fx
from ...application.journal.reverse_transaction import (
    ReverseTransactionCommand,
    execute_reverse_transaction,
)
from ...application.ledger_committer import LedgerCommitter
from ...domain.journal.events import FinancialExternalReference
from ..dependencies import SessionDependency, UnitOfWorkFactory
from .schemas import (
    CorrectExternalReferenceRequest,
    CorrectTransactionRequest,
    ExternalReferenceInput,
    JournalPostingInput,
    PostTransactionRequest,
    RecordFxRequest,
    RequestActor,
    ReverseTransactionRequest,
    call_application,
    command_response,
    create_actor_dependency,
    require_idempotency_key,
)


def create_journal_router(
    *,
    get_session: SessionDependency,
    uow_factory: UnitOfWorkFactory,
    ledger_committer: LedgerCommitter,
) -> APIRouter:
    router = APIRouter(tags=["journal"])
    request_actor = create_actor_dependency(get_session)

    @router.post("/books/{book_id}/journal/transactions", status_code=201)
    def post_transaction(
        book_id: UUID,
        payload: PostTransactionRequest,
        actor: RequestActor = Depends(request_actor),
        raw_key: str = Depends(require_idempotency_key),
    ) -> JSONResponse:
        command_actor = actor.require_book_scope(book_id, "ledger:write")
        outcome = call_application(
            lambda: execute_post_transaction(
                PostTransactionCommand(
                    book_id=book_id,
                    command_id=payload.command_id,
                    transaction_id=payload.transaction_id,
                    expected_stream_version=payload.expected_stream_version,
                    kind=payload.kind,
                    postings=_postings(payload.postings),
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

    @router.post(
        "/books/{book_id}/journal/transactions/{transaction_id}/reverse",
        status_code=201,
    )
    def reverse_transaction(
        book_id: UUID,
        transaction_id: UUID,
        payload: ReverseTransactionRequest,
        actor: RequestActor = Depends(request_actor),
        raw_key: str = Depends(require_idempotency_key),
    ) -> JSONResponse:
        command_actor = actor.require_book_scope(book_id, "ledger:write")
        outcome = call_application(
            lambda: execute_reverse_transaction(
                ReverseTransactionCommand(
                    book_id=book_id,
                    command_id=payload.command_id,
                    reversal_transaction_id=payload.reversal_transaction_id,
                    reverses_transaction_id=transaction_id,
                    expected_stream_version=payload.expected_stream_version,
                    reason_code=payload.reason_code,
                    effective_at=payload.effective_at,
                    description_ref=payload.description_ref,
                ),
                raw_key=raw_key,
                actor=command_actor,
                uow_factory=uow_factory,
                ledger_committer=ledger_committer,
            )
        )
        return command_response(outcome)

    @router.post(
        "/books/{book_id}/journal/transactions/{transaction_id}/correct",
        status_code=201,
    )
    def correct_transaction(
        book_id: UUID,
        transaction_id: UUID,
        payload: CorrectTransactionRequest,
        actor: RequestActor = Depends(request_actor),
        raw_key: str = Depends(require_idempotency_key),
    ) -> JSONResponse:
        command_actor = actor.require_book_scope(book_id, "ledger:write")
        replacement = payload.replacement
        outcome = call_application(
            lambda: execute_correct_transaction(
                CorrectTransactionCommand(
                    book_id=book_id,
                    command_id=payload.command_id,
                    reverses_transaction_id=transaction_id,
                    reversal_transaction_id=payload.reversal_transaction_id,
                    expected_reversal_stream_version=(
                        payload.expected_reversal_stream_version
                    ),
                    reason_code=payload.reason_code,
                    reversal_effective_at=payload.reversal_effective_at,
                    replacement=CorrectionReplacement(
                        transaction_id=replacement.transaction_id,
                        expected_stream_version=(replacement.expected_stream_version),
                        kind=replacement.kind,
                        postings=_postings(replacement.postings),
                        effective_at=replacement.effective_at,
                        description_ref=replacement.description_ref,
                        external_references=_external_references(
                            replacement.external_references
                        ),
                    ),
                    reversal_description_ref=payload.reversal_description_ref,
                ),
                raw_key=raw_key,
                actor=command_actor,
                uow_factory=uow_factory,
                ledger_committer=ledger_committer,
            )
        )
        return command_response(outcome)

    @router.post(
        "/books/{book_id}/journal/transactions/{transaction_id}"
        "/external-references/correct",
        status_code=201,
    )
    def correct_external_reference(
        book_id: UUID,
        transaction_id: UUID,
        payload: CorrectExternalReferenceRequest,
        actor: RequestActor = Depends(request_actor),
        raw_key: str = Depends(require_idempotency_key),
    ) -> JSONResponse:
        command_actor = actor.require_book_scope(book_id, "ledger:write")
        outcome = call_application(
            lambda: execute_correct_external_reference(
                CorrectExternalReferenceCommand(
                    book_id=book_id,
                    command_id=payload.command_id,
                    transaction_id=transaction_id,
                    provider_code=payload.provider_code,
                    reference_kind=payload.reference_kind,
                    corrected_reference=payload.corrected_reference,
                    expected_stream_version=payload.expected_stream_version,
                    effective_at=payload.effective_at,
                ),
                raw_key=raw_key,
                actor=command_actor,
                uow_factory=uow_factory,
                ledger_committer=ledger_committer,
            )
        )
        return command_response(outcome)

    @router.post("/books/{book_id}/journal/fx", status_code=201)
    def record_fx(
        book_id: UUID,
        payload: RecordFxRequest,
        actor: RequestActor = Depends(request_actor),
        raw_key: str = Depends(require_idempotency_key),
    ) -> JSONResponse:
        command_actor = actor.require_book_scope(book_id, "ledger:write")
        outcome = call_application(
            lambda: execute_record_fx(
                RecordFxCommand(
                    book_id=book_id,
                    command_id=payload.command_id,
                    transaction_id=payload.transaction_id,
                    expected_stream_version=payload.expected_stream_version,
                    source_account_id=payload.source_account_id,
                    source_trading_account_id=payload.source_trading_account_id,
                    source_asset_code=payload.source_asset_code,
                    source_amount=payload.source_amount,
                    target_trading_account_id=payload.target_trading_account_id,
                    target_account_id=payload.target_account_id,
                    target_asset_code=payload.target_asset_code,
                    target_amount=payload.target_amount,
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


def _postings(
    values: tuple[JournalPostingInput, ...],
) -> tuple[PostTransactionPosting, ...]:
    return tuple(
        PostTransactionPosting(
            posting_id=value.posting_id,
            account_id=value.account_id,
            asset_code=value.asset_code,
            side=value.side,
            amount=value.amount,
        )
        for value in values
    )


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


__all__ = ["create_journal_router"]
