from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4


CONTRIBUTION_EVENT_TYPES = {"buy", "add"}
INFLOW_EVENT_TYPES = {"sell", "income"}
INVESTMENT_EVENT_TYPES = CONTRIBUTION_EVENT_TYPES | INFLOW_EVENT_TYPES


@dataclass
class InvestmentEvent:
    event_id: str
    book_id: str
    account_id: str
    event_type: str
    amount: Decimal
    currency: str
    occurred_at: datetime
    memo: str = ""
    units: Decimal | None = None
    nav: Decimal | None = None
    transaction_id: str | None = None
    version: int = 1


@dataclass
class InvestmentValuation:
    valuation_id: str
    book_id: str
    account_id: str
    value: Decimal
    currency: str
    observed_at: datetime
    source: str = "manual"
    memo: str = ""
    version: int = 1


class InvestmentBook:
    def __init__(self) -> None:
        self.events: dict[str, InvestmentEvent] = {}
        self.valuations: dict[str, InvestmentValuation] = {}

    def record(
        self,
        *,
        book_id: str,
        account_id: str,
        event_type: str,
        amount: Decimal,
        currency: str,
        occurred_at: datetime,
        memo: str = "",
        units: Decimal | None = None,
        nav: Decimal | None = None,
        transaction_id: str | None = None,
    ) -> InvestmentEvent:
        event = InvestmentEvent(
            event_id=f"inv_{uuid4().hex}",
            book_id=book_id,
            account_id=account_id,
            event_type=event_type,
            amount=amount,
            currency=currency,
            occurred_at=occurred_at,
            memo=memo,
            units=units,
            nav=nav,
            transaction_id=transaction_id,
        )
        self.events[event.event_id] = event
        return event

    def list(self, account_id: str | None = None, *, book_id: str | None = None) -> list[InvestmentEvent]:
        events = list(self.events.values())
        if account_id is not None:
            events = [event for event in events if event.account_id == account_id]
        if book_id is not None:
            events = [event for event in events if event.book_id == book_id]
        return sorted(events, key=lambda event: (_time_key(event.occurred_at), event.event_id))

    def record_valuation(
        self,
        *,
        book_id: str,
        account_id: str,
        value: Decimal,
        currency: str,
        observed_at: datetime,
        source: str = "manual",
        memo: str = "",
    ) -> InvestmentValuation:
        valuation = InvestmentValuation(
            valuation_id=f"val_{uuid4().hex}",
            book_id=book_id,
            account_id=account_id,
            value=value,
            currency=currency,
            observed_at=observed_at,
            source=source,
            memo=memo,
        )
        self.valuations[valuation.valuation_id] = valuation
        return valuation

    def list_valuations(self, account_id: str | None = None, *, book_id: str | None = None) -> list[InvestmentValuation]:
        valuations = list(self.valuations.values())
        if account_id is not None:
            valuations = [valuation for valuation in valuations if valuation.account_id == account_id]
        if book_id is not None:
            valuations = [valuation for valuation in valuations if valuation.book_id == book_id]
        return sorted(valuations, key=lambda valuation: (_time_key(valuation.observed_at), valuation.valuation_id))

    def latest_valuation(self, account_id: str, *, book_id: str, as_of: datetime) -> InvestmentValuation | None:
        candidates = [
            valuation
            for valuation in self.list_valuations(account_id, book_id=book_id)
            if _time_key(valuation.observed_at) <= _time_key(as_of)
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda valuation: (_time_key(valuation.observed_at), valuation.valuation_id))


