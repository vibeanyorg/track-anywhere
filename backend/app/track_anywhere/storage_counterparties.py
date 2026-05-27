from __future__ import annotations

from sqlalchemy.orm import Session

from .books import DEFAULT_BOOK_ID
from .counterparties import Counterparty, normalize_counterparty_name, normalize_counterparty_slug
from .counterparty_storage_models import CounterpartyRecord
from .errors import NotFound
from .storage_repositories.catalog import counterparty_from_record


class CounterpartyStorageMixin:
    def _load_counterparties(self, session: Session) -> dict[str, Counterparty]:
        return {
            row.counterparty_id: counterparty_from_record(row)
            for row in session.query(CounterpartyRecord).all()
        }

    def list_counterparties(
        self,
        *,
        book_id: str = DEFAULT_BOOK_ID,
        kind: str | None = None,
        status: str | None = "active",
        name: str | None = None,
    ) -> list[Counterparty]:
        counterparties = self._cached_values("counterparties")
        if counterparties is None:
            with self.session_factory() as session:
                rows = session.query(CounterpartyRecord).all()
            counterparties = [counterparty_from_record(row) for row in rows]
        counterparties = [item for item in counterparties if item.book_id == book_id]
        if kind is not None:
            counterparties = [item for item in counterparties if item.kind == kind]
        if status is not None:
            counterparties = [item for item in counterparties if item.status == status]
        if name is not None:
            needle = normalize_counterparty_name(name).casefold()
            counterparties = [item for item in counterparties if needle in item.name.casefold()]
        return sorted(counterparties, key=lambda item: (item.name.casefold(), item.counterparty_id))

    def get_counterparty(self, counterparty_id: str, *, status: str | None = "active") -> Counterparty:
        cached = self._cached_get("counterparties", counterparty_id)
        if cached is not None:
            if status is not None and cached.status != status:
                raise NotFound(f"counterparty not found: {counterparty_id}")
            return cached
        with self.session_factory() as session:
            row = session.get(CounterpartyRecord, counterparty_id)
        if row is None or (status is not None and row.status != status):
            raise NotFound(f"counterparty not found: {counterparty_id}")
        return counterparty_from_record(row)

    def get_counterparty_by_slug(
        self,
        *,
        book_id: str,
        slug: str,
        status: str | None = "active",
    ) -> Counterparty:
        slug = normalize_counterparty_slug(slug)
        for counterparty in self.list_counterparties(book_id=book_id, status=status):
            if counterparty.slug == slug:
                return counterparty
        raise NotFound(f"counterparty slug not found in book: {book_id}/{slug}")

    def get_counterparty_by_name(
        self,
        *,
        book_id: str,
        name: str,
        status: str | None = "active",
    ) -> Counterparty:
        name = normalize_counterparty_name(name).casefold()
        for counterparty in self.list_counterparties(book_id=book_id, status=status):
            if counterparty.name.casefold() == name:
                return counterparty
        raise NotFound(f"counterparty name not found in book: {book_id}/{name}")
