from __future__ import annotations

from typing import Annotated, Any, Protocol

from .base import AuditRecorder, ServiceDependency


class CounterpartyRouteService(AuditRecorder, Protocol):
    def ensure_counterparty(self, token, payload: dict[str, Any], *, idempotency_key: str): ...
    def list_counterparties(self, token, **filters): ...
    def get_counterparty(self, token, counterparty_ref: str): ...


CounterpartyService = Annotated[CounterpartyRouteService, ServiceDependency]

