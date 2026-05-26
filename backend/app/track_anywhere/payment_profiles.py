from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from uuid import uuid4

from .books import DEFAULT_BOOK_ID
from .errors import NotFound


@dataclass
class PaymentProfile:
    profile_id: str
    book_id: str
    slug: str
    display_name: str
    kind: str
    instrument_account_id: str
    instrument_currency: str
    backing_account_id: str
    backing_currency: str
    settlement_mode: str
    settlement_rate: Decimal
    status: str = "active"
    version: int = 1


class PaymentProfileDirectory:
    def __init__(self) -> None:
        self.profiles: dict[str, PaymentProfile] = {}
        self._dirty_profile_ids: set[str] = set()

    def create(
        self,
        *,
        book_id: str,
        slug: str,
        display_name: str,
        kind: str,
        instrument_account_id: str,
        instrument_currency: str,
        backing_account_id: str,
        backing_currency: str,
        settlement_mode: str,
        settlement_rate: Decimal,
    ) -> PaymentProfile:
        profile = PaymentProfile(
            profile_id=f"pp_{uuid4().hex}",
            book_id=book_id,
            slug=slug,
            display_name=display_name,
            kind=kind,
            instrument_account_id=instrument_account_id,
            instrument_currency=instrument_currency,
            backing_account_id=backing_account_id,
            backing_currency=backing_currency,
            settlement_mode=settlement_mode,
            settlement_rate=settlement_rate,
        )
        self.profiles[profile.profile_id] = profile
        self._dirty_profile_ids.add(profile.profile_id)
        return profile

    def get(self, profile_id: str, *, status: str | None = "active") -> PaymentProfile:
        try:
            profile = self.profiles[profile_id]
        except KeyError as exc:
            raise NotFound(f"payment profile not found: {profile_id}") from exc
        if status is not None and profile.status != status:
            raise NotFound(f"payment profile not found: {profile_id}")
        return profile

    def get_by_slug(self, *, book_id: str, slug: str, status: str | None = "active") -> PaymentProfile:
        for profile in self.profiles.values():
            if profile.book_id != book_id or profile.slug != slug:
                continue
            if status is not None and profile.status != status:
                continue
            return profile
        raise NotFound(f"payment profile slug not found in book: {book_id}/{slug}")

    def get_optional_by_slug(self, *, book_id: str, slug: str, status: str | None = "active") -> PaymentProfile | None:
        try:
            return self.get_by_slug(book_id=book_id, slug=slug, status=status)
        except NotFound:
            return None

    def list(self, *, book_id: str = DEFAULT_BOOK_ID, status: str | None = "active") -> list[PaymentProfile]:
        profiles = [profile for profile in self.profiles.values() if profile.book_id == book_id]
        if status is not None:
            profiles = [profile for profile in profiles if profile.status == status]
        return sorted(profiles, key=lambda profile: (profile.slug, profile.profile_id))

    def mark_clean(self) -> None:
        self._dirty_profile_ids.clear()

    def dirty_profiles(self) -> list[PaymentProfile]:
        return [
            self.profiles[profile_id]
            for profile_id in self._dirty_profile_ids
            if profile_id in self.profiles
        ]
