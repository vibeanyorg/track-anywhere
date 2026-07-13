from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class PostingSide(str, Enum):
    DEBIT = "debit"
    CREDIT = "credit"


class TransactionKind(str, Enum):
    STANDARD = "standard"
    OPENING = "opening"
    ADJUSTMENT = "adjustment"
    TRANSFER = "transfer"
    FX = "fx"
    INVESTMENT_CASH = "investment_cash"


class AccountSystemRole(str, Enum):
    STANDARD = "standard"
    FX_TRADING = "fx_trading"


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
    system_role: AccountSystemRole = AccountSystemRole.STANDARD


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
                or type(account.system_role) is not AccountSystemRole
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
