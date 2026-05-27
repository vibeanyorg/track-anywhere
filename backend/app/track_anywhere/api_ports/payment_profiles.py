from __future__ import annotations

from typing import Annotated, Any, Protocol

from .base import AuditRecorder, ServiceDependency


class PaymentProfileRouteService(AuditRecorder, Protocol):
    def create_payment_profile(self, token, payload: dict[str, Any], *, idempotency_key: str): ...
    def list_payment_profiles(self, token, **filters): ...
    def payment_profile_status(self, token, profile_ref: str): ...
    def record_payment_profile_expense(self, token, payload: dict[str, Any], *, idempotency_key: str): ...


PaymentProfileService = Annotated[PaymentProfileRouteService, ServiceDependency]

