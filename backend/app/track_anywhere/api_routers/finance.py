from __future__ import annotations

from fastapi import APIRouter, File, UploadFile

from ..api_dependencies import AuthToken, IdempotencyKey
from ..api_errors import raise_command_error
from ..api_service_ports import FinanceService
from ..api_serialization import serialize
from ..commands import (
    CreateFundCommand,
    FundAllocationCommand,
    FundSpendCommand,
    ReconciliationActionCommand,
    RecordInvestmentEventCommand,
)
from ..domain_commands import RecordInvestmentValuationCommand
from .common import COMMAND_ERRORS, command_payload, protected, read_upload_with_limit


router = APIRouter()


@router.post("/investments/events", dependencies=protected)
def record_investment_event(
    payload: RecordInvestmentEventCommand,
    token: AuthToken,
    key: IdempotencyKey,
    service: FinanceService,
):
    try:
        event, replay = service.record_investment_event(token, command_payload(payload), idempotency_key=key)
        return {"event": serialize(event), "idempotent_replay": replay}
    except COMMAND_ERRORS as exc:
        raise_command_error(exc, "investment.event.record", recorder=service)


@router.post("/investments/valuations", dependencies=protected)
def record_investment_valuation(
    payload: RecordInvestmentValuationCommand,
    token: AuthToken,
    key: IdempotencyKey,
    service: FinanceService,
):
    try:
        valuation, replay = service.record_investment_valuation(token, command_payload(payload), idempotency_key=key)
        return {"valuation": serialize(valuation), "idempotent_replay": replay}
    except COMMAND_ERRORS as exc:
        raise_command_error(exc, "investment.valuation.record", recorder=service)


@router.get("/investments/accounts/{account_id}/valuations", dependencies=protected)
def list_investment_valuations(account_id: str, token: AuthToken, service: FinanceService):
    try:
        return {"valuations": serialize(service.list_investment_valuations(token, account_id))}
    except COMMAND_ERRORS as exc:
        raise_command_error(exc, "investment.valuation.list", recorder=service)


@router.get("/investments/accounts/{account_id}/performance", dependencies=protected)
def investment_performance(
    account_id: str,
    token: AuthToken,
    service: FinanceService,
    as_of: str | None = None,
):
    try:
        return service.investment_performance(token, account_id, as_of=as_of)
    except COMMAND_ERRORS as exc:
        raise_command_error(exc, "investment.performance", recorder=service)


@router.post("/funds", dependencies=protected)
def create_fund(payload: CreateFundCommand, token: AuthToken, key: IdempotencyKey, service: FinanceService):
    try:
        fund, replay = service.create_fund(token, command_payload(payload), idempotency_key=key)
        return {"fund": serialize(fund), "idempotent_replay": replay}
    except COMMAND_ERRORS as exc:
        raise_command_error(exc, "fund.create", recorder=service)


@router.post("/funds/allocate", dependencies=protected)
def allocate_fund(payload: FundAllocationCommand, token: AuthToken, key: IdempotencyKey, service: FinanceService):
    try:
        result, replay = service.allocate_fund(token, command_payload(payload), idempotency_key=key)
        return {"result": serialize(result), "idempotent_replay": replay}
    except COMMAND_ERRORS as exc:
        raise_command_error(exc, "fund.allocate", recorder=service)


@router.post("/funds/spend", dependencies=protected)
def spend_fund(payload: FundSpendCommand, token: AuthToken, key: IdempotencyKey, service: FinanceService):
    try:
        result, replay = service.spend_fund(token, command_payload(payload), idempotency_key=key)
        return {"result": serialize(result), "idempotent_replay": replay}
    except COMMAND_ERRORS as exc:
        raise_command_error(exc, "fund.spend", recorder=service)


@router.post("/attachments", dependencies=protected)
async def upload_attachment(
    token: AuthToken,
    key: IdempotencyKey,
    service: FinanceService,
    file: UploadFile = File(...),
):
    try:
        content = await read_upload_with_limit(file)
        result, replay = service.upload_attachment(
            token,
            filename=file.filename or "attachment",
            mime_type=file.content_type or "application/octet-stream",
            content=content,
            idempotency_key=key,
        )
        return {"result": serialize(result), "idempotent_replay": replay}
    except COMMAND_ERRORS as exc:
        raise_command_error(exc, "attachment.upload", recorder=service)


@router.post("/reconciliation/actions", dependencies=protected)
def record_reconciliation(
    payload: ReconciliationActionCommand,
    token: AuthToken,
    key: IdempotencyKey,
    service: FinanceService,
):
    try:
        action, replay = service.record_reconciliation_action(token, command_payload(payload), idempotency_key=key)
        return {"action": serialize(action), "idempotent_replay": replay}
    except COMMAND_ERRORS as exc:
        raise_command_error(exc, "reconciliation.record", recorder=service)
