from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from .books import DEFAULT_BOOK_ID
from .commands import RecordInvestmentEventCommand
from .domain_commands import RecordInvestmentValuationCommand
from .errors import NotFound, ValidationError
from .investments import InvestmentEvent, InvestmentValuation, investment_performance_report
from .ledger import Posting


class InvestmentUseCases:
    def record_investment_event(self, token: str, payload: dict[str, Any], *, idempotency_key: str) -> tuple[InvestmentEvent, bool]:
        command = RecordInvestmentEventCommand.model_validate(payload)
        account = self.ledger.get_account(command.account_id)
        actor = self.actor_for_book(token, account.book_id, "investment:write")
        if account.type != "asset":
            raise ValidationError("investment events can only be recorded against asset accounts")
        if account.currency != command.currency:
            raise ValidationError("investment event currency must match account currency")
        self.assets.validate_amount(command.currency, command.amount)
        if command.transaction_id is not None and command.cash_account_id is not None:
            raise ValidationError("transaction_id and cash_account_id cannot both be provided")
        if command.transaction_id is not None:
            linked = self.ledger.transactions.get(command.transaction_id)
            if linked is None:
                raise NotFound(f"transaction not found: {command.transaction_id}")
            if linked.book_id != account.book_id:
                raise ValidationError("investment event transaction must belong to the account book")
        if command.cash_account_id is not None:
            cash_account = self.ledger.get_account(command.cash_account_id)
            if cash_account.book_id != account.book_id:
                raise ValidationError("investment cash account must belong to the investment account book")
            if cash_account.currency != command.currency:
                raise ValidationError("investment cash account currency must match event currency")
            if cash_account.type != "asset":
                raise ValidationError("investment cash account must be an asset account")
        request_hash = self._hash_command(command)

        def run():
            linked_transaction_id = command.transaction_id
            if command.cash_account_id is not None:
                linked_transaction_id = self._post_investment_event_transaction(command, account.book_id)
            event = self.investments.record(
                book_id=account.book_id,
                account_id=command.account_id,
                event_type=command.event_type,
                amount=command.amount,
                currency=command.currency,
                occurred_at=command.occurred_at,
                memo=command.memo,
                units=command.units,
                nav=command.nav,
                transaction_id=linked_transaction_id,
            )
            self.audit.record(operation="investment.event.record", actor=actor, entity_ref=event.event_id, details=command.model_dump(mode="json"))
            return event

        event, replay = self.idempotency.run(key=idempotency_key, actor=actor, operation="investment.event.record", request_hash=request_hash, fn=run)
        if replay:
            self._persist_idempotency()
        else:
            transactions = ()
            if event.transaction_id in self.ledger.transactions:
                transactions = (self.ledger.transactions[event.transaction_id],)
            self._persist_investment_change(events=(event,), transactions=transactions)
        return event, replay

    def _post_investment_event_transaction(self, command: RecordInvestmentEventCommand, book_id: str) -> str:
        assert command.cash_account_id is not None
        if command.event_type in {"buy", "add"}:
            postings = [
                Posting(command.cash_account_id, -command.amount, command.currency),
                Posting(command.account_id, command.amount, command.currency),
            ]
            line_type = "investment_buy"
        elif command.event_type == "sell":
            postings = [
                Posting(command.account_id, -command.amount, command.currency),
                Posting(command.cash_account_id, command.amount, command.currency),
            ]
            line_type = "investment_sell"
        elif command.event_type == "income":
            income_account_id = self._system_category_account_id("income", command.currency, book_id=book_id)
            postings = [
                Posting(income_account_id, -command.amount, command.currency),
                Posting(command.cash_account_id, command.amount, command.currency),
            ]
            line_type = "dividend"
        else:
            raise ValidationError("unsupported investment event type")
        transaction = self.ledger.create_transaction(
            memo=command.memo,
            occurred_at=command.occurred_at,
            purpose=f"investment_{command.event_type}",
            postings=postings,
            book_id=book_id,
        )
        self.ledger.add_line(
            transaction,
            line_type=line_type,
            amount=command.amount,
            currency=command.currency,
            memo=command.memo,
        )
        return transaction.transaction_id

    def list_investment_events(self, token: str, account_id: str | None = None) -> list[InvestmentEvent]:
        if account_id is not None:
            account = self.ledger.get_account(account_id)
            self.actor_for_book(token, account.book_id, "investment:read")
            return self.investments.list(account_id, book_id=account.book_id)
        self.actor_for_book(token, DEFAULT_BOOK_ID, "investment:read")
        return self.investments.list(book_id=DEFAULT_BOOK_ID)

    def record_investment_valuation(self, token: str, payload: dict[str, Any], *, idempotency_key: str) -> tuple[InvestmentValuation, bool]:
        command = RecordInvestmentValuationCommand.model_validate(payload)
        account = self.ledger.get_account(command.account_id)
        actor = self.actor_for_book(token, account.book_id, "investment:write")
        if account.type != "asset":
            raise ValidationError("investment valuations can only be recorded against asset accounts")
        if account.currency != command.currency:
            raise ValidationError("investment valuation currency must match account currency")
        self.assets.validate_amount(command.currency, command.value, field_name="valuation value")
        request_hash = self._hash_command(command)

        def run():
            valuation = self.investments.record_valuation(
                book_id=account.book_id,
                account_id=command.account_id,
                value=command.value,
                currency=command.currency,
                observed_at=command.observed_at,
                source=command.source,
                memo=command.memo,
            )
            self.audit.record(operation="investment.valuation.record", actor=actor, entity_ref=valuation.valuation_id, details=command.model_dump(mode="json"))
            return valuation

        valuation, replay = self.idempotency.run(key=idempotency_key, actor=actor, operation="investment.valuation.record", request_hash=request_hash, fn=run)
        if replay:
            self._persist_idempotency()
        else:
            self._persist_investment_change(valuations=(valuation,))
        return valuation, replay

    def list_investment_valuations(self, token: str, account_id: str) -> list[InvestmentValuation]:
        account = self.ledger.get_account(account_id)
        self.actor_for_book(token, account.book_id, "investment:read")
        return self.investments.list_valuations(account_id, book_id=account.book_id)

    def investment_performance(self, token: str, account_id: str, *, as_of: str | None = None):
        account = self.ledger.get_account(account_id)
        self.actor_for_book(token, account.book_id, "investment:read")
        try:
            as_of_datetime = datetime.fromisoformat(as_of) if as_of is not None else None
        except ValueError as exc:
            raise ValidationError("as_of must be an ISO-8601 datetime") from exc
        events = self.investments.list(account_id, book_id=account.book_id)
        if as_of_datetime is None:
            as_of_datetime = max((event.occurred_at for event in events), default=None)
        if as_of_datetime is None:
            as_of_datetime = max((transaction.occurred_at for transaction in self.ledger.transactions.values()), default=None)
        if as_of_datetime is None:
            as_of_datetime = datetime.now(timezone.utc)
        valuation = self.investments.latest_valuation(account_id, book_id=account.book_id, as_of=as_of_datetime)
        if valuation is not None:
            current_value, current_value_source, valuation_id = valuation.value, "valuation_snapshot", valuation.valuation_id
        else:
            current_value, current_value_source, valuation_id = self.ledger.balance(account_id).get(account.currency, Decimal("0")), "account_balance", None
        return investment_performance_report(
            account_id=account_id,
            currency=account.currency,
            current_value=current_value,
            events=events,
            as_of=as_of_datetime,
            current_value_source=current_value_source,
            valuation_id=valuation_id,
        )
