from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from .books import DEFAULT_BOOK_ID
from .errors import NotFound
from .payment_profiles import PaymentProfile
from .payment_profile_storage_models import PaymentProfileRecord


class PaymentProfileStorageMixin:
    @staticmethod
    def _iter_payment_profiles(service: Any, *, only_dirty: bool = False) -> list[Any]:
        profiles = getattr(service, "payment_profiles", None)
        if profiles is None:
            return []
        if only_dirty and hasattr(profiles, "dirty_profiles"):
            return list(profiles.dirty_profiles())
        if isinstance(profiles, dict):
            return list(profiles.values())
        container_profiles = getattr(profiles, "profiles", None)
        if isinstance(container_profiles, dict):
            return list(container_profiles.values())
        return []

    def _hydrate_payment_profiles(self, service: Any, payment_profiles: dict[str, Any]) -> None:
        container = getattr(service, "payment_profiles", None)
        if container is None:
            return
        if isinstance(container, dict):
            container.clear()
            container.update(payment_profiles)
            return
        if isinstance(getattr(container, "profiles", None), dict):
            container.profiles = dict(payment_profiles)
            if hasattr(container, "mark_clean"):
                container.mark_clean()

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

    def _save_payment_profiles(self, session: Session, service: Any, *, only_dirty: bool = False) -> None:
        for profile in self._iter_payment_profiles(service, only_dirty=only_dirty):
            session.merge(
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
        payment_profiles = getattr(service, "payment_profiles", None)
        if hasattr(payment_profiles, "mark_clean"):
            payment_profiles.mark_clean()


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
