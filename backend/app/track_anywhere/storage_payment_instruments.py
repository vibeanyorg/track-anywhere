from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

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
            row.instrument_id: PaymentInstrument(
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
            for row in session.query(PaymentInstrumentRecord).all()
        }

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
