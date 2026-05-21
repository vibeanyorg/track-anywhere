from __future__ import annotations

from .audit import AuditEvent
from .auth_identities import LinkedAuthIdentity
from .domain_storage_models import BookMemberRecord, LedgerBookRecord
from .storage_json import to_jsonable
from .storage_models import AuthIdentityRecord, UserRecord
from .users import AppUser


class AuthStorageWriters:
    def save_auth_login_state(
        self,
        *,
        book,
        members,
        user: AppUser,
        identity: LinkedAuthIdentity,
        credential,
        audit_event: AuditEvent,
    ) -> None:
        with self.session_factory.begin() as session:
            session.merge(
                LedgerBookRecord(
                    book_id=book.book_id,
                    name=book.name,
                    kind=book.kind,
                    base_currency=book.base_currency,
                    timezone=book.timezone,
                    status=book.status,
                    template_key=book.template_key,
                    settings=to_jsonable(book.settings),
                    created_by=book.created_by,
                    version=book.version,
                )
            )
            seen_members = set()
            for member in members:
                member_key = (member.book_id, member.user_id)
                if member_key in seen_members:
                    continue
                seen_members.add(member_key)
                session.merge(
                    BookMemberRecord(
                        book_id=member.book_id,
                        user_id=member.user_id,
                        role=member.role,
                        status=member.status,
                        scopes=list(member.scopes),
                        version=member.version,
                    )
                )
            session.merge(
                UserRecord(
                    user_id=user.user_id,
                    username=user.username,
                    display_name=user.display_name,
                    version=user.version,
                )
            )
            session.merge(
                AuthIdentityRecord(
                    identity_id=identity.identity_id,
                    provider=identity.provider,
                    subject=identity.subject,
                    user_id=identity.user_id,
                    email=identity.email,
                    email_verified=identity.email_verified,
                    display_name=identity.display_name,
                    picture_url=identity.picture_url,
                    status=identity.status,
                    version=identity.version,
                )
            )
            self._save_credentials(session, [credential])
            self._save_audit_events(session, [audit_event])
