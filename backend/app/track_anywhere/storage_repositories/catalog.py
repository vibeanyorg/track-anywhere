from __future__ import annotations

from typing import Any, Iterable


class AssetRepository:
    def __init__(self, storage, session) -> None:
        self.storage = storage
        self.session = session

    def save(self, assets: Iterable[Any]) -> None:
        self.storage._save_assets(self.session, assets)


class BookRepository:
    def __init__(self, storage, session) -> None:
        self.storage = storage
        self.session = session

    def save(self, books: Iterable[Any], members: Iterable[Any]) -> None:
        self.storage._save_books(self.session, books, members)


class CategoryRepository:
    def __init__(self, storage, session) -> None:
        self.storage = storage
        self.session = session

    def save(self, categories: Iterable[Any]) -> None:
        self.storage._save_categories(self.session, categories)

    def save_history(self, *, aliases, versions, events) -> None:
        self.storage._save_category_history(self.session, aliases=aliases, versions=versions, events=events)


class CounterpartyRepository:
    def __init__(self, storage, session) -> None:
        self.storage = storage
        self.session = session

    def save(self, counterparties: Iterable[Any]) -> None:
        self.storage._save_counterparties(self.session, counterparties)


class PaymentInstrumentRepository:
    def __init__(self, storage, session) -> None:
        self.storage = storage
        self.session = session

    def save(self, instruments: Iterable[Any]) -> None:
        self.storage._save_payment_instruments(self.session, instruments)


class PaymentProfileRepository:
    def __init__(self, storage, session) -> None:
        self.storage = storage
        self.session = session

    def save(self, profiles: Iterable[Any]) -> None:
        self.storage._save_payment_profiles(self.session, profiles)
