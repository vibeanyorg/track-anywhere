from __future__ import annotations

from ..storage_changes import FinanceChanges, InvestmentChanges


class ServiceFinancePersistence:
    def _commit_finance_change(
        self,
        *,
        funds=(),
        budgets: bool = False,
        transactions=(),
        accounts=(),
        actions=(),
    ) -> None:
        metadata = self._write_metadata()
        changes = FinanceChanges(
            metadata=metadata,
            funds=tuple(funds),
            budget_changes=self._budget_changes() if budgets else None,
            transactions=tuple(transactions),
            accounts=tuple(accounts),
            assets=tuple(self.assets.dirty_assets()),
            actions=tuple(actions),
        )
        self.storage.save_finance_change(changes)
        self._mark_metadata_committed(metadata)
        if budgets:
            self.budgets.mark_clean()
        self.assets.mark_clean()

    def _commit_investment_change(self, *, events=(), valuations=(), transactions=(), accounts=()) -> None:
        metadata = self._write_metadata()
        changes = InvestmentChanges(
            metadata=metadata,
            events=tuple(events),
            valuations=tuple(valuations),
            transactions=tuple(transactions),
            accounts=tuple(accounts),
            assets=tuple(self.assets.dirty_assets()),
            adjustment_account_ids=dict(self.adjustment_account_ids),
        )
        self.storage.save_investment_change(changes)
        self._mark_metadata_committed(metadata)
        self.assets.mark_clean()
