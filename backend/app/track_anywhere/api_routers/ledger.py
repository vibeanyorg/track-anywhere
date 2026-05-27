from __future__ import annotations

from typing import Annotated, Any, Protocol

from fastapi import APIRouter, Depends

from ..api_dependencies import AuthToken, IdempotencyKey, get_service
from ..api_errors import raise_command_error
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


class LedgerRouteService(Protocol):
    def capture_draft(self, token, payload: dict[str, Any], *, idempotency_key: str): ...
    def list_transactions(self, token, **filters): ...
    def record_transaction(self, token, payload: dict[str, Any], *, idempotency_key: str): ...
    def record_fx_exchange(self, token, payload: dict[str, Any], *, idempotency_key: str): ...
    def record_expense(self, token, payload: dict[str, Any], *, idempotency_key: str): ...
    def record_income(self, token, payload: dict[str, Any], *, idempotency_key: str): ...
    def get_transaction(self, token, transaction_id: str): ...
    def transaction_snapshot(self, token, transaction_id: str): ...
    def adjust_balance(self, token, payload: dict[str, Any], *, idempotency_key: str): ...
    def confirm_draft(self, token, payload: dict[str, Any], *, idempotency_key: str): ...
    def reject_draft(self, token, payload: dict[str, Any], *, idempotency_key: str): ...
    def supersede_draft(self, token, payload: dict[str, Any], *, idempotency_key: str): ...
    def reverse_transaction(self, token, payload: dict[str, Any], *, idempotency_key: str): ...
    def reclassify_transaction(self, token, payload: dict[str, Any], *, idempotency_key: str): ...
    def account_balance(self, token, account_id: str, *, include_drafts: bool = False): ...
    def record_security_failure(self, operation: str, details: dict[str, Any]) -> None: ...


LedgerService = Annotated[LedgerRouteService, Depends(get_service)]


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
