from __future__ import annotations

from datetime import timedelta
from typing import Any

from .auth_identities import OAuthIdentity
from .books import DEFAULT_OWNER_ID, BookMember
from .errors import ValidationError
from .service_auth import scopes_for_role


class IdentityUseCases:
    def login_oauth_identity(
        self,
        identity: OAuthIdentity,
        *,
        role: str = "viewer",
        ttl_minutes: int = 8 * 60,
    ) -> dict[str, Any]:
        try:
            scopes = scopes_for_role(role)
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
        linked_identity = self.auth_identities.get_by_provider_subject(identity.provider, identity.subject)
        if linked_identity is None:
            user = self._create_user_for_identity(identity)
            linked_identity = self.auth_identities.link(
                provider=identity.provider,
                subject=identity.subject,
                user_id=user.user_id,
                email=identity.email,
                email_verified=identity.email_verified,
                display_name=identity.name,
                picture_url=identity.picture,
            )
        else:
            user = self.users.get(linked_identity.user_id)
            self.auth_identities.refresh(
                linked_identity,
                email=identity.email,
                email_verified=identity.email_verified,
                display_name=identity.name,
                picture_url=identity.picture,
            )

        book = self.books.ensure_default()
        member = self.books.members.get((book.book_id, user.user_id))
        if member is None or member.status != "active":
            member = BookMember(book_id=book.book_id, user_id=user.user_id, role=role, scopes=sorted(scopes))
            self.books.members[(book.book_id, user.user_id)] = member
        else:
            member.role = role
            member.scopes = sorted(scopes)
            member.version += 1

        credential_token = self.credentials.issue(
            actor_id=user.user_id,
            actor_type="human",
            scopes=scopes,
            ttl=timedelta(minutes=ttl_minutes),
        )
        credential = self.credentials.get_by_token(credential_token)
        if credential is None:
            raise RuntimeError("issued credential was not stored")
        audit_event = self.audit.record(
            operation="auth.login",
            actor=self.actor_from_token(credential_token),
            entity_ref=linked_identity.identity_id,
            details={
                "provider": linked_identity.provider,
                "subject": linked_identity.subject,
                "email": linked_identity.email,
                "role": member.role,
            },
        )
        owner_member = self.books.members.get((book.book_id, DEFAULT_OWNER_ID))
        members = [member] if owner_member is None else [owner_member, member]
        self.storage.save_auth_login_state(
            book=book,
            members=members,
            user=user,
            identity=linked_identity,
            credential=credential,
            audit_event=audit_event,
        )
        return {
            "credential_token": credential_token,
            "user": {
                "user_id": user.user_id,
                "username": user.username,
                "display_name": user.display_name,
            },
            "identity": {
                "identity_id": linked_identity.identity_id,
                "provider": linked_identity.provider,
                "subject": linked_identity.subject,
                "user_id": linked_identity.user_id,
                "email": linked_identity.email,
                "email_verified": linked_identity.email_verified,
                "display_name": linked_identity.display_name,
                "picture_url": linked_identity.picture_url,
                "status": linked_identity.status,
            },
            "membership": {
                "book_id": member.book_id,
                "user_id": member.user_id,
                "role": member.role,
                "scopes": sorted(member.scopes),
            },
        }

    def _create_user_for_identity(self, identity: OAuthIdentity):
        username = _username_from_identity(identity)
        display_name = identity.name or username
        try:
            return self.users.create(username=username, display_name=display_name)
        except ValidationError:
            return self.users.create(
                username=f"{identity.provider}_{identity.subject}",
                display_name=display_name,
            )


def _username_from_identity(identity: OAuthIdentity) -> str:
    if identity.email:
        return identity.email.split("@", 1)[0].lower()
    if identity.name:
        return "_".join(identity.name.lower().split())
    return f"{identity.provider}_{identity.subject}"
