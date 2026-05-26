from __future__ import annotations

from copy import deepcopy
from typing import Any


class StorageReadCache:
    def refresh_read_cache_from_service(self, service: Any) -> None:
        self._read_accounts = deepcopy(service.ledger.accounts)
        self._read_transactions = deepcopy(service.ledger.transactions)
        self._read_categories = deepcopy(service.categories.categories)
        self._read_recurring_items = deepcopy(service.recurring.items)
        self._read_payment_instruments = deepcopy(service.payment_instruments.instruments)
        self._read_payment_profiles = deepcopy(service.payment_profiles.profiles)
        self._read_credit_card_profiles = deepcopy(service.credit_cards.profiles)

    def _cached_values(self, name: str):
        cache = getattr(self, f"_read_{name}", None)
        return deepcopy(list(cache.values())) if cache is not None else None

    def _cache_loaded(self, name: str) -> bool:
        return getattr(self, f"_read_{name}", None) is not None

    def _cached_get(self, name: str, key: str):
        cache = getattr(self, f"_read_{name}", None)
        if cache is None or key not in cache:
            return None
        return deepcopy(cache[key])
