from __future__ import annotations

import re
from dataclasses import dataclass
from uuid import uuid4

from .books import DEFAULT_BOOK_ID
from .errors import NotFound, ValidationError


COUNTERPARTY_KINDS = {"merchant", "person", "institution", "employer", "platform", "other"}
COUNTERPARTY_STATUSES = {"active", "hidden", "archived"}


@dataclass
class Counterparty:
    counterparty_id: str
    book_id: str
    slug: str
    name: str
    kind: str = "merchant"
    status: str = "active"
    version: int = 1


class CounterpartyDirectory:
    def __init__(self) -> None:
        self.counterparties: dict[str, Counterparty] = {}
        self._dirty_counterparty_ids: set[str] = set()

    def create(
        self,
        *,
        name: str,
        book_id: str = DEFAULT_BOOK_ID,
        kind: str = "merchant",
        slug: str | None = None,
    ) -> Counterparty:
        name = normalize_counterparty_name(name)
        slug = normalize_counterparty_slug(slug or name)
        self._validate_kind(kind)
        if self.get_optional_by_slug(book_id=book_id, slug=slug, status=None) is not None:
            raise ValidationError("counterparty slug already exists")
        counterparty = Counterparty(
            counterparty_id=f"cp_{uuid4().hex}",
            book_id=book_id,
            slug=slug,
            name=name,
            kind=kind,
        )
        self.counterparties[counterparty.counterparty_id] = counterparty
        self._dirty_counterparty_ids.add(counterparty.counterparty_id)
        return counterparty

    def ensure(
        self,
        *,
        name: str,
        book_id: str = DEFAULT_BOOK_ID,
        kind: str = "merchant",
        slug: str | None = None,
    ) -> Counterparty:
        name = normalize_counterparty_name(name)
        slug = normalize_counterparty_slug(slug or name)
        self._validate_kind(kind)
        existing = self.get_optional_by_slug(book_id=book_id, slug=slug, status=None)
        if existing is not None:
            return existing
        existing = self.get_optional_by_name(book_id=book_id, name=name, kind=kind, status=None)
        if existing is not None:
            return existing
        return self.create(name=name, book_id=book_id, kind=kind, slug=slug)

    def get(self, counterparty_id: str, *, status: str | None = "active") -> Counterparty:
        try:
            counterparty = self.counterparties[counterparty_id]
        except KeyError as exc:
            raise NotFound(f"counterparty not found: {counterparty_id}") from exc
        if status is not None and counterparty.status != status:
            raise NotFound(f"counterparty not found: {counterparty_id}")
        return counterparty

    def get_by_slug(self, *, book_id: str, slug: str, status: str | None = "active") -> Counterparty:
        slug = normalize_counterparty_slug(slug)
        for counterparty in self.counterparties.values():
            if counterparty.book_id != book_id or counterparty.slug != slug:
                continue
            if status is not None and counterparty.status != status:
                continue
            return counterparty
        raise NotFound(f"counterparty slug not found in book: {book_id}/{slug}")

    def get_optional_by_slug(
        self,
        *,
        book_id: str,
        slug: str,
        status: str | None = "active",
    ) -> Counterparty | None:
        try:
            return self.get_by_slug(book_id=book_id, slug=slug, status=status)
        except NotFound:
            return None

    def get_by_name(
        self,
        *,
        book_id: str,
        name: str,
        kind: str | None = None,
        status: str | None = "active",
    ) -> Counterparty:
        normalized = normalize_counterparty_name(name).casefold()
        for counterparty in self.counterparties.values():
            if counterparty.book_id != book_id or counterparty.name.casefold() != normalized:
                continue
            if kind is not None and counterparty.kind != kind:
                continue
            if status is not None and counterparty.status != status:
                continue
            return counterparty
        raise NotFound(f"counterparty name not found in book: {book_id}/{name}")

    def get_optional_by_name(
        self,
        *,
        book_id: str,
        name: str,
        kind: str | None = None,
        status: str | None = "active",
    ) -> Counterparty | None:
        try:
            return self.get_by_name(book_id=book_id, name=name, kind=kind, status=status)
        except NotFound:
            return None

    def resolve(self, *, book_id: str, ref: str, status: str | None = "active") -> Counterparty:
        try:
            counterparty = self.get(ref, status=status)
        except NotFound:
            pass
        else:
            if counterparty.book_id != book_id:
                raise NotFound(f"counterparty not found in book: {book_id}/{ref}")
            return counterparty
        try:
            return self.get_by_slug(book_id=book_id, slug=ref, status=status)
        except NotFound:
            return self.get_by_name(book_id=book_id, name=ref, status=status)

    def list(
        self,
        *,
        book_id: str = DEFAULT_BOOK_ID,
        kind: str | None = None,
        status: str | None = "active",
        name: str | None = None,
    ) -> list[Counterparty]:
        if kind is not None:
            self._validate_kind(kind)
        if status is not None and status not in COUNTERPARTY_STATUSES:
            raise ValidationError("status must be active, hidden, or archived")
        items = [item for item in self.counterparties.values() if item.book_id == book_id]
        if kind is not None:
            items = [item for item in items if item.kind == kind]
        if status is not None:
            items = [item for item in items if item.status == status]
        if name is not None:
            needle = normalize_counterparty_name(name).casefold()
            items = [item for item in items if needle in item.name.casefold()]
        return sorted(items, key=lambda item: (item.name.casefold(), item.counterparty_id))

    def mark_clean(self) -> None:
        self._dirty_counterparty_ids.clear()

    def dirty_counterparties(self) -> list[Counterparty]:
        return [
            self.counterparties[counterparty_id]
            for counterparty_id in self._dirty_counterparty_ids
            if counterparty_id in self.counterparties
        ]

    @staticmethod
    def _validate_kind(kind: str) -> None:
        if kind not in COUNTERPARTY_KINDS:
            raise ValidationError("counterparty kind is invalid")


def normalize_counterparty_name(value: str) -> str:
    normalized = " ".join(value.strip().split())
    if not normalized:
        raise ValidationError("counterparty name must not be blank")
    if len(normalized) > 120:
        raise ValidationError("counterparty name must be at most 120 characters")
    return normalized


def normalize_counterparty_slug(value: str) -> str:
    normalized = normalize_counterparty_name(value).casefold()
    normalized = re.sub(r"\s+", "-", normalized)
    normalized = re.sub(r"-+", "-", normalized).strip("-")
    if not normalized:
        raise ValidationError("counterparty slug must not be blank")
    if len(normalized) > 120:
        raise ValidationError("counterparty slug must be at most 120 characters")
    return normalized
