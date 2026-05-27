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
        changes = FinanceChanges(
            metadata=self._write_metadata(),
            funds=tuple(funds),
            budget_changes=self._budget_changes() if budgets else None,
            transactions=tuple(transactions),
            accounts=tuple(accounts),
            assets=tuple(self.assets.dirty_assets()),
            actions=tuple(actions),
        )
        self.storage.save_finance_change(changes)
        if budgets:
            self.budgets.mark_clean()
        self.credentials.mark_clean()
        self.assets.mark_clean()
        self.audit.mark_persisted()
        self.idempotency.mark_clean()
    def _commit_investment_change(self, *, events=(), valuations=(), transactions=(), accounts=()) -> None:
        changes = InvestmentChanges(
            metadata=self._write_metadata(),
            events=tuple(events),
            valuations=tuple(valuations),
            transactions=tuple(transactions),
            accounts=tuple(accounts),
            assets=tuple(self.assets.dirty_assets()),
            adjustment_account_ids=dict(self.adjustment_account_ids),
        )
        self.storage.save_investment_change(changes)
        self.credentials.mark_clean()
        self.assets.mark_clean()
        self.audit.mark_persisted()
        self.idempotency.mark_clean()
