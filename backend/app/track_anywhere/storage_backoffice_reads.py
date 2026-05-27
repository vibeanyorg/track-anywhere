from __future__ import annotations

from .audit import AuditEvent
from .auth_identities import LinkedAuthIdentity
from .books import BookMember, LedgerBook
from .domain_storage_models import BookMemberRecord, LedgerBookRecord
from .storage_models import AuditEventRecord, AuthIdentityRecord, UserRecord
from .users import AppUser


class BackofficeReadStorage:
    def list_books(self) -> list[LedgerBook]:
        with self.session_factory() as session:
            rows = session.query(LedgerBookRecord).all()
        return sorted(
            (_book_from_row(row) for row in rows),
            key=lambda book: (book.name, book.book_id),
        )

    def list_book_members(self) -> list[BookMember]:
        with self.session_factory() as session:
            rows = session.query(BookMemberRecord).all()
        return sorted(
            (_book_member_from_row(row) for row in rows),
            key=lambda member: (member.book_id, member.user_id, member.role),
        )

    def list_users(self) -> list[AppUser]:
        with self.session_factory() as session:
            rows = session.query(UserRecord).all()
        return sorted(
            (_user_from_row(row) for row in rows),
            key=lambda user: (user.username, user.user_id),
        )

    def list_auth_identities(self) -> list[LinkedAuthIdentity]:
        with self.session_factory() as session:
            rows = session.query(AuthIdentityRecord).all()
        return sorted(
            (_auth_identity_from_row(row) for row in rows),
            key=lambda identity: (identity.provider, identity.subject, identity.identity_id),
        )

    def list_audit_events(self) -> list[AuditEvent]:
        with self.session_factory() as session:
            rows = session.query(AuditEventRecord).all()
        return sorted(
            (_audit_event_from_row(row) for row in rows),
            key=lambda event: (event.created_at, event.event_id),
        )


def _book_from_row(row: LedgerBookRecord) -> LedgerBook:
    return LedgerBook(
        book_id=row.book_id,
        name=row.name,
        kind=row.kind,
        base_currency=row.base_currency,
        timezone=row.timezone,
        status=row.status,
        template_key=row.template_key,
        settings=dict(row.settings or {}),
        created_by=row.created_by,
        version=row.version,
    )


def _book_member_from_row(row: BookMemberRecord) -> BookMember:
    return BookMember(
        book_id=row.book_id,
        user_id=row.user_id,
        role=row.role,
        status=row.status,
        scopes=list(row.scopes or []),
        version=row.version,
    )


def _user_from_row(row: UserRecord) -> AppUser:
    return AppUser(
        user_id=row.user_id,
        username=row.username,
        display_name=row.display_name,
        version=row.version,
    )


def _auth_identity_from_row(row: AuthIdentityRecord) -> LinkedAuthIdentity:
    return LinkedAuthIdentity(
        identity_id=row.identity_id,
        provider=row.provider,
        subject=row.subject,
        user_id=row.user_id,
        email=row.email,
        email_verified=row.email_verified,
        display_name=row.display_name,
        picture_url=row.picture_url,
        status=row.status,
        version=row.version,
    )


def _audit_event_from_row(row: AuditEventRecord) -> AuditEvent:
    return AuditEvent(
        event_id=row.event_id,
        operation=row.operation,
        actor_id=row.actor_id,
        actor_type=row.actor_type,
        entity_ref=row.entity_ref,
        details=dict(row.details or {}),
        created_at=row.created_at,
    )
