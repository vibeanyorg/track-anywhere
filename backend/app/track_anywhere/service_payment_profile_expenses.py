from __future__ import annotations

from decimal import Decimal
from typing import Any

from .domain_commands import RecordPaymentProfileExpenseCommand
from .errors import ValidationError
from .ledger import Posting, Transaction
from .transaction_builder import add_transaction_line, build_transaction


class PaymentProfileExpenseUseCases:
    def record_payment_profile_expense(
        self,
        token: str,
        payload: dict[str, Any],
        *,
        idempotency_key: str,
    ) -> tuple[Transaction, bool]:
        command = RecordPaymentProfileExpenseCommand.model_validate(payload)
        profile = self._resolve_payment_profile_reference(command.payment)
        instrument = self.storage.get_account(profile.instrument_account_id)
        backing = self.storage.get_account(profile.backing_account_id)

        if profile.kind != "token_backed_card" or profile.settlement_mode != "immediate":
            raise ValidationError("payment profile is not an immediate token-backed card")
        if profile.book_id != instrument.book_id or profile.book_id != backing.book_id:
            raise ValidationError("payment profile accounts must belong to the profile book")
        if command.currency != profile.instrument_currency or command.currency != instrument.currency:
            raise ValidationError("payment profile expense currency must match instrument currency")
        if backing.currency != profile.backing_currency:
            raise ValidationError("payment profile backing currency must match backing account currency")
        self.assets.validate_amount(command.currency, command.amount)
        backing_amount = command.amount * profile.settlement_rate
        self.assets.validate_amount(profile.backing_currency, backing_amount, field_name="backing amount")

        category = self.storage.get_category(command.category_id)
        if category.book_id != profile.book_id:
            raise ValidationError("expense category must belong to the payment profile book")
        if category.kind != "expense":
            raise ValidationError("payment profile expense requires an expense category")

        actor = self.actor_for_book(token, profile.book_id, "ledger:confirm")
        request_hash = self._hash_command(command)
        created_accounts = []

        def run():
            counterparty = self._resolve_counterparty_for_write(command.counterparty, book_id=profile.book_id)
            confirmed_backing_balance = self.storage.account_balance(profile.backing_account_id).get(
                profile.backing_currency,
                Decimal("0"),
            )
            if confirmed_backing_balance < backing_amount:
                raise ValidationError("insufficient backing balance")

            expense_account = self._system_category_account(
                "expense",
                profile.instrument_currency,
                book_id=profile.book_id,
                created_accounts=created_accounts,
            )
            fx_backing_account = self._system_fx_clearing_account(
                profile.backing_currency,
                book_id=profile.book_id,
                created_accounts=created_accounts,
            )
            fx_instrument_account = self._system_fx_clearing_account(
                profile.instrument_currency,
                book_id=profile.book_id,
                created_accounts=created_accounts,
            )
            expense_account_id = expense_account.account_id
            fx_backing_account_id = fx_backing_account.account_id
            fx_instrument_account_id = fx_instrument_account.account_id
            transaction = build_transaction(
                memo=command.memo,
                occurred_at=command.occurred_at,
                purpose=command.purpose,
                postings=[
                    Posting(profile.instrument_account_id, -command.amount, profile.instrument_currency),
                    Posting(expense_account_id, command.amount, profile.instrument_currency),
                    Posting(profile.backing_account_id, -backing_amount, profile.backing_currency),
                    Posting(fx_backing_account_id, backing_amount, profile.backing_currency),
                    Posting(profile.instrument_account_id, command.amount, profile.instrument_currency),
                    Posting(fx_instrument_account_id, -command.amount, profile.instrument_currency),
                ],
                book_id=profile.book_id,
                accounts=[instrument, expense_account, backing, fx_backing_account, fx_instrument_account],
                scale_lookup=self.assets.scale_for,
            )
            self._add_category_line_for_transaction(
                transaction,
                category,
                accounts=(instrument, expense_account, backing, fx_backing_account, fx_instrument_account),
                counterparty_id=counterparty.counterparty_id if counterparty else None,
            )
            add_transaction_line(
                transaction,
                line_type="fx_exchange",
                amount=backing_amount,
                currency=profile.backing_currency,
                memo=f"{profile.display_name} {profile.backing_currency}-backed card settlement",
                scale_lookup=self.assets.scale_for,
            )
            self.audit.record(
                operation="payment_profile.expense.record",
                actor=actor,
                entity_ref=transaction.transaction_id,
                details={
                    **command.model_dump(mode="json"),
                    "profile_id": profile.profile_id,
                    "instrument_account_id": profile.instrument_account_id,
                    "backing_account_id": profile.backing_account_id,
                    "backing_amount": str(backing_amount),
                },
            )
            return transaction

        transaction, replay = self.idempotency.run(
            key=idempotency_key,
            actor=actor,
            operation="payment_profile.expense.record",
            request_hash=request_hash,
            fn=run,
        )
        self._commit_replay_or(replay, lambda: self._commit_ledger_change(transaction, accounts=created_accounts))
        return transaction, replay
