from __future__ import annotations

from typing import AbstractSet, Any

from .auth_identities import OAuthIdentity
from .errors import PolicyDenied
from .password_auth import PasswordAccount, PasswordAccountStore, normalize_email


class PasswordAuthUseCases:
    def authenticate_password_account(
        self,
        *,
        email: str,
        password: str,
        source: str = "unknown",
    ) -> PasswordAccount:
        attempt_key = f"{source}:{normalize_email(email)}"
        self.password_login_limiter.check(attempt_key)
        try:
            with self.storage.unit_of_work() as uow:
                account = PasswordAccountStore(uow.password_accounts).authenticate(email=email, password=password)
        except PolicyDenied:
            self.password_login_limiter.record_failure(attempt_key)
            self.record_security_failure("auth.password_denied", {"reason": "bad_credentials"})
            raise
        self.password_login_limiter.record_success(attempt_key)
        return account

    def create_password_account(
        self,
        *,
        email: str,
        password: str,
        display_name: str | None,
        signup_allowed_emails: AbstractSet[str],
    ) -> PasswordAccount:
        normalized_email = normalize_email(email)
        if self.config.mode != "local" and normalized_email not in signup_allowed_emails:
            self.record_security_failure("auth.password_signup_denied", {"reason": "email_not_allowlisted"})
            raise PolicyDenied("password signup is not allowlisted")
        with self.storage.unit_of_work() as uow:
            return PasswordAccountStore(uow.password_accounts).create(
                email=normalized_email,
                password=password,
                display_name=display_name,
            )

    def login_password_account(self, account: PasswordAccount) -> dict[str, Any]:
        return self.login_oauth_identity(
            OAuthIdentity(
                provider="password",
                subject=account.email,
                email=account.email,
                email_verified=True,
                name=account.display_name,
                picture=None,
            ),
            role=account.role,
        )
