from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


ACCOUNT_SUBTYPE_PATTERN = r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$"
_ACCOUNT_SUBTYPE = re.compile(ACCOUNT_SUBTYPE_PATTERN)


class PostingSide(str, Enum):
    DEBIT = "debit"
    CREDIT = "credit"


class TransactionKind(str, Enum):
    STANDARD = "standard"
    OPENING = "opening"
    ADJUSTMENT = "adjustment"
    TRANSFER = "transfer"
    REFUND = "refund"
    FX = "fx"
    CREDIT_CARD_PAYMENT = "credit_card_payment"
    INVESTMENT_CASH = "investment_cash"


class AccountSystemRole(str, Enum):
    STANDARD = "standard"
    EXPENSE_CLEARING = "expense_clearing"
    INCOME_CLEARING = "income_clearing"
    BALANCE_ADJUSTMENT = "balance_adjustment"
    FX_TRADING = "fx_trading"


class AccountType(str, Enum):
    ASSET = "asset"
    LIABILITY = "liability"
    EQUITY = "equity"
    INCOME = "income"
    EXPENSE = "expense"
    FUND = "fund"
    SYSTEM = "system"


def is_valid_account_subtype(value: object) -> bool:
    return value is None or (
        type(value) is str
        and len(value) <= 64
        and _ACCOUNT_SUBTYPE.fullmatch(value) is not None
    )


class JournalError(ValueError):
    """Base class for journal invariant violations."""


class InvalidAccountCatalog(JournalError):
    """Raised when an account snapshot cannot be resolved unambiguously."""


@dataclass(frozen=True, slots=True)
class PostingDraft:
    posting_id: str
    position: int
    account_id: str
    asset_code: str
    side: PostingSide
    units: int


@dataclass(frozen=True, slots=True)
class AccountSnapshot:
    account_id: str
    book_id: str
    asset_code: str
    account_type: AccountType
    account_subtype: str | None = None
    system_role: AccountSystemRole = AccountSystemRole.STANDARD
    status: str = "active"


@dataclass(frozen=True, slots=True)
class AccountCatalogSnapshot:
    accounts: tuple[AccountSnapshot, ...]

    def _index_by_identity(self) -> dict[tuple[str, str], AccountSnapshot]:
        if type(self.accounts) is not tuple:
            raise InvalidAccountCatalog("catalog accounts must be an immutable tuple")

        indexed: dict[tuple[str, str], AccountSnapshot] = {}
        for account in self.accounts:
            if type(account) is not AccountSnapshot:
                raise InvalidAccountCatalog(
                    "each catalog entry must be an AccountSnapshot"
                )
            if (
                type(account.account_id) is not str
                or not account.account_id
                or type(account.book_id) is not str
                or not account.book_id
                or type(account.asset_code) is not str
                or not account.asset_code
                or type(account.account_type) is not AccountType
                or not is_valid_account_subtype(account.account_subtype)
                or (
                    account.account_subtype == "credit_card"
                    and account.account_type is not AccountType.LIABILITY
                )
                or type(account.system_role) is not AccountSystemRole
                or type(account.status) is not str
                or account.status not in {"active", "closed"}
            ):
                raise InvalidAccountCatalog(
                    "account snapshot has an invalid runtime shape"
                )

            identity = (account.book_id, account.account_id)
            if identity in indexed:
                raise InvalidAccountCatalog(
                    "duplicate account snapshot for book "
                    f"{account.book_id} and account {account.account_id}"
                )
            indexed[identity] = account
        return indexed

    def resolve(self, book_id: str, account_id: str) -> AccountSnapshot | None:
        if type(book_id) is not str or not book_id:
            raise InvalidAccountCatalog("book id must be a non-empty string")
        if type(account_id) is not str or not account_id:
            raise InvalidAccountCatalog("account id must be a non-empty string")
        return self._index_by_identity().get((book_id, account_id))
