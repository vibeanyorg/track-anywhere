from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from fastapi import APIRouter
from pydantic import Field

from ..api_dependencies import AuthToken, IdempotencyKey
from ..api_errors import raise_command_error
from ..api_service_ports import PaymentProfileService
from ..api_serialization import serialize
from ..commands import ASSET_CODE_PATTERN, StrictCommand
from ..domain_commands import CreatePaymentProfileCommand
from .common import COMMAND_ERRORS, command_payload, protected


class RecordPaymentProfileExpenseBody(StrictCommand):
    amount: Decimal = Field(gt=0)
    currency: str = Field(pattern=ASSET_CODE_PATTERN)
    category_id: str
    purpose: str = Field(min_length=1, max_length=256)
    memo: str = Field(default="", max_length=256)
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    counterparty: str | None = Field(default=None, min_length=1, max_length=120)


router = APIRouter()


@router.post("/payment-profiles", dependencies=protected)
def create_payment_profile(
    payload: CreatePaymentProfileCommand,
    token: AuthToken,
    key: IdempotencyKey,
    service: PaymentProfileService,
):
    try:
        profile, replay = service.create_payment_profile(token, command_payload(payload), idempotency_key=key)
        return {"payment_profile": serialize(profile), "idempotent_replay": replay}
    except COMMAND_ERRORS as exc:
        raise_command_error(exc, "payment_profile.create", recorder=service)


@router.get("/payment-profiles", dependencies=protected)
def list_payment_profiles(token: AuthToken, service: PaymentProfileService, status: str | None = "active"):
    try:
        profiles = service.list_payment_profiles(token, status=status)
        return {"payment_profiles": serialize(profiles)}
    except COMMAND_ERRORS as exc:
        raise_command_error(exc, "payment_profile.list", recorder=service)


@router.get("/payment-profiles/{payment}/status", dependencies=protected)
def get_payment_profile_status(payment: str, token: AuthToken, service: PaymentProfileService):
    try:
        return serialize(service.payment_profile_status(token, payment))
    except COMMAND_ERRORS as exc:
        raise_command_error(exc, "payment_profile.status", recorder=service)


@router.post("/payment-profiles/{payment}/expenses", dependencies=protected)
def record_payment_profile_expense(
    payment: str,
    payload: RecordPaymentProfileExpenseBody,
    token: AuthToken,
    key: IdempotencyKey,
    service: PaymentProfileService,
):
    try:
        transaction, replay = service.record_payment_profile_expense(
            token,
            {**command_payload(payload), "payment": payment},
            idempotency_key=key,
        )
        return {"transaction": serialize(transaction), "idempotent_replay": replay}
    except COMMAND_ERRORS as exc:
        raise_command_error(exc, "payment_profile.expense.record", recorder=service)
