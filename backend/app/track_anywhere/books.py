from __future__ import annotations

from dataclasses import dataclass, field
from uuid import uuid4

from .errors import NotFound, PolicyDenied, ValidationError
from .security import Actor


DEFAULT_BOOK_ID = "book_default"
DEFAULT_OWNER_ID = "owner"


@dataclass
class LedgerBook:
    book_id: str
    name: str
    kind: str = "personal"
    base_currency: str = "CNY"
    timezone: str = "Asia/Shanghai"
    status: str = "active"
    template_key: str | None = None
    settings: dict[str, object] = field(default_factory=dict)
    created_by: str = DEFAULT_OWNER_ID
    version: int = 1


@dataclass
class BookMember:
    book_id: str
    user_id: str
    role: str
    status: str = "active"
    scopes: list[str] = field(default_factory=list)
    version: int = 1


class BookDirectory:
    def __init__(self) -> None:
        self.books: dict[str, LedgerBook] = {}
        self.members: dict[tuple[str, str], BookMember] = {}
        self._dirty_book_ids: set[str] = set()
        self._dirty_member_keys: set[tuple[str, str]] = set()

    def ensure_default(self) -> LedgerBook:
        book = self.books.get(DEFAULT_BOOK_ID)
        if book is None:
            book = LedgerBook(book_id=DEFAULT_BOOK_ID, name="Personal")
            self.books[book.book_id] = book
            self._dirty_book_ids.add(book.book_id)
        member_key = (book.book_id, DEFAULT_OWNER_ID)
        if member_key not in self.members:
            self.members[member_key] = BookMember(book_id=book.book_id, user_id=DEFAULT_OWNER_ID, role="owner")
            self._dirty_member_keys.add(member_key)
        return book

    def create(
        self,
        *,
        name: str,
        kind: str = "personal",
        base_currency: str = "CNY",
        timezone: str = "Asia/Shanghai",
        template_key: str | None = None,
        created_by: str = DEFAULT_OWNER_ID,
    ) -> LedgerBook:
        name = _normalize_text(name, "book name")
        if kind not in {"personal", "family", "travel", "business", "reimbursement", "custom"}:
            raise ValidationError("book kind is invalid")
        book = LedgerBook(
            book_id=f"book_{uuid4().hex}",
            name=name,
            kind=kind,
            base_currency=base_currency,
            timezone=timezone,
            template_key=template_key,
            created_by=created_by,
        )
        self.books[book.book_id] = book
        member_key = (book.book_id, created_by)
        self.members[member_key] = BookMember(
            book_id=book.book_id,
            user_id=created_by,
            role="owner",
        )
        self._dirty_book_ids.add(book.book_id)
        self._dirty_member_keys.add(member_key)
        return book

    def get(self, book_id: str | None = None) -> LedgerBook:
        resolved = book_id or DEFAULT_BOOK_ID
        try:
            return self.books[resolved]
        except KeyError as exc:
            raise NotFound(f"book not found: {resolved}") from exc

    def require_access(self, book_id: str | None, actor: Actor, required_scope: str | None = None) -> LedgerBook:
        book = self.get(book_id)
        member = self.members.get((book.book_id, actor.actor_id))
        if member is None or member.status != "active":
            raise PolicyDenied("actor is not a member of the book")
        if member.role in {"owner", "admin"}:
            return book
        if member.scopes and required_scope is not None and required_scope not in member.scopes:
            raise PolicyDenied(f"book membership lacks required scope: {required_scope}")
        if required_scope is not None and not required_scope.endswith(":read") and member.role in {"viewer", "auditor"}:
            raise PolicyDenied("book membership is read-only")
        return book

    def has_access(self, book_id: str | None, actor: Actor, required_scope: str | None = None) -> bool:
        try:
            self.require_access(book_id, actor, required_scope)
            return True
        except (NotFound, PolicyDenied):
            return False

    def list(self, *, status: str | None = "active") -> list[LedgerBook]:
        books = list(self.books.values())
        if status is not None:
            books = [book for book in books if book.status == status]
        return sorted(books, key=lambda book: (book.name, book.book_id))

    def dirty_books(self) -> list[LedgerBook]:
        return [self.books[book_id] for book_id in self._dirty_book_ids if book_id in self.books]

    def dirty_members(self) -> list[BookMember]:
        return [self.members[key] for key in self._dirty_member_keys if key in self.members]

    def mark_clean(self) -> None:
        self._dirty_book_ids.clear()
        self._dirty_member_keys.clear()


def _normalize_text(value: str | None, field_name: str) -> str:
    if value is None:
        raise ValidationError(f"{field_name} is required")
    normalized = " ".join(value.strip().split())
    if not normalized:
        raise ValidationError(f"{field_name} must not be blank")
    return normalized
