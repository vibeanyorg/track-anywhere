from __future__ import annotations

from decimal import Decimal
from typing import Any, Iterable

from sqlalchemy import select

from ..books import DEFAULT_BOOK_ID
from ..errors import NotFound
from ..payment_instrument_storage_models import PaymentInstrumentRecord
from ..payment_instruments import PaymentInstrument
from ..payment_profile_storage_models import PaymentProfileRecord
from ..payment_profiles import PaymentProfile


class PaymentInstrumentRepository:
    def __init__(self, _storage, session) -> None:
        self.session = session

    def list_instruments(
        self,
        *,
        book_id: str | None = DEFAULT_BOOK_ID,
        account_id: str | None = None,
        status: str | None = "active",
    ) -> list[PaymentInstrument]:
        statement = select(PaymentInstrumentRecord)
        if book_id is not None:
            statement = statement.where(PaymentInstrumentRecord.book_id == book_id)
        if account_id is not None:
            statement = statement.where(PaymentInstrumentRecord.account_id == account_id)
        if status is not None:
            statement = statement.where(PaymentInstrumentRecord.status == status)
        instruments = [payment_instrument_from_record(row) for row in self.session.scalars(statement)]
        return sorted(instruments, key=lambda instrument: (instrument.slug, instrument.instrument_id))

    def get_instrument(
        self,
        instrument_id: str,
        *,
        status: str | None = "active",
    ) -> PaymentInstrument:
        row = self.session.get(PaymentInstrumentRecord, instrument_id)
        if row is None or (status is not None and row.status != status):
            raise NotFound(f"payment instrument not found: {instrument_id}")
        return payment_instrument_from_record(row)

    def get_instrument_by_slug(
        self,
        *,
        book_id: str,
        slug: str,
        status: str | None = "active",
    ) -> PaymentInstrument:
        statement = select(PaymentInstrumentRecord).where(
            PaymentInstrumentRecord.book_id == book_id,
            PaymentInstrumentRecord.slug == slug,
        )
        if status is not None:
            statement = statement.where(PaymentInstrumentRecord.status == status)
        row = self.session.scalars(statement).first()
        if row is None:
            raise NotFound(f"payment instrument slug not found in book: {book_id}/{slug}")
        return payment_instrument_from_record(row)

    def save(self, instruments: Iterable[Any]) -> None:
        for instrument in instruments:
            self.session.merge(
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


class PaymentProfileRepository:
    def __init__(self, _storage, session) -> None:
        self.session = session

    def list_profiles(
        self,
        *,
        book_id: str | None = DEFAULT_BOOK_ID,
        status: str | None = "active",
    ) -> list[PaymentProfile]:
        statement = select(PaymentProfileRecord)
        if book_id is not None:
            statement = statement.where(PaymentProfileRecord.book_id == book_id)
        if status is not None:
            statement = statement.where(PaymentProfileRecord.status == status)
        profiles = [payment_profile_from_record(row) for row in self.session.scalars(statement)]
        return sorted(profiles, key=lambda profile: (profile.slug, profile.profile_id))

    def get_profile(
        self,
        profile_id: str,
        *,
        status: str | None = "active",
    ) -> PaymentProfile:
        row = self.session.get(PaymentProfileRecord, profile_id)
        if row is None or (status is not None and row.status != status):
            raise NotFound(f"payment profile not found: {profile_id}")
        return payment_profile_from_record(row)

    def get_profile_by_slug(
        self,
        *,
        book_id: str,
        slug: str,
        status: str | None = "active",
    ) -> PaymentProfile:
        statement = select(PaymentProfileRecord).where(
            PaymentProfileRecord.book_id == book_id,
            PaymentProfileRecord.slug == slug,
        )
        if status is not None:
            statement = statement.where(PaymentProfileRecord.status == status)
        row = self.session.scalars(statement).first()
        if row is None:
            raise NotFound(f"payment profile slug not found in book: {book_id}/{slug}")
        return payment_profile_from_record(row)

    def save(self, profiles: Iterable[Any]) -> None:
        for profile in profiles:
            self.session.merge(
                PaymentProfileRecord(
                    profile_id=profile.profile_id,
                    book_id=profile.book_id,
                    slug=profile.slug,
                    display_name=profile.display_name,
                    kind=profile.kind,
                    instrument_account_id=profile.instrument_account_id,
                    instrument_currency=profile.instrument_currency,
                    backing_account_id=profile.backing_account_id,
                    backing_currency=profile.backing_currency,
                    settlement_mode=profile.settlement_mode,
                    settlement_rate=str(profile.settlement_rate),
                    status=profile.status,
                    version=profile.version,
                )
            )


def payment_instrument_from_record(row: PaymentInstrumentRecord) -> PaymentInstrument:
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


def payment_profile_from_record(row: PaymentProfileRecord) -> PaymentProfile:
    return PaymentProfile(
        profile_id=row.profile_id,
        book_id=row.book_id,
        slug=row.slug,
        display_name=row.display_name,
        kind=row.kind,
        instrument_account_id=row.instrument_account_id,
        instrument_currency=row.instrument_currency,
        backing_account_id=row.backing_account_id,
        backing_currency=row.backing_currency,
        settlement_mode=row.settlement_mode,
        settlement_rate=Decimal(row.settlement_rate),
        status=row.status,
        version=row.version,
    )
