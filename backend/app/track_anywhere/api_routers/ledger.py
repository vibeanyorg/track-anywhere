from __future__ import annotations

from fastapi import APIRouter

from ..api_dependencies import AuthToken, IdempotencyKey
from ..api_errors import raise_command_error
from ..api_service_ports import LedgerService
from ..api_serialization import serialize
from ..domain_commands import RecordFxExchangeCommand
from ..commands import (
    BalanceAdjustmentCommand,
    CaptureDraftCommand,
    ConfirmDraftCommand,
    ReclassifyTransactionCommand,
    RecordExpenseCommand,
    RecordIncomeCommand,
    RecordTransactionCommand,
    RejectDraftCommand,
    ReverseTransactionCommand,
    SupersedeDraftCommand,
)
from .common import COMMAND_ERRORS, command_payload, protected


router = APIRouter()


@router.post("/drafts/capture", dependencies=protected)
def capture_draft(payload: CaptureDraftCommand, token: AuthToken, key: IdempotencyKey, service: LedgerService):
    try:
        draft, replay = service.capture_draft(token, command_payload(payload), idempotency_key=key)
        return {"draft": serialize(draft), "idempotent_replay": replay}
    except COMMAND_ERRORS as exc:
        raise_command_error(exc, "draft.capture", recorder=service)


@router.get("/ledger/transactions", dependencies=protected)
def list_transactions(
    token: AuthToken,
    service: LedgerService,
    account_id: str | None = None,
    category_id: str | None = None,
    counterparty: str | None = None,
    limit: int = 20,
):
    try:
        transactions = service.list_transactions(
            token,
            account_id=account_id,
            category_id=category_id,
            counterparty=counterparty,
            limit=limit,
        )
        return {"transactions": serialize(transactions)}
    except COMMAND_ERRORS as exc:
        raise_command_error(exc, "ledger.transaction.list", recorder=service)


@router.post("/ledger/transactions", dependencies=protected)
def record_transaction(
    payload: RecordTransactionCommand,
    token: AuthToken,
    key: IdempotencyKey,
    service: LedgerService,
):
    try:
        transaction, replay = service.record_transaction(token, command_payload(payload), idempotency_key=key)
        return {"transaction": serialize(transaction), "idempotent_replay": replay}
    except COMMAND_ERRORS as exc:
        raise_command_error(exc, "ledger.transaction.record", recorder=service)


@router.post("/ledger/fx-exchanges", dependencies=protected)
def record_fx_exchange(payload: RecordFxExchangeCommand, token: AuthToken, key: IdempotencyKey, service: LedgerService):
    try:
        transaction, replay = service.record_fx_exchange(token, command_payload(payload), idempotency_key=key)
        return {"transaction": serialize(transaction), "idempotent_replay": replay}
    except COMMAND_ERRORS as exc:
        raise_command_error(exc, "ledger.fx.exchange", recorder=service)


@router.post("/expenses", dependencies=protected)
def record_expense(payload: RecordExpenseCommand, token: AuthToken, key: IdempotencyKey, service: LedgerService):
    try:
        transaction, replay = service.record_expense(token, command_payload(payload), idempotency_key=key)
        return {"transaction": serialize(transaction), "idempotent_replay": replay}
    except COMMAND_ERRORS as exc:
        raise_command_error(exc, "expense.record", recorder=service)


@router.post("/incomes", dependencies=protected)
def record_income(payload: RecordIncomeCommand, token: AuthToken, key: IdempotencyKey, service: LedgerService):
    try:
        transaction, replay = service.record_income(token, command_payload(payload), idempotency_key=key)
        return {"transaction": serialize(transaction), "idempotent_replay": replay}
    except COMMAND_ERRORS as exc:
        raise_command_error(exc, "income.record", recorder=service)


