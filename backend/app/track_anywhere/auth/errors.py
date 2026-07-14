from __future__ import annotations


class AuthPolicyDenied(PermissionError):
    pass


class AuthSecurityError(ValueError):
    pass


class OAuthFlowError(Exception):
    def __init__(
        self,
        error: str,
        description: str,
        extra: dict[str, object] | None = None,
    ) -> None:
        super().__init__(description)
        self.error = error
        self.description = description
        self.extra = extra or {}


__all__ = ["AuthPolicyDenied", "AuthSecurityError", "OAuthFlowError"]
