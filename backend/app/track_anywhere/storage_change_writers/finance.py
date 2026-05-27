from __future__ import annotations

from ..storage_changes import FinanceChanges, InvestmentChanges


class FinanceChangeStorageWriters:
    def save_finance_change(self, changes: FinanceChanges) -> None:
        accounts = list(changes.accounts)
        with self.unit_of_work() as uow:
            uow.funds.save(changes.funds)
            if changes.budget_changes is not None:
                uow.budgets.save(changes.budget_changes.budgets, changes.budget_changes.targets)
            uow.accounts.save(accounts)
            uow.ledger.save_transactions(changes.transactions)
            uow.assets.save(changes.assets)
            self._save_write_metadata(uow, changes.metadata)
            uow.reconciliation.save(changes.actions)
        self.update_read_cache(accounts=accounts, transactions=changes.transactions)

    def save_investment_change(self, changes: InvestmentChanges) -> None:
        accounts = list(changes.accounts)
        with self.unit_of_work() as uow:
            uow.investments.save_events(changes.events)
            uow.investments.save_valuations(changes.valuations)
            uow.accounts.save(accounts)
            uow.ledger.save_transactions(changes.transactions)
            uow.assets.save(changes.assets)
            self._save_write_metadata(uow, changes.metadata)
            uow.ledger.save_adjustment_accounts(changes.adjustment_account_ids)
        self.update_read_cache(accounts=accounts, transactions=changes.transactions)
