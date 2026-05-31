from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from .category_models import Category
from .domain_commands import RecordPaymentProfileExpenseCommand
from .errors import ValidationError
from .ledger import Account, Posting, Transaction
from .payment_profiles import PaymentProfile
from .transaction_builder import add_transaction_line, build_transaction


@dataclass(frozen=True)
class PaymentProfileExpenseContext:
    profile: PaymentProfile
    instrument_account: Account
    backing_account: Account
    category: Category
    backing_amount: Decimal


class PaymentProfileExpenseUseCases:
    def record_payment_profile_expense(
        self,
        token: str,
        payload: dict[str, Any],
        *,
        idempotency_key: str,
    ) -> tuple[Transaction, bool]:
        command = RecordPaymentProfileExpenseCommand.model_validate(payload)
        context = self._payment_profile_expense_context(command)
        actor = self.actor_for_book(token, context.profile.book_id, "ledger:confirm")
        request_hash = self._hash_command(command)
        created_accounts: list[Account] = []

        def create_expense_transaction() -> Transaction:
            return self._create_payment_profile_expense_transaction(command, context, actor, created_accounts=created_accounts)

        transaction, replay = self.idempotency.run(
            key=idempotency_key,
            actor=actor,
            operation="payment_profile.expense.record",
            request_hash=request_hash,
            fn=create_expense_transaction,
        )
        self._commit_replay_or(replay, lambda: self._commit_ledger_change(transaction, accounts=created_accounts))
        return transaction, replay

    def _payment_profile_expense_context(self, command: RecordPaymentProfileExpenseCommand) -> PaymentProfileExpenseContext:
        profile = self._resolve_payment_profile_reference(command.payment)
        instrument_account = self._get_account_from_storage(profile.instrument_account_id)
        backing_account = self._get_account_from_storage(profile.backing_account_id)
        self._validate_payment_profile_expense_accounts(command, profile, instrument_account, backing_account)

        category = self._get_category_from_storage(command.category_id)
        if category.book_id != profile.book_id:
            raise ValidationError("expense category must belong to the payment profile book")
        if category.kind != "expense":
            raise ValidationError("payment profile expense requires an expense category")

        backing_amount = command.amount * profile.settlement_rate
        self.assets.validate_amount(command.currency, command.amount)
        self.assets.validate_amount(profile.backing_currency, backing_amount, field_name="backing amount")
        return PaymentProfileExpenseContext(
            profile=profile,
            instrument_account=instrument_account,
            backing_account=backing_account,
            category=category,
            backing_amount=backing_amount,
        )

    def _validate_payment_profile_expense_accounts(
        self,
        command: RecordPaymentProfileExpenseCommand,
        profile: PaymentProfile,
        instrument_account: Account,
        backing_account: Account,
    ) -> None:
        if profile.kind != "token_backed_card" or profile.settlement_mode != "immediate":
            raise ValidationError("payment profile is not an immediate token-backed card")
        if profile.book_id != instrument_account.book_id or profile.book_id != backing_account.book_id:
            raise ValidationError("payment profile accounts must belong to the profile book")
        if command.currency != profile.instrument_currency or command.currency != instrument_account.currency:
            raise ValidationError("payment profile expense currency must match instrument currency")
        if backing_account.currency != profile.backing_currency:
            raise ValidationError("payment profile backing currency must match backing account currency")

    def _create_payment_profile_expense_transaction(
        self,
        command: RecordPaymentProfileExpenseCommand,
        context: PaymentProfileExpenseContext,
        actor: Any,
        *,
        created_accounts: list[Account],
    ) -> Transaction:
        profile = context.profile
        counterparty = self._resolve_counterparty_for_write(command.counterparty, book_id=profile.book_id)
        self._ensure_sufficient_backing_balance(profile.backing_account_id, profile.backing_currency, context.backing_amount)

        expense_account = self._system_category_account("expense", profile.instrument_currency, book_id=profile.book_id, created_accounts=created_accounts)
        fx_backing_account = self._system_fx_clearing_account(profile.backing_currency, book_id=profile.book_id, created_accounts=created_accounts)
        fx_instrument_account = self._system_fx_clearing_account(profile.instrument_currency, book_id=profile.book_id, created_accounts=created_accounts)
        transaction = self._build_payment_profile_expense_transaction(
            command,
            context,
            expense_account=expense_account,
            fx_backing_account=fx_backing_account,
            fx_instrument_account=fx_instrument_account,
        )
        self._add_category_line_for_transaction(
            transaction,
            context.category,
            accounts=(context.instrument_account, expense_account, context.backing_account, fx_backing_account, fx_instrument_account),
            counterparty_id=counterparty.counterparty_id if counterparty else None,
        )
        add_transaction_line(
            transaction,
            line_type="fx_exchange",
            amount=context.backing_amount,
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
                "backing_amount": str(context.backing_amount),
            },
        )
        return transaction

    def _ensure_sufficient_backing_balance(self, backing_account_id: str, backing_currency: str, backing_amount: Decimal) -> None:
        confirmed_backing_balance = self._account_balance_from_storage(backing_account_id).get(backing_currency, Decimal("0"))
        if confirmed_backing_balance < backing_amount:
            raise ValidationError("insufficient backing balance")

    def _build_payment_profile_expense_transaction(
        self,
        command: RecordPaymentProfileExpenseCommand,
        context: PaymentProfileExpenseContext,
        *,
        expense_account: Account,
        fx_backing_account: Account,
        fx_instrument_account: Account,
    ) -> Transaction:
        profile = context.profile
        return build_transaction(
            memo=command.memo,
            occurred_at=command.occurred_at,
            purpose=command.purpose,
            postings=[
                Posting(profile.instrument_account_id, -command.amount, profile.instrument_currency),
                Posting(expense_account.account_id, command.amount, profile.instrument_currency),
                Posting(profile.backing_account_id, -context.backing_amount, profile.backing_currency),
                Posting(fx_backing_account.account_id, context.backing_amount, profile.backing_currency),
                Posting(profile.instrument_account_id, command.amount, profile.instrument_currency),
                Posting(fx_instrument_account.account_id, -command.amount, profile.instrument_currency),
            ],
            book_id=profile.book_id,
            accounts=[context.instrument_account, expense_account, context.backing_account, fx_backing_account, fx_instrument_account],
            scale_lookup=self.assets.scale_for,
        )
