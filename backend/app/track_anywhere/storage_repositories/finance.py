from __future__ import annotations

from typing import Any, Iterable

from ..storage_json import to_jsonable
from ..storage_models import CreditCardProfileRecord, ReconciliationActionRecord


class CreditCardRepository:
    def __init__(self, session) -> None:
        self.session = session

    def save_profiles(self, profiles: Iterable[Any]) -> None:
        for profile in profiles:
            self.session.merge(
                CreditCardProfileRecord(
                    account_id=profile.account_id,
                    credit_limit=str(profile.credit_limit) if profile.credit_limit is not None else None,
                    available_credit=str(profile.available_credit) if profile.available_credit is not None else None,
                    statement_day=profile.statement_day,
                    due_day=profile.due_day,
                    annual_fee=str(profile.annual_fee) if profile.annual_fee is not None else None,
                    version=profile.version,
                )
            )

class FundRepository:
    def __init__(self, storage, session) -> None:
        self.storage = storage
        self.session = session

    def save(self, funds: Iterable[Any]) -> None:
        self.storage._save_funds(self.session, funds)


class BudgetRepository:
    def __init__(self, storage, session) -> None:
        self.storage = storage
        self.session = session

    def save(self, budgets: Iterable[Any], targets: Iterable[Any]) -> None:
        self.storage._save_budgets(self.session, budgets, targets)


class InvestmentRepository:
    def __init__(self, storage, session) -> None:
        self.storage = storage
        self.session = session

    def save_events(self, events: Iterable[Any]) -> None:
        self.storage._save_investment_events(self.session, events)

    def save_valuations(self, valuations: Iterable[Any]) -> None:
        self.storage._save_investment_valuations(self.session, valuations)


class ReconciliationRepository:
    def __init__(self, session) -> None:
        self.session = session

    def save(self, actions: Iterable[dict[str, Any]]) -> None:
        for action in actions:
            self.session.merge(
                ReconciliationActionRecord(
                    reconciliation_id=str(action["reconciliation_id"]),
                    payload=to_jsonable(action),
                )
            )
