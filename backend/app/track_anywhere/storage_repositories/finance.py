from __future__ import annotations

from decimal import Decimal
from typing import Any, Iterable

from ..credit_cards import CreditCardProfile
from ..domain_storage_models import BudgetRecord, BudgetTargetRecord
from ..storage_json import to_jsonable
from ..storage_models import (
    CreditCardProfileRecord,
    FundRecord,
    InvestmentEventRecord,
    InvestmentValuationRecord,
    ReconciliationActionRecord,
)


class CreditCardRepository:
    def __init__(self, session) -> None:
        self.session = session

    def get_profile_optional(self, account_id: str) -> CreditCardProfile | None:
        row = self.session.get(CreditCardProfileRecord, account_id)
        if row is None:
            return None
        return CreditCardProfile(
            account_id=row.account_id,
            credit_limit=Decimal(row.credit_limit) if row.credit_limit is not None else None,
            available_credit=Decimal(row.available_credit) if row.available_credit is not None else None,
            statement_day=row.statement_day,
            due_day=row.due_day,
            annual_fee=Decimal(row.annual_fee) if row.annual_fee is not None else None,
            version=row.version,
        )

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
    def __init__(self, _storage, session) -> None:
        self.session = session

    def save(self, funds: Iterable[Any]) -> None:
        for fund in funds:
            self.session.merge(
                FundRecord(
                    fund_id=fund.fund_id,
                    book_id=fund.book_id,
                    account_id=fund.account_id,
                    name=fund.name,
                    currency=fund.currency,
                    allocated=str(fund.allocated),
                    spent=str(fund.spent),
                    version=fund.version,
                    flow=to_jsonable(fund.flow),
                )
            )


class BudgetRepository:
    def __init__(self, _storage, session) -> None:
        self.session = session

    def save(self, budgets: Iterable[Any], targets: Iterable[Any]) -> None:
        for budget in budgets:
            self.session.merge(
                BudgetRecord(
                    budget_id=budget.budget_id,
                    book_id=budget.book_id,
                    name=budget.name,
                    period=budget.period,
                    starts_on=budget.starts_on.isoformat() if budget.starts_on else None,
                    ends_on=budget.ends_on.isoformat() if budget.ends_on else None,
                    currency=budget.currency,
                    total_amount=str(budget.total_amount),
                    rollover_policy=budget.rollover_policy,
                    alert_thresholds=list(budget.alert_thresholds),
                    status=budget.status,
                    version=budget.version,
                )
            )
        for target in targets:
            self.session.merge(
                BudgetTargetRecord(
                    budget_target_id=target.budget_target_id,
                    budget_id=target.budget_id,
                    target_type=target.target_type,
                    target_id=target.target_id,
                    mode=target.mode,
                    amount=str(target.amount) if target.amount is not None else None,
                    metadata_json=to_jsonable(target.metadata),
                    version=target.version,
                )
            )


class InvestmentRepository:
    def __init__(self, _storage, session) -> None:
        self.session = session

    def save_events(self, events: Iterable[Any]) -> None:
        for event in events:
            self.session.merge(
                InvestmentEventRecord(
                    event_id=event.event_id,
                    book_id=event.book_id,
                    account_id=event.account_id,
                    event_type=event.event_type,
                    amount=str(event.amount),
                    currency=event.currency,
                    occurred_at=event.occurred_at.isoformat(),
                    memo=event.memo,
                    units=str(event.units) if event.units is not None else None,
                    nav=str(event.nav) if event.nav is not None else None,
                    transaction_id=event.transaction_id,
                    version=event.version,
                )
            )

    def save_valuations(self, valuations: Iterable[Any]) -> None:
        for valuation in valuations:
            self.session.merge(
                InvestmentValuationRecord(
                    valuation_id=valuation.valuation_id,
                    book_id=valuation.book_id,
                    account_id=valuation.account_id,
                    value=str(valuation.value),
                    currency=valuation.currency,
                    observed_at=valuation.observed_at.isoformat(),
                    source=valuation.source,
                    memo=valuation.memo,
                    version=valuation.version,
                )
            )


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
