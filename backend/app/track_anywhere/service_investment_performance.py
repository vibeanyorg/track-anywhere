from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from .errors import ValidationError
from .investments import investment_performance_report


class InvestmentPerformanceUseCases:
    def investment_performance(self, token: str, account_id: str, *, as_of: str | None = None):
        account = self.storage.get_account(account_id)
        self.actor_for_book(token, account.book_id, "investment:read")
        try:
            as_of_datetime = datetime.fromisoformat(as_of) if as_of is not None else None
        except ValueError as exc:
            raise ValidationError("as_of must be an ISO-8601 datetime") from exc
        events = self.investments.list(account_id, book_id=account.book_id)
        if as_of_datetime is None:
            as_of_datetime = max((event.occurred_at for event in events), default=None)
        if as_of_datetime is None:
            latest = self.storage.list_confirmed_transactions(book_id=account.book_id, account_id=account_id, limit=1)
            as_of_datetime = latest[0].occurred_at if latest else None
        if as_of_datetime is None:
            as_of_datetime = datetime.now(timezone.utc)
        valuation = self.investments.latest_valuation(account_id, book_id=account.book_id, as_of=as_of_datetime)
        if valuation is not None:
            current_value, current_value_source, valuation_id = valuation.value, "valuation_snapshot", valuation.valuation_id
        else:
            current_value = self.storage.account_balance(account_id).get(account.currency, Decimal("0"))
            current_value_source, valuation_id = "account_balance", None
        return investment_performance_report(
            account_id=account_id,
            currency=account.currency,
            current_value=current_value,
            events=events,
            as_of=as_of_datetime,
            current_value_source=current_value_source,
            valuation_id=valuation_id,
        )
