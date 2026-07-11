class TrackAnywhereError(Exception):
    """Base domain error."""


class ValidationError(TrackAnywhereError):
    """Input failed domain validation."""


class PolicyDenied(TrackAnywhereError):
    """Actor is not allowed to run the requested command."""


class RateLimitExceeded(TrackAnywhereError):
    """A security-sensitive operation exceeded its retry budget."""

    def __init__(self, message: str, *, retry_after: int) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class SecurityPreconditionFailed(TrackAnywhereError):
    """HTTP or deployment security precondition failed."""


class IdempotencyConflict(TrackAnywhereError):
    """Idempotency key was reused with a different command payload."""


class StaleVersion(TrackAnywhereError):
    """Optimistic concurrency version check failed."""


class NotFound(TrackAnywhereError):
    """Requested entity was not found."""
