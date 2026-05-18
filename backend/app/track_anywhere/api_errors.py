from __future__ import annotations

from fastapi import HTTPException
from pydantic import ValidationError as PydanticValidationError

from .api_runtime import service
from .errors import IdempotencyConflict, NotFound, PolicyDenied, SecurityPreconditionFailed, StaleVersion


def error_to_status(error: Exception) -> int:
    if isinstance(error, PolicyDenied):
        return 403
    if isinstance(error, SecurityPreconditionFailed):
        return 400
    if isinstance(error, IdempotencyConflict | StaleVersion):
        return 409
    if isinstance(error, NotFound):
        return 404
    return 422


def raise_command_error(error: Exception, operation: str) -> None:
    if isinstance(error, PolicyDenied):
        service.record_security_failure("security.policy_denied", {"operation": operation})
    elif isinstance(error, IdempotencyConflict):
        service.record_security_failure("command.idempotency_conflict", {"operation": operation})
    elif isinstance(error, StaleVersion):
        service.record_security_failure("command.stale_version", {"operation": operation})
    elif isinstance(error, PydanticValidationError):
        service.record_security_failure("command.validation_failed", {"operation": operation, "error_count": error.error_count()})
    raise HTTPException(status_code=error_to_status(error), detail=str(error))
