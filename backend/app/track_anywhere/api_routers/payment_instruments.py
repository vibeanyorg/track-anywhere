from __future__ import annotations

from fastapi import APIRouter

from ..api_dependencies import AuthToken, IdempotencyKey
from ..api_errors import raise_command_error
from ..api_ports.payment_instruments import PaymentInstrumentService
from ..api_serialization import serialize
from ..domain_commands import CreatePaymentInstrumentCommand
from .common import COMMAND_ERRORS, command_payload, protected


router = APIRouter()


@router.post("/payment-instruments", dependencies=protected)
def create_payment_instrument(
    payload: CreatePaymentInstrumentCommand,
    token: AuthToken,
    key: IdempotencyKey,
    service: PaymentInstrumentService,
):
    try:
        instrument, replay = service.create_payment_instrument(token, command_payload(payload), idempotency_key=key)
        return {"payment_instrument": serialize(instrument), "idempotent_replay": replay}
    except COMMAND_ERRORS as exc:
        raise_command_error(exc, "payment_instrument.create", recorder=service)


@router.get("/payment-instruments", dependencies=protected)
def list_payment_instruments(
    token: AuthToken,
    service: PaymentInstrumentService,
    account_id: str | None = None,
    status: str | None = "active",
):
    try:
        instruments = service.list_payment_instruments(token, account_id=account_id, status=status)
        return {"payment_instruments": serialize(instruments)}
    except COMMAND_ERRORS as exc:
        raise_command_error(exc, "payment_instrument.list", recorder=service)


@router.get("/payment-instruments/{instrument}", dependencies=protected)
def get_payment_instrument(instrument: str, token: AuthToken, service: PaymentInstrumentService):
    try:
        return {"payment_instrument": serialize(service.resolve_payment_instrument(token, instrument))}
    except COMMAND_ERRORS as exc:
        raise_command_error(exc, "payment_instrument.get", recorder=service)
