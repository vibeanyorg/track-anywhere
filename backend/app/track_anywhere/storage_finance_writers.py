from __future__ import annotations

from sqlalchemy.orm import Session

from .storage_json import to_jsonable
from .storage_models import FundRecord, InvestmentEventRecord, InvestmentValuationRecord


class FinanceStorageWriters:
    def _save_funds(self, session: Session, funds) -> None:
        for fund in funds:
            session.merge(
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

    def _save_investment_events(self, session: Session, events) -> None:
        for event in events:
            session.merge(
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

    def _save_investment_valuations(self, session: Session, valuations) -> None:
        for valuation in valuations:
            session.merge(
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
