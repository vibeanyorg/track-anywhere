from __future__ import annotations

from typing import Any, Iterable

from sqlalchemy import delete

from ..storage_models import AdjustmentAccountRecord, AppStateRecord


class LedgerRepository:
    def __init__(self, storage, session) -> None:
        self.storage = storage
        self.session = session

    def save_accounts(self, accounts: Iterable[Any]) -> None:
        self.storage._save_accounts(self.session, accounts)

    def save_transactions(self, transactions: Iterable[Any]) -> None:
        self.storage._save_transactions(self.session, transactions)

    def save_adjustment_accounts(self, adjustment_account_ids: dict[str, str]) -> None:
        for currency, account_id in adjustment_account_ids.items():
            self.session.merge(AdjustmentAccountRecord(currency=currency, account_id=account_id))


class StateRepository:
    def __init__(self, session) -> None:
        self.session = session

    def delete_app_state(self, key: str) -> None:
        self.session.execute(delete(AppStateRecord).where(AppStateRecord.key == key))
