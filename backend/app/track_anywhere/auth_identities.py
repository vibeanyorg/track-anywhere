from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from .errors import ValidationError


@dataclass(frozen=True)
class OAuthIdentity:
    provider: str
    subject: str
    email: str | None
    email_verified: bool
    name: str | None
    picture: str | None

    def public_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "subject": self.subject,
            "email": self.email,
            "email_verified": self.email_verified,
            "name": self.name,
            "picture": self.picture,
        }


@dataclass
class LinkedAuthIdentity:
    identity_id: str
    provider: str
    subject: str
    user_id: str
    email: str | None
    email_verified: bool
    display_name: str | None
    picture_url: str | None
    status: str = "active"
    version: int = 1


class AuthIdentityDirectory:
    def __init__(self) -> None:
        self.identities: dict[str, LinkedAuthIdentity] = {}

    def get_by_provider_subject(self, provider: str, subject: str) -> LinkedAuthIdentity | None:
        return next(
            (
                identity
                for identity in self.identities.values()
                if identity.provider == provider and identity.subject == subject
            ),
            None,
        )

    def link(
        self,
        *,
        provider: str,
        subject: str,
        user_id: str,
        email: str | None,
        email_verified: bool,
        display_name: str | None,
        picture_url: str | None,
    ) -> LinkedAuthIdentity:
        if self.get_by_provider_subject(provider, subject) is not None:
            raise ValidationError("auth identity is already linked")
        identity = LinkedAuthIdentity(
            identity_id=f"identity_{uuid4().hex}",
            provider=provider,
            subject=subject,
            user_id=user_id,
            email=email,
            email_verified=email_verified,
            display_name=display_name,
            picture_url=picture_url,
        )
        self.identities[identity.identity_id] = identity
        return identity

    def refresh(
        self,
        identity: LinkedAuthIdentity,
        *,
        email: str | None,
        email_verified: bool,
        display_name: str | None,
        picture_url: str | None,
    ) -> LinkedAuthIdentity:
        identity.email = email
        identity.email_verified = email_verified
        identity.display_name = display_name
        identity.picture_url = picture_url
        identity.version += 1
        return identity
