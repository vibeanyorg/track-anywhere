from __future__ import annotations

from typing import Any

from .service_auth import SYSTEM_ACTOR


class CredentialAuditUseCases:
    def record_security_failure(self, operation: str, details: dict[str, Any] | None = None) -> None:
        event = self.audit.record(operation=operation, actor=SYSTEM_ACTOR, entity_ref=None, details=details or {})
        self._commit_audit_event(event)
