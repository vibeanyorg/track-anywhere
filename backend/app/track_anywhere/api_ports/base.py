from __future__ import annotations

from typing import Any, Protocol

from fastapi import Depends

from ..api_dependencies import get_service


class AuditRecorder(Protocol):
    def record_security_failure(self, operation: str, details: dict[str, Any]) -> None: ...


ServiceDependency = Depends(get_service)

