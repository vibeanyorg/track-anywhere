from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from .books import DEFAULT_BOOK_ID
from .errors import NotFound
from .payment_instrument_storage_models import PaymentInstrumentRecord
from .payment_instruments import PaymentInstrument


class PaymentInstrumentStorageMixin:
    @staticmethod
    def _iter_payment_instruments(service: Any, *, only_dirty: bool = False) -> list[Any]:
        instruments = getattr(service, "payment_instruments", None)
        if instruments is None:
            return []
        if only_dirty and hasattr(instruments, "dirty_instruments"):
            return list(instruments.dirty_instruments())
        if isinstance(instruments, dict):
            return list(instruments.values())
        container_instruments = getattr(instruments, "instruments", None)
        if isinstance(container_instruments, dict):
            return list(container_instruments.values())
        return []

    def _load_payment_instruments(self, session: Session) -> dict[str, PaymentInstrument]:
        return {
            row.instrument_id: _payment_instrument_from_row(row)
            for row in session.query(PaymentInstrumentRecord).all()
        }

    def list_payment_instruments(
        self,
        *,
        book_id: str = DEFAULT_BOOK_ID,
        account_id: str | None = None,
        status: str | None = "active",
    ) -> list[PaymentInstrument]:
        instruments = self._cached_values("payment_instruments")
        if instruments is None:
            with self.session_factory() as session:
                rows = session.query(PaymentInstrumentRecord).all()
            instruments = [_payment_instrument_from_row(row) for row in rows]
        instruments = [instrument for instrument in instruments if instrument.book_id == book_id]
        if account_id is not None:
            instruments = [instrument for instrument in instruments if instrument.account_id == account_id]
        if status is not None:
            instruments = [instrument for instrument in instruments if instrument.status == status]
        return sorted(instruments, key=lambda instrument: (instrument.slug, instrument.instrument_id))

    def get_payment_instrument(self, instrument_id: str, *, status: str | None = "active") -> PaymentInstrument:
        cached = self._cached_get("payment_instruments", instrument_id)
        if cached is not None:
            if status is not None and cached.status != status:
                raise NotFound(f"payment instrument not found: {instrument_id}")
            return cached
        with self.session_factory() as session:
            row = session.get(PaymentInstrumentRecord, instrument_id)
        if row is None or (status is not None and row.status != status):
            raise NotFound(f"payment instrument not found: {instrument_id}")
        return _payment_instrument_from_row(row)

    def get_payment_instrument_by_slug(
        self,
        *,
        book_id: str,
        slug: str,
        status: str | None = "active",
    ) -> PaymentInstrument:
        for instrument in self.list_payment_instruments(book_id=book_id, status=status):
            if instrument.slug == slug:
                return instrument
        raise NotFound(f"payment instrument slug not found in book: {book_id}/{slug}")

    def _hydrate_payment_instruments(self, service: Any, payment_instruments: dict[str, PaymentInstrument]) -> None:
        container = getattr(service, "payment_instruments", None)
        if container is None:
            return
        if isinstance(container, dict):
            container.clear()
            container.update(payment_instruments)
            return
        if isinstance(getattr(container, "instruments", None), dict):
            container.instruments = dict(payment_instruments)
            if hasattr(container, "mark_clean"):
                container.mark_clean()

    def _save_payment_instruments(self, session: Session, service: Any, *, only_dirty: bool = False) -> None:
        for instrument in self._iter_payment_instruments(service, only_dirty=only_dirty):
            session.merge(
                PaymentInstrumentRecord(
                    instrument_id=instrument.instrument_id,
                    book_id=instrument.book_id,
                    slug=instrument.slug,
                    display_name=instrument.display_name,
                    kind=instrument.kind,
                    account_id=instrument.account_id,
                    last4=instrument.last4,
                    status=instrument.status,
                    version=instrument.version,
                )
            )
        payment_instruments = getattr(service, "payment_instruments", None)
        if hasattr(payment_instruments, "mark_clean"):
            payment_instruments.mark_clean()


def _payment_instrument_from_row(row: PaymentInstrumentRecord) -> PaymentInstrument:
    return PaymentInstrument(
        instrument_id=row.instrument_id,
        book_id=row.book_id,
        slug=row.slug,
        display_name=row.display_name,
        kind=row.kind,
        account_id=row.account_id,
        last4=row.last4,
        status=row.status,
        version=row.version,
    )