def investment_performance_report(
    *,
    account_id: str,
    currency: str,
    current_value: Decimal,
    events: list[InvestmentEvent],
    as_of: datetime,
    current_value_source: str = "account_balance",
    valuation_id: str | None = None,
) -> dict[str, object]:
    relevant = [event for event in events if _time_key(event.occurred_at) <= _time_key(as_of)]
    contributions = sum((event.amount for event in relevant if event.event_type in CONTRIBUTION_EVENT_TYPES), Decimal("0"))
    withdrawals = sum((event.amount for event in relevant if event.event_type == "sell"), Decimal("0"))
    income = sum((event.amount for event in relevant if event.event_type == "income"), Decimal("0"))
    net_contributed = contributions - withdrawals
    total_return = current_value + withdrawals + income - contributions

    first_contribution = min(
        (event.occurred_at for event in relevant if event.event_type in CONTRIBUTION_EVENT_TYPES),
        key=_time_key,
        default=None,
    )
    holding_days = None
    if first_contribution is not None:
        holding_days = max((_time_key(as_of) - _time_key(first_contribution)).days, 0)

    cash_flows: list[tuple[datetime, Decimal, str]] = []
    for event in relevant:
        if event.event_type in CONTRIBUTION_EVENT_TYPES:
            cash_flows.append((event.occurred_at, -event.amount, event.event_type))
        elif event.event_type in INFLOW_EVENT_TYPES:
            cash_flows.append((event.occurred_at, event.amount, event.event_type))
    cash_flows.append((as_of, current_value, "current_value"))
    cash_flows.sort(key=lambda item: (_time_key(item[0]), item[2], item[1]))

    annualized = _xirr([(date, amount) for date, amount, _ in cash_flows])
    return {
        "account_id": account_id,
        "currency": currency,
        "as_of": as_of.isoformat(),
        "current_value": str(current_value),
        "current_value_source": current_value_source,
        "valuation_id": valuation_id,
        "contributions": str(contributions),
        "withdrawals": str(withdrawals),
        "income": str(income),
        "net_contributed": str(net_contributed),
        "total_return": str(total_return),
        "first_invested_at": first_contribution.isoformat() if first_contribution is not None else None,
        "holding_days": holding_days,
        "event_count": len(relevant),
        "cash_flows": [
            {"date": date.isoformat(), "amount": str(amount), "event_type": event_type}
            for date, amount, event_type in cash_flows
        ],
        "money_weighted_annualized_return": str(annualized) if annualized is not None else None,
        "money_weighted_annualized_return_percent": str(annualized * Decimal("100")) if annualized is not None else None,
        "method": "xirr_with_explicit_valuation_or_account_balance",
    }


def _xirr(cash_flows: list[tuple[datetime, Decimal]]) -> Decimal | None:
    if not cash_flows:
        return None
    amounts = [amount for _, amount in cash_flows]
    if not any(amount < 0 for amount in amounts) or not any(amount > 0 for amount in amounts):
        return None

    first_date = min((date for date, _ in cash_flows), key=_time_key)
    if _time_key(max((date for date, _ in cash_flows), key=_time_key)) == _time_key(first_date):
        return None

    def npv(rate: float) -> float:
        total = 0.0
        for date, amount in cash_flows:
            years = (_time_key(date) - _time_key(first_date)).days / 365.0
            total += float(amount) / ((1.0 + rate) ** years)
        return total

    low = -0.999999
    high = 1.0
    low_value = npv(low)
    high_value = npv(high)
    for _ in range(64):
        if low_value == 0:
            return _decimal_rate(low)
        if high_value == 0:
            return _decimal_rate(high)
        if low_value * high_value < 0:
            break
        high = high * 2 + 1
        high_value = npv(high)
    else:
        return None

    for _ in range(100):
        mid = (low + high) / 2
        mid_value = npv(mid)
        if abs(mid_value) < 1e-8:
            return _decimal_rate(mid)
        if low_value * mid_value <= 0:
            high = mid
            high_value = mid_value
        else:
            low = mid
            low_value = mid_value
    return _decimal_rate((low + high) / 2)


def _decimal_rate(rate: float) -> Decimal:
    return Decimal(str(rate)).quantize(Decimal("0.0000000001"))


def _time_key(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)
