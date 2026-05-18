from __future__ import annotations

from fastapi import APIRouter, File, UploadFile

from ..api_dependencies import AuthToken, IdempotencyKey
from ..api_errors import raise_command_error
from ..api_runtime import service
from ..api_serialization import serialize
from ..commands import (
    CreateFundCommand,
    FundAllocationCommand,
    FundSpendCommand,
    ReconciliationActionCommand,
    RecordInvestmentEventCommand,
)
from .common import COMMAND_ERRORS, command_payload, protected, read_upload_with_limit


router = APIRouter()


@router.post("/investments/events", dependencies=protected)
def record_investment_event(payload: RecordInvestmentEventCommand, token: AuthToken, key: IdempotencyKey):
    try:
        event, replay = service.record_investment_event(token, command_payload(payload), idempotency_key=key)
        return {"event": serialize(event), "idempotent_replay": replay}
    except COMMAND_ERRORS as exc:
        raise_command_error(exc, "investment.event.record")


@router.get("/investments/accounts/{account_id}/performance", dependencies=protected)
def investment_performance(account_id: str, token: AuthToken, as_of: str | None = None):
    try:
        return service.investment_performance(token, account_id, as_of=as_of)
    except COMMAND_ERRORS as exc:
        raise_command_error(exc, "investment.performance")


@router.post("/funds", dependencies=protected)
def create_fund(payload: CreateFundCommand, token: AuthToken, key: IdempotencyKey):
    try:
        fund, replay = service.create_fund(token, command_payload(payload), idempotency_key=key)
        return {"fund": serialize(fund), "idempotent_replay": replay}
    except COMMAND_ERRORS as exc:
        raise_command_error(exc, "fund.create")


@router.post("/funds/allocate", dependencies=protected)
def allocate_fund(payload: FundAllocationCommand, token: AuthToken, key: IdempotencyKey):
    try:
        result, replay = service.allocate_fund(token, command_payload(payload), idempotency_key=key)
        return {"result": serialize(result), "idempotent_replay": replay}
    except COMMAND_ERRORS as exc:
        raise_command_error(exc, "fund.allocate")


@router.post("/funds/spend", dependencies=protected)
def spend_fund(payload: FundSpendCommand, token: AuthToken, key: IdempotencyKey):
    try:
        result, replay = service.spend_fund(token, command_payload(payload), idempotency_key=key)
        return {"result": serialize(result), "idempotent_replay": replay}
    except COMMAND_ERRORS as exc:
        raise_command_error(exc, "fund.spend")


@router.post("/attachments", dependencies=protected)
async def upload_attachment(token: AuthToken, key: IdempotencyKey, file: UploadFile = File(...)):
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
        raise_command_error(exc, "attachment.upload")


@router.post("/reconciliation/actions", dependencies=protected)
def record_reconciliation(payload: ReconciliationActionCommand, token: AuthToken, key: IdempotencyKey):
    try:
        action, replay = service.record_reconciliation_action(token, command_payload(payload), idempotency_key=key)
        return {"action": serialize(action), "idempotent_replay": replay}
    except COMMAND_ERRORS as exc:
        raise_command_error(exc, "reconciliation.record")
