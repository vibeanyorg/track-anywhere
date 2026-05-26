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

    def update_read_cache(
        self,
        *,
        accounts=(),
        transactions=(),
        categories=(),
        recurring_items=(),
        payment_instruments=(),
        payment_profiles=(),
        credit_card_profiles=(),
    ) -> None:
        self._replace_cached("accounts", accounts, "account_id")
        self._replace_cached("transactions", transactions, "transaction_id")
        self._replace_cached("categories", categories, "category_id")
        self._replace_cached("recurring_items", recurring_items, "recurring_id")
        self._replace_cached("payment_instruments", payment_instruments, "instrument_id")
        self._replace_cached("payment_profiles", payment_profiles, "profile_id")
        self._replace_cached("credit_card_profiles", credit_card_profiles, "account_id")

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

    def _replace_cached(self, name: str, items, key_attr: str) -> None:
        cache = getattr(self, f"_read_{name}", None)
        if cache is None:
            return
        for item in items:
            cache[getattr(item, key_attr)] = deepcopy(item)
