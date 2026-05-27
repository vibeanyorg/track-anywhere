from __future__ import annotations

from typing import Annotated, Protocol

from .base import ServiceDependency


class SystemRouteService(Protocol):
    def system_readiness(self) -> dict[str, object]: ...
    def system_status(self, token, *, include_counts: bool = False) -> dict[str, object]: ...
    def local_dev_session(self) -> dict[str, object]: ...
    def local_dev_token(self) -> dict[str, object]: ...


SystemService = Annotated[SystemRouteService, ServiceDependency]
