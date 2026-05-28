from __future__ import annotations

from typing import Any

from .books import DEFAULT_BOOK_ID
from .commands import RecordInvestmentEventCommand
from .errors import NotFound, ValidationError
from .investments import InvestmentEvent
from .ledger import Posting
from .transaction_builder import add_transaction_line, build_transaction


class InvestmentEventUseCases:
    def record_investment_event(self, token: str, payload: dict[str, Any], *, idempotency_key: str) -> tuple[InvestmentEvent, bool]:
        command = RecordInvestmentEventCommand.model_validate(payload)
        account = self._get_account_from_storage(command.account_id)
        actor = self.actor_for_book(token, account.book_id, "investment:write")
        if account.type != "asset":
            raise ValidationError("investment events can only be recorded against asset accounts")
        if account.currency != command.currency:
            raise ValidationError("investment event currency must match account currency")
        self.assets.validate_amount(command.currency, command.amount)
        if command.transaction_id is not None and command.cash_account_id is not None:
            raise ValidationError("transaction_id and cash_account_id cannot both be provided")
        if command.transaction_id is not None:
            linked = self._get_transaction_from_storage(command.transaction_id)
            if linked is None:
                raise NotFound(f"transaction not found: {command.transaction_id}")
            if linked.book_id != account.book_id:
                raise ValidationError("investment event transaction must belong to the account book")
        if command.cash_account_id is not None:
            cash_account = self._get_account_from_storage(command.cash_account_id)
            if cash_account.book_id != account.book_id:
                raise ValidationError("investment cash account must belong to the investment account book")
            if cash_account.currency != command.currency:
                raise ValidationError("investment cash account currency must match event currency")
            if cash_account.type != "asset":
                raise ValidationError("investment cash account must be an asset account")
        request_hash = self._hash_command(command)
        created_transaction = None
        created_accounts = []

        def run():
            nonlocal created_transaction
            linked_transaction_id = command.transaction_id
            if command.cash_account_id is not None:
                created_transaction = self._post_investment_event_transaction(
                    command,
                    account.book_id,
                    created_accounts=created_accounts,
                )
                linked_transaction_id = created_transaction.transaction_id
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
        self._commit_replay_or(
            replay,
            lambda: self._commit_investment_change(
                events=(event,),
                transactions=(created_transaction,) if created_transaction is not None else (),
                accounts=created_accounts,
            ),
        )
        return event, replay

    def _post_investment_event_transaction(self, command: RecordInvestmentEventCommand, book_id: str, *, created_accounts):
        assert command.cash_account_id is not None
        cash_account = self._get_account_from_storage(command.cash_account_id)
        investment_account = self._get_account_from_storage(command.account_id)
        if command.event_type in {"buy", "add"}:
            postings = [
                Posting(command.cash_account_id, -command.amount, command.currency),
                Posting(command.account_id, command.amount, command.currency),
            ]
            accounts = [cash_account, investment_account]
            line_type = "investment_buy"
        elif command.event_type == "sell":
            postings = [
                Posting(command.account_id, -command.amount, command.currency),
                Posting(command.cash_account_id, command.amount, command.currency),
            ]
            accounts = [investment_account, cash_account]
            line_type = "investment_sell"
        elif command.event_type == "income":
            income_account = self._system_category_account(
                "income",
                command.currency,
                book_id=book_id,
                created_accounts=created_accounts,
            )
            income_account_id = income_account.account_id
            postings = [
                Posting(income_account_id, -command.amount, command.currency),
                Posting(command.cash_account_id, command.amount, command.currency),
            ]
            accounts = [income_account, cash_account]
            line_type = "dividend"
        else:
            raise ValidationError("unsupported investment event type")
        transaction = build_transaction(
            memo=command.memo,
            occurred_at=command.occurred_at,
            purpose=f"investment_{command.event_type}",
            postings=postings,
            accounts=accounts,
            book_id=book_id,
            scale_lookup=self.assets.scale_for,
        )
        add_transaction_line(
            transaction,
            line_type=line_type,
            amount=command.amount,
            currency=command.currency,
            memo=command.memo,
            scale_lookup=self.assets.scale_for,
        )
        return transaction

    def list_investment_events(self, token: str, account_id: str | None = None) -> list[InvestmentEvent]:
        if account_id is not None:
            account = self._get_account_from_storage(account_id)
            self.actor_for_book(token, account.book_id, "investment:read")
            return self.investments.list(account_id, book_id=account.book_id)
        self.actor_for_book(token, DEFAULT_BOOK_ID, "investment:read")
        return self.investments.list(book_id=DEFAULT_BOOK_ID)
