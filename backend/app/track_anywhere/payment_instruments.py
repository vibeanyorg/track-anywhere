from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from .books import DEFAULT_BOOK_ID
from .errors import NotFound


@dataclass
class PaymentInstrument:
    instrument_id: str
    book_id: str
    slug: str
    display_name: str
    kind: str
    account_id: str
    last4: str | None = None
    status: str = "active"
    version: int = 1


class PaymentInstrumentDirectory:
    def __init__(self) -> None:
        self.instruments: dict[str, PaymentInstrument] = {}
        self._dirty_instrument_ids: set[str] = set()

    def create(
        self,
        *,
        book_id: str,
        slug: str,
        display_name: str,
        kind: str,
        account_id: str,
        last4: str | None = None,
    ) -> PaymentInstrument:
        instrument = PaymentInstrument(
            instrument_id=f"pi_{uuid4().hex}",
            book_id=book_id,
            slug=slug,
            display_name=display_name,
            kind=kind,
            account_id=account_id,
            last4=last4,
        )
        self.instruments[instrument.instrument_id] = instrument
        self._dirty_instrument_ids.add(instrument.instrument_id)
        return instrument

    def get(self, instrument_id: str, *, status: str | None = "active") -> PaymentInstrument:
        try:
            instrument = self.instruments[instrument_id]
        except KeyError as exc:
            raise NotFound(f"payment instrument not found: {instrument_id}") from exc
        if status is not None and instrument.status != status:
            raise NotFound(f"payment instrument not found: {instrument_id}")
        return instrument

    def get_by_slug(self, *, book_id: str, slug: str, status: str | None = "active") -> PaymentInstrument:
        for instrument in self.instruments.values():
            if instrument.book_id != book_id or instrument.slug != slug:
                continue
            if status is not None and instrument.status != status:
                continue
            return instrument
        raise NotFound(f"payment instrument slug not found in book: {book_id}/{slug}")

    def get_optional_by_slug(self, *, book_id: str, slug: str, status: str | None = "active") -> PaymentInstrument | None:
        try:
            return self.get_by_slug(book_id=book_id, slug=slug, status=status)
        except NotFound:
            return None

    def list(
        self,
        *,
        book_id: str = DEFAULT_BOOK_ID,
        account_id: str | None = None,
        status: str | None = "active",
    ) -> list[PaymentInstrument]:
        instruments = [item for item in self.instruments.values() if item.book_id == book_id]
        if account_id is not None:
            instruments = [item for item in instruments if item.account_id == account_id]
        if status is not None:
            instruments = [item for item in instruments if item.status == status]
        return sorted(instruments, key=lambda item: (item.slug, item.instrument_id))

    def mark_clean(self) -> None:
        self._dirty_instrument_ids.clear()

    def dirty_instruments(self) -> list[PaymentInstrument]:
        return [
            self.instruments[instrument_id]
            for instrument_id in self._dirty_instrument_ids
            if instrument_id in self.instruments
        ]
