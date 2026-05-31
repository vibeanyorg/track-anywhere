from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .books import DEFAULT_BOOK_ID
from .commands import RecordInvestmentEventCommand
from .errors import NotFound, ValidationError
from .investments import InvestmentEvent
from .ledger import Account, Posting, Transaction
from .transaction_builder import add_transaction_line, build_transaction


@dataclass(frozen=True)
class InvestmentEventContext:
    investment_account: Account
    linked_transaction_id: str | None = None
    cash_account: Account | None = None


class InvestmentEventUseCases:
    def record_investment_event(self, token: str, payload: dict[str, Any], *, idempotency_key: str) -> tuple[InvestmentEvent, bool]:
        command = RecordInvestmentEventCommand.model_validate(payload)
        context = self._investment_event_context(command)
        actor = self.actor_for_book(token, context.investment_account.book_id, "investment:write")
        request_hash = self._hash_command(command)
        created_transaction: Transaction | None = None
        created_accounts: list[Account] = []

        def create_investment_event() -> InvestmentEvent:
            nonlocal created_transaction
            linked_transaction_id = context.linked_transaction_id
            if context.cash_account is not None:
                created_transaction = self._post_investment_event_transaction(
                    command,
                    context.investment_account,
                    context.cash_account,
                    created_accounts=created_accounts,
                )
                linked_transaction_id = created_transaction.transaction_id
            event = self.investments.record(
                book_id=context.investment_account.book_id,
                account_id=context.investment_account.account_id,
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

        event, replay = self.idempotency.run(
            key=idempotency_key,
            actor=actor,
            operation="investment.event.record",
            request_hash=request_hash,
            fn=create_investment_event,
        )
        self._commit_replay_or(
            replay,
            lambda: self._commit_investment_change(
                events=(event,),
                transactions=(created_transaction,) if created_transaction is not None else (),
                accounts=created_accounts,
            ),
        )
        return event, replay

    def _investment_event_context(self, command: RecordInvestmentEventCommand) -> InvestmentEventContext:
        investment_account = self._get_account_from_storage(command.account_id)
        if investment_account.type != "asset":
            raise ValidationError("investment events can only be recorded against asset accounts")
        if investment_account.currency != command.currency:
            raise ValidationError("investment event currency must match account currency")
        self.assets.validate_amount(command.currency, command.amount)
        if command.transaction_id is not None and command.cash_account_id is not None:
            raise ValidationError("transaction_id and cash_account_id cannot both be provided")
        if command.transaction_id is not None:
            self._validate_linked_investment_transaction(command.transaction_id, book_id=investment_account.book_id)
            return InvestmentEventContext(investment_account=investment_account, linked_transaction_id=command.transaction_id)
        if command.cash_account_id is not None:
            cash_account = self._validated_investment_cash_account(command.cash_account_id, investment_account=investment_account, currency=command.currency)
            return InvestmentEventContext(investment_account=investment_account, cash_account=cash_account)
        return InvestmentEventContext(investment_account=investment_account)

    def _validate_linked_investment_transaction(self, transaction_id: str, *, book_id: str) -> None:
        linked_transaction = self._get_transaction_from_storage(transaction_id)
        if linked_transaction is None:
            raise NotFound(f"transaction not found: {transaction_id}")
        if linked_transaction.book_id != book_id:
            raise ValidationError("investment event transaction must belong to the account book")

    def _validated_investment_cash_account(self, cash_account_id: str, *, investment_account: Account, currency: str) -> Account:
        cash_account = self._get_account_from_storage(cash_account_id)
        if cash_account.book_id != investment_account.book_id:
            raise ValidationError("investment cash account must belong to the investment account book")
        if cash_account.currency != currency:
            raise ValidationError("investment cash account currency must match event currency")
        if cash_account.type != "asset":
            raise ValidationError("investment cash account must be an asset account")
        return cash_account

    def _post_investment_event_transaction(
        self,
        command: RecordInvestmentEventCommand,
        investment_account: Account,
        cash_account: Account,
        *,
        created_accounts: list[Account],
    ) -> Transaction:
        if command.event_type in {"buy", "add"}:
            postings = [
                Posting(cash_account.account_id, -command.amount, command.currency),
                Posting(investment_account.account_id, command.amount, command.currency),
            ]
            accounts = [cash_account, investment_account]
            line_type = "investment_buy"
        elif command.event_type == "sell":
            postings = [
                Posting(investment_account.account_id, -command.amount, command.currency),
                Posting(cash_account.account_id, command.amount, command.currency),
            ]
            accounts = [investment_account, cash_account]
            line_type = "investment_sell"
        elif command.event_type == "income":
            income_account = self._system_category_account("income", command.currency, book_id=investment_account.book_id, created_accounts=created_accounts)
            postings = [
                Posting(income_account.account_id, -command.amount, command.currency),
                Posting(cash_account.account_id, command.amount, command.currency),
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
            book_id=investment_account.book_id,
            scale_lookup=self.assets.scale_for,
        )
        add_transaction_line(transaction, line_type=line_type, amount=command.amount, currency=command.currency, memo=command.memo, scale_lookup=self.assets.scale_for)
        return transaction

    def list_investment_events(self, token: str, account_id: str | None = None) -> list[InvestmentEvent]:
        if account_id is not None:
            account = self._get_account_from_storage(account_id)
            self.actor_for_book(token, account.book_id, "investment:read")
            return self.investments.list(account_id, book_id=account.book_id)
        self.actor_for_book(token, DEFAULT_BOOK_ID, "investment:read")
        return self.investments.list(book_id=DEFAULT_BOOK_ID)
