from __future__ import annotations

from copy import deepcopy
from decimal import Decimal

from .counterparty_storage_models import CounterpartyRecord
from .credit_cards import CreditCardProfile
from .storage_catalog_reads import _account_from_row, _category_from_row
from .storage_models import AccountRecord, CategoryRecord, CreditCardProfileRecord
from .storage_repositories.catalog import counterparty_from_record


class StorageReadCache:
    def refresh_read_cache_from_storage(self) -> None:
        with self.session_factory() as session:
            self._read_accounts = {
                row.account_id: _account_from_row(row)
                for row in session.query(AccountRecord).all()
            }
            self._read_transactions = self._load_transactions(session)
            self._read_categories = {
                row.category_id: _category_from_row(row)
                for row in session.query(CategoryRecord).all()
            }
            self._read_counterparties = {
                row.counterparty_id: counterparty_from_record(row)
                for row in session.query(CounterpartyRecord).all()
            }
            self._read_drafts = self._load_drafts(session)
            self._read_recurring_items = self._load_recurring_items(session)
            self._read_payment_instruments = self._load_payment_instruments(session)
            self._read_payment_profiles = self._load_payment_profiles(session)
            self._read_credit_card_profiles = {
                row.account_id: CreditCardProfile(
                    account_id=row.account_id,
                    credit_limit=Decimal(row.credit_limit) if row.credit_limit is not None else None,
                    available_credit=Decimal(row.available_credit) if row.available_credit is not None else None,
                    statement_day=row.statement_day,
                    due_day=row.due_day,
                    annual_fee=Decimal(row.annual_fee) if row.annual_fee is not None else None,
                    version=row.version,
                )
                for row in session.query(CreditCardProfileRecord).all()
            }

    def update_read_cache(
        self,
        *,
        accounts=(),
        transactions=(),
        categories=(),
        counterparties=(),
        drafts=(),
        recurring_items=(),
        payment_instruments=(),
        payment_profiles=(),
        credit_card_profiles=(),
    ) -> None:
        self._replace_cached("accounts", accounts, "account_id")
        self._replace_cached("transactions", transactions, "transaction_id")
        self._replace_cached("categories", categories, "category_id")
        self._replace_cached("counterparties", counterparties, "counterparty_id")
        self._replace_cached("drafts", drafts, "draft_id")
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
