from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal
from typing import Any

from .balance_semantics import balance_semantics_for_account_type
from .books import DEFAULT_BOOK_ID
from .errors import NotFound, ValidationError
from .ledger import Account
from .service_balance_views import official_account_balance_view


FINANCIAL_LEDGER_ACCOUNT_TYPES = {"asset", "liability", "fund"}
FINANCIAL_ACCOUNT_TYPE_ORDER = {
    "cash": 0,
    "bank": 1,
    "e_wallet": 2,
    "credit_card": 3,
    "brokerage": 4,
    "crypto_wallet": 5,
    "fund": 6,
    "other": 7,
}
INTERNAL_ACCOUNT_SUBTYPES = {
    "opening_equity",
    "system_adjustment",
    "fx_clearing",
    "income_clearing",
    "expense_clearing",
    "clearing",
    "adjustment",
    "category",
    "category_clearing",
}


class AccountQueryUseCases:
    def list_accounts(
        self,
        token: str,
        *,
        name: str | None = None,
        type: str | None = None,
        currency: str | None = None,
        institution_type: str | None = None,
        subtype: str | None = None,
        institution: str | None = None,
        book_id: str | None = DEFAULT_BOOK_ID,
    ) -> list[Account]:
        if book_id is not None:
            self.actor_for_book(token, book_id, "account:read")
        else:
            self.actor_from_token(token, "account:read")
        return self._list_accounts_from_storage(
            book_id=book_id,
            name=name,
            type=type,
            currency=currency,
            institution_type=institution_type,
            subtype=subtype,
            institution=institution,
        )

    def get_account(self, token: str, account_id: str) -> Account:
        account = self._get_account_from_storage(account_id)
        self.actor_for_book(token, account.book_id, "account:read")
        return account

    def list_ledger_accounts(
        self,
        token: str,
        *,
        name: str | None = None,
        type: str | None = None,
        currency: str | None = None,
        institution_type: str | None = None,
        subtype: str | None = None,
        institution: str | None = None,
        book_id: str | None = DEFAULT_BOOK_ID,
    ) -> list[Account]:
        return self.list_accounts(
            token,
            name=name,
            type=type,
            currency=currency,
            institution_type=institution_type,
            subtype=subtype,
            institution=institution,
            book_id=book_id,
        )

    def get_ledger_account(self, token: str, account_id: str) -> Account:
        return self.get_account(token, account_id)

    def list_financial_accounts(
        self,
        token: str,
        *,
        q: str | None = None,
        type: str | None = None,
        currency: str | None = None,
        institution_type: str | None = None,
        subtype: str | None = None,
        institution: str | None = None,
        status: str | None = None,
        include_balance: bool = False,
        book_id: str = DEFAULT_BOOK_ID,
    ) -> list[dict[str, Any]]:
        self.actor_for_book(token, book_id, "account:read")
        if status is not None and status != "active":
            raise ValidationError("status must be active")
        accounts = self._list_accounts_from_storage(
            book_id=book_id,
            currency=currency,
            institution_type=institution_type,
            subtype=subtype,
            institution=institution,
        )
        accounts = [account for account in accounts if _is_user_visible_financial_account(account)]
        if q:
            accounts = [account for account in accounts if _matches_financial_account_query(account, q)]
        if type:
            accounts = [account for account in accounts if _financial_account_type(account) == type]
        accounts = sorted(accounts, key=_financial_account_sort_key)

        balances: dict[tuple[str, str], Decimal] = {}
        confirmed_transaction_count = 0
        draft_count = 0
        if include_balance:
            balances = self._financial_account_balances_from_storage(account.account_id for account in accounts)
            confirmed_transaction_count = self._financial_confirmed_transaction_count_from_storage(book_id=book_id)
            draft_count = self._financial_draft_count_from_storage()

        return [
            self._financial_account_payload(
                account,
                balance=(
                    official_account_balance_view(
                        account,
                        balances.get((account.account_id, account.currency), Decimal("0")),
                        confirmed_transaction_count=confirmed_transaction_count,
                        draft_count=draft_count,
                    )
                    if include_balance
                    else None
                ),
            )
            for account in accounts
        ]

    def get_financial_account(
        self,
        token: str,
        account_id: str,
        *,
        include_balance: bool = False,
    ) -> dict[str, Any]:
        account = self._get_visible_financial_account(token, account_id)
        balance = self._financial_account_balance_for_account(account) if include_balance else None
        return self._financial_account_payload(account, balance=balance)

    def financial_account_balance(self, token: str, account_id: str) -> dict[str, Any]:
        account = self._get_visible_financial_account(token, account_id)
        return self._financial_account_balance_for_account(account)

    def _financial_account_balance_for_account(self, account: Account) -> dict[str, Any]:
        official = self._financial_account_balance_from_storage(account.account_id)
        return official_account_balance_view(
            account,
            official.get(account.currency, Decimal("0")),
            confirmed_transaction_count=self._financial_confirmed_transaction_count_from_storage(book_id=account.book_id),
            draft_count=self._financial_draft_count_from_storage(),
        )

    def _financial_account_payload(self, account: Account, *, balance: dict[str, Any] | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "account_id": account.account_id,
            "ledger_account_id": account.account_id,
            "name": account.name,
            "type": _financial_account_type(account),
            "ledger_account_type": account.type,
            "currency": account.currency,
            "institution_type": account.institution_type,
            "subtype": account.subtype,
            "institution": account.institution,
            "book_id": account.book_id,
            "status": "active",
            "balance_semantics": balance_semantics_for_account_type(account.type),
        }
        if balance is not None:
            payload["balance"] = balance
        return payload

    def _get_visible_financial_account(self, token: str, account_id: str) -> Account:
        account = self._get_account_from_storage(account_id)
        self.actor_for_book(token, account.book_id, "account:read")
        if not _is_user_visible_financial_account(account):
            raise NotFound(f"financial account not found: {account_id}")
        return account

    def _financial_account_balance_from_storage(self, account_id: str) -> dict[str, Decimal]:
        return self.storage.account_balance(account_id)

    def _financial_account_balances_from_storage(self, account_ids: Iterable[str]) -> dict[tuple[str, str], Decimal]:
        return self.storage.account_balances(account_ids)

    def _financial_confirmed_transaction_count_from_storage(self, *, book_id: str | None = None) -> int:
        return self.storage.confirmed_transaction_count(book_id=book_id)

    def _financial_draft_count_from_storage(self) -> int:
        return self.storage.draft_count()

    def _list_accounts_from_storage(
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
        with self.storage.unit_of_work() as uow:
            return uow.accounts.list_accounts(
                book_id=book_id,
                name=name,
                type=type,
                currency=currency,
                institution_type=institution_type,
                subtype=subtype,
                institution=institution,
            )

    def _get_account_from_storage(self, account_id: str) -> Account:
        with self.storage.unit_of_work() as uow:
            return uow.accounts.get_account(account_id)


def _is_user_visible_financial_account(account: Account) -> bool:
    if account.type not in FINANCIAL_LEDGER_ACCOUNT_TYPES:
        return False
    if account.institution_type == "system":
        return False
    if _is_internal_account_subtype(account.subtype):
        return False
    return True


def _is_internal_account_subtype(subtype: str | None) -> bool:
    if subtype is None:
        return False
    return subtype in INTERNAL_ACCOUNT_SUBTYPES or subtype.endswith("_clearing")


def _financial_account_type(account: Account) -> str:
    if account.type == "fund":
        return "fund"
    if account.subtype == "credit_card":
        return "credit_card"
    if account.institution_type in {"cash", "bank", "e_wallet", "brokerage", "crypto_wallet"}:
        return account.institution_type
    return "other"


def _matches_financial_account_query(account: Account, query: str) -> bool:
    lowered = query.lower()
    haystacks = (
        account.account_id,
        account.name,
        account.currency,
        account.institution_type or "",
        account.subtype or "",
        account.institution or "",
        _financial_account_type(account),
    )
    return any(lowered in value.lower() for value in haystacks)


def _financial_account_sort_key(account: Account) -> tuple[int, str, str, str, str]:
    financial_type = _financial_account_type(account)
    return (
        FINANCIAL_ACCOUNT_TYPE_ORDER.get(financial_type, FINANCIAL_ACCOUNT_TYPE_ORDER["other"]),
        account.institution or "",
        account.name,
        account.currency,
        account.account_id,
    )