@router.get("/ledger/transactions/{transaction_id}", dependencies=protected)
def get_transaction(transaction_id: str, token: AuthToken, service: LedgerService):
    try:
        return {"transaction": serialize(service.get_transaction(token, transaction_id))}
    except COMMAND_ERRORS as exc:
        raise_command_error(exc, "ledger.transaction.get", recorder=service)


@router.get("/ledger/transactions/{transaction_id}/snapshot", dependencies=protected)
def get_transaction_snapshot(transaction_id: str, token: AuthToken, service: LedgerService):
    try:
        return {"snapshot": serialize(service.transaction_snapshot(token, transaction_id))}
    except COMMAND_ERRORS as exc:
        raise_command_error(exc, "ledger.transaction.snapshot", recorder=service)


@router.post("/ledger/adjustments", dependencies=protected)
def adjust_balance(payload: BalanceAdjustmentCommand, token: AuthToken, key: IdempotencyKey, service: LedgerService):
    try:
        transaction, replay = service.adjust_balance(token, command_payload(payload), idempotency_key=key)
        return {"transaction": serialize(transaction), "idempotent_replay": replay}
    except COMMAND_ERRORS as exc:
        raise_command_error(exc, "ledger.balance.adjust", recorder=service)


@router.post("/drafts/confirm", dependencies=protected)
def confirm_draft(payload: ConfirmDraftCommand, token: AuthToken, key: IdempotencyKey, service: LedgerService):
    try:
        transaction, replay = service.confirm_draft(token, command_payload(payload), idempotency_key=key)
        return {"transaction": serialize(transaction), "idempotent_replay": replay}
    except COMMAND_ERRORS as exc:
        raise_command_error(exc, "draft.confirm", recorder=service)


@router.post("/drafts/reject", dependencies=protected)
def reject_draft(payload: RejectDraftCommand, token: AuthToken, key: IdempotencyKey, service: LedgerService):
    try:
        draft, replay = service.reject_draft(token, command_payload(payload), idempotency_key=key)
        return {"draft": serialize(draft), "idempotent_replay": replay}
    except COMMAND_ERRORS as exc:
        raise_command_error(exc, "draft.reject", recorder=service)


@router.post("/drafts/supersede", dependencies=protected)
def supersede_draft(payload: SupersedeDraftCommand, token: AuthToken, key: IdempotencyKey, service: LedgerService):
    try:
        draft, replay = service.supersede_draft(token, command_payload(payload), idempotency_key=key)
        return {"draft": serialize(draft), "idempotent_replay": replay}
    except COMMAND_ERRORS as exc:
        raise_command_error(exc, "draft.supersede", recorder=service)


@router.post("/ledger/reverse", dependencies=protected)
def reverse_transaction(
    payload: ReverseTransactionCommand,
    token: AuthToken,
    key: IdempotencyKey,
    service: LedgerService,
):
    try:
        transaction, replay = service.reverse_transaction(token, command_payload(payload), idempotency_key=key)
        return {"transaction": serialize(transaction), "idempotent_replay": replay}
    except COMMAND_ERRORS as exc:
        raise_command_error(exc, "ledger.reverse", recorder=service)


@router.post("/ledger/reclassify", dependencies=protected)
def reclassify_transaction(
    payload: ReclassifyTransactionCommand,
    token: AuthToken,
    key: IdempotencyKey,
    service: LedgerService,
):
    try:
        transaction, replay = service.reclassify_transaction(token, command_payload(payload), idempotency_key=key)
        return {"transaction": serialize(transaction), "idempotent_replay": replay}
    except COMMAND_ERRORS as exc:
        raise_command_error(exc, "ledger.reclassify", recorder=service)


@router.get("/query/accounts/{account_id}/balance", dependencies=protected)
def account_balance(account_id: str, token: AuthToken, service: LedgerService, include_drafts: bool = False):
    try:
        return service.account_balance(token, account_id, include_drafts=include_drafts)
    except COMMAND_ERRORS as exc:
        raise_command_error(exc, "account.balance", recorder=service)
