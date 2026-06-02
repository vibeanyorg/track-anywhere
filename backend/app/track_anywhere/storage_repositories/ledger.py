from __future__ import annotations

from decimal import Decimal
from typing import Any, Iterable

from sqlalchemy import delete, func, select

from ..books import DEFAULT_BOOK_ID
from ..errors import NotFound
from ..ledger import Account
from ..storage_ledger_reads import (
    _amount_scale_expression,
    _canonical_decimal,
    _effective_posting_amount_expression,
)
from ..storage_models import AdjustmentAccountRecord, AppStateRecord, PostingRecord
from ..storage_models import AccountRecord
from ..storage_upsert_writers import upsert_record


class AccountRepository:
    def __init__(self, _storage, session) -> None:
        self.session = session

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
        statement = select(AccountRecord)
        if book_id is not None:
            statement = statement.where(AccountRecord.book_id == book_id)
        if type is not None:
            statement = statement.where(AccountRecord.type == type)
        if currency is not None:
            statement = statement.where(AccountRecord.currency == currency)
        if institution_type is not None:
            statement = statement.where(AccountRecord.institution_type == institution_type)
        if subtype is not None:
            statement = statement.where(AccountRecord.subtype == subtype)
        accounts = [account_from_record(row) for row in self.session.scalars(statement)]
        if name:
            lowered = name.lower()
            accounts = [account for account in accounts if lowered in account.name.lower()]
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
        row = self.session.get(AccountRecord, account_id)
        if row is None:
            raise NotFound(f"account not found: {account_id}")
        return account_from_record(row)

    def save(self, accounts: Iterable[Any]) -> None:
        for account in accounts:
            upsert_record(
                self.session,
                AccountRecord,
                {
                    "account_id": account.account_id,
                    "book_id": account.book_id,
                    "name": account.name,
                    "type": account.type,
                    "currency": account.currency,
                    "institution_type": account.institution_type,
                    "subtype": account.subtype,
                    "institution": account.institution,
                    "version": account.version,
                },
                ["account_id"],
            )


class LedgerRepository:
    def __init__(self, _storage, session) -> None:
        self.session = session

    def account_balances(self, account_ids: Iterable[str]) -> dict[tuple[str, str], Decimal]:
        ids = sorted(set(account_ids))
        if not ids:
            return {}
        max_scale = _amount_scale_expression(self.session, PostingRecord.amount)
        effective_amount = _effective_posting_amount_expression(self.session)
        rows = self.session.execute(
            select(
                PostingRecord.account_id,
                PostingRecord.currency,
                func.sum(effective_amount).label("amount"),
                max_scale.label("scale"),
            )
            .join(AccountRecord, AccountRecord.account_id == PostingRecord.account_id)
            .where(PostingRecord.account_id.in_(ids))
            .group_by(PostingRecord.account_id, PostingRecord.currency)
        )
        return {
            (account_id, currency): _canonical_decimal(Decimal(amount or 0), scale=int(scale or 0))
            for account_id, currency, amount, scale in rows
        }

    def save_adjustment_accounts(self, adjustment_account_ids: dict[str, str]) -> None:
        for currency, account_id in adjustment_account_ids.items():
            self.session.merge(AdjustmentAccountRecord(currency=currency, account_id=account_id))


def account_from_record(row: AccountRecord) -> Account:
    return Account(
        account_id=row.account_id,
        book_id=row.book_id,
        name=row.name,
        type=row.type,
        currency=row.currency,
        institution_type=row.institution_type,
        subtype=row.subtype,
        institution=row.institution,
        version=row.version,
    )


class StateRepository:
    def __init__(self, session) -> None:
        self.session = session

    def delete_app_state(self, key: str) -> None:
        self.session.execute(delete(AppStateRecord).where(AppStateRecord.key == key))
