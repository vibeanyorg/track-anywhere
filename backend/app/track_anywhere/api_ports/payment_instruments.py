from __future__ import annotations

from typing import Annotated, Any, Protocol

from .base import AuditRecorder, ServiceDependency


class PaymentInstrumentRouteService(AuditRecorder, Protocol):
    def create_payment_instrument(self, token, payload: dict[str, Any], *, idempotency_key: str): ...
    def list_payment_instruments(self, token, **filters): ...
    def resolve_payment_instrument(self, token, instrument_ref: str): ...


PaymentInstrumentService = Annotated[PaymentInstrumentRouteService, ServiceDependency]

