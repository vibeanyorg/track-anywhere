from __future__ import annotations

from sqlalchemy.orm import Session

from .books import DEFAULT_BOOK_ID
from .errors import NotFound
from .payment_instrument_storage_models import PaymentInstrumentRecord
from .payment_instruments import PaymentInstrument
from .storage_repositories.catalog import payment_instrument_from_record


class PaymentInstrumentStorageMixin:
    def _load_payment_instruments(self, session: Session) -> dict[str, PaymentInstrument]:
        return {
            row.instrument_id: payment_instrument_from_record(row)
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
            instruments = [payment_instrument_from_record(row) for row in rows]
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
        return payment_instrument_from_record(row)

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
