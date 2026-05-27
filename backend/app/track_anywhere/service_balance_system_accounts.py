from __future__ import annotations

from .categories import Category
from .errors import NotFound, ValidationError


class SystemAccountUseCases:
    def _system_adjustment_account_id(self, currency: str, *, book_id: str | None = None) -> str:
        return self._system_adjustment_account(currency, book_id=book_id).account_id

    def _system_adjustment_account(self, currency: str, *, book_id: str | None = None, created_accounts=None):
        book_id = book_id or self.books.ensure_default().book_id
        key = f"{book_id}:{currency}"
        account_id = self.adjustment_account_ids.get(key) or self.adjustment_account_ids.get(currency)
        if account_id is not None:
            try:
                return self.storage.get_account(account_id)
            except NotFound:
                for account in created_accounts or ():
                    if account.account_id == account_id:
                        return account
        account = self._find_system_account(
            type="system",
            currency=currency,
            book_id=book_id,
            subtype="system_adjustment",
        )
        if account is not None:
            self.adjustment_account_ids[key] = account.account_id
            return account
        account = self._new_account(
            f"System balance adjustments {currency}",
            "system",
            currency,
            institution_type="system",
            subtype="system_adjustment",
            institution="track-anywhere",
            book_id=book_id,
        )
        if created_accounts is not None:
            created_accounts.append(account)
        self.adjustment_account_ids[key] = account.account_id
        return account

    def _system_fx_clearing_account_id(self, currency: str, *, book_id: str | None = None) -> str:
        return self._system_fx_clearing_account(currency, book_id=book_id).account_id

    def _system_fx_clearing_account(self, currency: str, *, book_id: str | None = None, created_accounts=None):
        book_id = book_id or self.books.ensure_default().book_id
        account = self._find_system_account(
            type="system",
            currency=currency,
            book_id=book_id,
            subtype="fx_clearing",
        )
        if account is not None:
            return account
        account = self._new_account(
            f"System FX clearing {currency}",
            "system",
            currency,
            institution_type="system",
            subtype="fx_clearing",
            institution="track-anywhere",
            book_id=book_id,
        )
        if created_accounts is not None:
            created_accounts.append(account)
        return account

    def _system_category_account_id(self, kind: str, currency: str, *, book_id: str | None = None) -> str:
        return self._system_category_account(kind, currency, book_id=book_id).account_id

    def _system_category_account(self, kind: str, currency: str, *, book_id: str | None = None, created_accounts=None):
        book_id = book_id or self.books.ensure_default().book_id
        subtype = f"{kind}_clearing"
        account = self._find_system_account(type=kind, currency=currency, book_id=book_id, subtype=subtype)
        if account is not None:
            return account
        account = self._new_account(
            f"System {kind} {currency}",
            kind,
            currency,
            institution_type="system",
            subtype=subtype,
            institution="track-anywhere",
            book_id=book_id,
        )
        if created_accounts is not None:
            created_accounts.append(account)
        return account

    def _validate_transaction_category(self, category: Category, *, from_account_id: str, to_account_id: str) -> None:
        from_account = self._transaction_account(from_account_id)
        to_account = self._transaction_account(to_account_id)
        if category.kind == "expense" and to_account.type != "expense":
            raise ValidationError("expense category requires an expense to-account")
        if category.kind == "income" and from_account.type != "income":
            raise ValidationError("income category requires an income from-account")

    def _transaction_account(self, account_id: str):
        return self.storage.get_account(account_id)

    def _find_system_account(self, *, type: str, currency: str, book_id: str, subtype: str):
        matches = self.storage.list_accounts(
            book_id=book_id,
            type=type,
            currency=currency,
            institution_type="system",
            subtype=subtype,
            institution="track-anywhere",
        )
        return matches[0] if matches else None
