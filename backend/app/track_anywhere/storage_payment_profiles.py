from __future__ import annotations

from decimal import Decimal
from sqlalchemy.orm import Session

from .books import DEFAULT_BOOK_ID
from .errors import NotFound
from .payment_profiles import PaymentProfile
from .payment_profile_storage_models import PaymentProfileRecord


class PaymentProfileStorageMixin:
    def list_payment_profiles(
        self,
        *,
        book_id: str = DEFAULT_BOOK_ID,
        status: str | None = "active",
    ) -> list[PaymentProfile]:
        profiles = self._cached_values("payment_profiles")
        if profiles is None:
            with self.session_factory() as session:
                rows = session.query(PaymentProfileRecord).all()
            profiles = [_payment_profile_from_row(row) for row in rows]
        profiles = [profile for profile in profiles if profile.book_id == book_id]
        if status is not None:
            profiles = [profile for profile in profiles if profile.status == status]
        return sorted(profiles, key=lambda profile: (profile.slug, profile.profile_id))

    def get_payment_profile(self, profile_id: str, *, status: str | None = "active") -> PaymentProfile:
        cached = self._cached_get("payment_profiles", profile_id)
        if cached is not None:
            if status is not None and cached.status != status:
                raise NotFound(f"payment profile not found: {profile_id}")
            return cached
        with self.session_factory() as session:
            row = session.get(PaymentProfileRecord, profile_id)
        if row is None or (status is not None and row.status != status):
            raise NotFound(f"payment profile not found: {profile_id}")
        return _payment_profile_from_row(row)

    def get_payment_profile_by_slug(
        self,
        *,
        book_id: str,
        slug: str,
        status: str | None = "active",
    ) -> PaymentProfile:
        for profile in self.list_payment_profiles(book_id=book_id, status=status):
            if profile.slug == slug:
                return profile
        raise NotFound(f"payment profile slug not found in book: {book_id}/{slug}")

def _payment_profile_from_row(row: PaymentProfileRecord) -> PaymentProfile:
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
