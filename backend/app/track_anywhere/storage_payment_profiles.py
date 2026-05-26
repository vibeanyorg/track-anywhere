from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

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
