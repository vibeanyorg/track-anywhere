from __future__ import annotations

from fastapi import APIRouter

from ..api_dependencies import AuthToken, IdempotencyKey
from ..api_errors import raise_command_error
from ..api_runtime import service
from ..api_serialization import serialize
from ..domain_commands import CreateCounterpartyCommand
from .common import COMMAND_ERRORS, command_payload, protected


router = APIRouter()


@router.post("/counterparties/ensure", dependencies=protected)
def ensure_counterparty(payload: CreateCounterpartyCommand, token: AuthToken, key: IdempotencyKey):
    try:
        counterparty, replay = service.ensure_counterparty(token, command_payload(payload), idempotency_key=key)
        return {"counterparty": serialize(counterparty), "idempotent_replay": replay}
    except COMMAND_ERRORS as exc:
        raise_command_error(exc, "counterparty.ensure")


@router.post("/counterparties", dependencies=protected)
def create_counterparty(payload: CreateCounterpartyCommand, token: AuthToken, key: IdempotencyKey):
    try:
        counterparty, replay = service.ensure_counterparty(token, command_payload(payload), idempotency_key=key)
        return {"counterparty": serialize(counterparty), "idempotent_replay": replay}
    except COMMAND_ERRORS as exc:
        raise_command_error(exc, "counterparty.create")


@router.get("/counterparties", dependencies=protected)
def list_counterparties(
    token: AuthToken,
    kind: str | None = None,
    status: str | None = "active",
    name: str | None = None,
):
    try:
        counterparties = service.list_counterparties(token, kind=kind, status=status, name=name)
        return {"counterparties": serialize(counterparties)}
    except COMMAND_ERRORS as exc:
        raise_command_error(exc, "counterparty.list")


@router.get("/counterparties/{counterparty_ref}", dependencies=protected)
def get_counterparty(counterparty_ref: str, token: AuthToken):
    try:
        return {"counterparty": serialize(service.get_counterparty(token, counterparty_ref))}
    except COMMAND_ERRORS as exc:
        raise_command_error(exc, "counterparty.get")
