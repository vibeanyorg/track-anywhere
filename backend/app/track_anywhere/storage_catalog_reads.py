from __future__ import annotations

from decimal import Decimal

from .books import DEFAULT_BOOK_ID
from .categories import Category
from .category_models import normalize_key
from .credit_cards import CreditCardProfile
from .errors import NotFound, ValidationError
from .ledger import Account
from .recurring import RecurringItem
from .storage_models import AccountRecord, CategoryRecord, CreditCardProfileRecord, RecurringItemRecord
from .storage_repositories.categories import category_from_record
from .storage_repositories.ledger import account_from_record
from .storage_repositories.workflow import recurring_item_from_record


class CatalogReadStorage:
    def list_accounts(
        self,
        *,
        book_id: str | None = DEFAULT_BOOK_ID,
        name: str | None = None,
        type: str | None = None,
        currency: str | None = None,
        institution_type: str | None = None,
        subtype: str | None = None,
        institution: str | None = None,
    ) -> list[Account]:
        accounts = self._cached_values("accounts")
        if accounts is None:
            with self.session_factory() as session:
                rows = session.query(AccountRecord).all()
            accounts = [account_from_record(row) for row in rows]
        if book_id is not None:
            accounts = [account for account in accounts if account.book_id == book_id]
        if name:
            lowered = name.lower()
            accounts = [account for account in accounts if lowered in account.name.lower()]
        if type:
            accounts = [account for account in accounts if account.type == type]
        if currency:
            accounts = [account for account in accounts if account.currency == currency]
        if institution_type:
            accounts = [account for account in accounts if account.institution_type == institution_type]
        if subtype:
            accounts = [account for account in accounts if account.subtype == subtype]
        if institution:
            lowered = institution.lower()
            accounts = [
                account
                for account in accounts
                if account.institution and lowered in account.institution.lower()
            ]
        return sorted(
            accounts,
            key=lambda account: (
                account.type,
                account.institution_type or "",
                account.subtype or "",
                account.name,
                account.account_id,
            ),
        )

    def get_account(self, account_id: str) -> Account:
        cached = self._cached_get("accounts", account_id)
        if cached is not None:
            return cached
        with self.session_factory() as session:
            row = session.get(AccountRecord, account_id)
        if row is None:
            raise NotFound(f"account not found: {account_id}")
        return account_from_record(row)

    def list_categories(
        self,
        *,
        kind: str | None = None,
        name: str | None = None,
        parent_id: str | None = None,
        book_id: str | None = DEFAULT_BOOK_ID,
        status: str | None = "active",
    ) -> list[Category]:
        categories = self._cached_values("categories")
        if categories is None:
            with self.session_factory() as session:
                rows = session.query(CategoryRecord).all()
            categories = [category_from_record(row) for row in rows]
        if book_id is not None:
            categories = [category for category in categories if category.book_id == book_id]
        if status is not None:
            categories = [category for category in categories if category.status == status]
        if kind is not None:
            categories = [category for category in categories if category.kind == kind]
        if name is not None:
            normalized_name = normalize_key(name)
            categories = [category for category in categories if category.normalized_name == normalized_name]
        if parent_id is not None:
            categories = [category for category in categories if category.parent_id == parent_id]
        return sorted(
            categories,
            key=lambda category: (
                category.book_id,
                category.kind,
                category.primary,
                category.level,
                category.secondary or "",
                category.sort_order,
                category.category_id,
            ),
        )

    def get_category(self, category_id: str) -> Category:
        cached = self._cached_get("categories", category_id)
        if cached is not None:
            return cached
        with self.session_factory() as session:
            row = session.get(CategoryRecord, category_id)
        if row is None:
            raise NotFound(f"category not found: {category_id}")
        return category_from_record(row)

    def find_category_by_path(self, *, book_id: str, kind: str, path: str) -> Category | None:
        if kind not in {"income", "expense"}:
            raise ValidationError("category kind must be income or expense")
        parts = [_clean_path_part(part) for part in path.split("/")]
        parts = [part for part in parts if part]
        if not parts:
            raise ValidationError("category path must not be blank")
        if len(parts) > 2:
            raise ValidationError("category path supports at most two levels")
        parent_matches = self.list_categories(kind=kind, name=parts[0], parent_id=None, book_id=book_id)
        parent = parent_matches[0] if parent_matches else None
        if len(parts) == 1 or parent is None:
            return parent
        child_matches = self.list_categories(kind=kind, name=parts[1], parent_id=parent.category_id, book_id=book_id)
        return child_matches[0] if child_matches else None

    def list_recurring_items(
        self,
        *,
        status: str | None = None,
        kind: str | None = None,
        book_id: str | None = DEFAULT_BOOK_ID,
    ) -> list[RecurringItem]:
        items = self._cached_values("recurring_items")
        if items is None:
            with self.session_factory() as session:
                rows = session.query(RecurringItemRecord).all()
            items = [recurring_item_from_record(row) for row in rows]
        if book_id is not None:
            items = [item for item in items if item.book_id == book_id]
        if status is not None:
            items = [item for item in items if item.status == status]
        if kind is not None:
            items = [item for item in items if item.kind == kind]
        return sorted(items, key=lambda item: (item.status, item.name, item.recurring_id))

    def get_recurring_item(self, recurring_id: str) -> RecurringItem:
        cached = self._cached_get("recurring_items", recurring_id)
        if cached is not None:
            return cached
        with self.session_factory() as session:
            row = session.get(RecurringItemRecord, recurring_id)
        if row is None:
            raise NotFound(f"recurring item not found: {recurring_id}")
        return recurring_item_from_record(row)

    def get_credit_card_profile_optional(self, account_id: str) -> CreditCardProfile | None:
        cached = self._cached_get("credit_card_profiles", account_id)
        if cached is not None:
            return cached
        if self._cache_loaded("credit_card_profiles"):
            return None
        with self.session_factory() as session:
            row = session.get(CreditCardProfileRecord, account_id)
        return _credit_card_profile_from_row(row) if row is not None else None

def _credit_card_profile_from_row(row: CreditCardProfileRecord) -> CreditCardProfile:
    return CreditCardProfile(
        account_id=row.account_id,
        credit_limit=Decimal(row.credit_limit) if row.credit_limit is not None else None,
        available_credit=Decimal(row.available_credit) if row.available_credit is not None else None,
        statement_day=row.statement_day,
        due_day=row.due_day,
        annual_fee=Decimal(row.annual_fee) if row.annual_fee is not None else None,
        version=row.version,
    )


def _clean_path_part(value: str) -> str:
    return " ".join(value.strip().split())
