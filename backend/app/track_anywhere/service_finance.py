from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
from typing import Any

from .books import DEFAULT_BOOK_ID
from .commands import (
    CreateFundCommand,
    FundAllocationCommand,
    FundSpendCommand,
    ReconciliationActionCommand,
    RecordInvestmentEventCommand,
)
from .errors import NotFound, ValidationError
from .investments import InvestmentEvent, investment_performance_report
from .ledger import Posting


class FinancialUseCases:
    def record_investment_event(self, token: str, payload: dict[str, Any], *, idempotency_key: str) -> tuple[InvestmentEvent, bool]:
        command = RecordInvestmentEventCommand.model_validate(payload)
        account = self.ledger.get_account(command.account_id)
        actor = self.actor_for_book(token, account.book_id, "investment:write")
        if account.type != "asset":
            raise ValidationError("investment events can only be recorded against asset accounts")
        if account.currency != command.currency:
            raise ValidationError("investment event currency must match account currency")
        request_hash = self._hash_command(command)

        def run():
            event = self.investments.record(
                account_id=command.account_id,
                event_type=command.event_type,
                amount=command.amount,
                currency=command.currency,
                occurred_at=command.occurred_at,
                memo=command.memo,
                units=command.units,
                nav=command.nav,
            )
            self.audit.record(
                operation="investment.event.record",
                actor=actor,
                entity_ref=event.event_id,
                details=command.model_dump(mode="json"),
            )
            return event

        result = self.idempotency.run(
            key=idempotency_key,
            actor=actor,
            operation="investment.event.record",
            request_hash=request_hash,
            fn=run,
        )
        self._persist()
        return result

    def list_investment_events(self, token: str, account_id: str | None = None) -> list[InvestmentEvent]:
        if account_id is not None:
            account = self.ledger.get_account(account_id)
            self.actor_for_book(token, account.book_id, "investment:read")
            return self.investments.list(account_id)
        self.actor_for_book(token, DEFAULT_BOOK_ID, "investment:read")
        return [
            event
            for event in self.investments.list(account_id)
            if self.ledger.accounts.get(event.account_id) is not None
            and self.ledger.accounts[event.account_id].book_id == DEFAULT_BOOK_ID
        ]

    def investment_performance(self, token: str, account_id: str, *, as_of: str | None = None):
        account = self.ledger.get_account(account_id)
        self.actor_for_book(token, account.book_id, "investment:read")
        try:
            as_of_datetime = datetime.fromisoformat(as_of) if as_of is not None else None
        except ValueError as exc:
            raise ValidationError("as_of must be an ISO-8601 datetime") from exc
        if as_of_datetime is None:
            as_of_datetime = max(
                (event.occurred_at for event in self.investments.list(account_id)),
                default=None,
            )
        if as_of_datetime is None:
            as_of_datetime = max(
                (transaction.occurred_at for transaction in self.ledger.transactions.values()),
                default=None,
            )
        if as_of_datetime is None:
            as_of_datetime = datetime.now(timezone.utc)
        current_value = self.ledger.balance(account_id).get(account.currency, Decimal("0"))
        return investment_performance_report(
            account_id=account_id,
            currency=account.currency,
            current_value=current_value,
            events=self.investments.list(account_id),
            as_of=as_of_datetime,
        )

    def create_fund(self, token: str, payload: dict[str, Any], *, idempotency_key: str):
        actor = self.actor_for_book(token, DEFAULT_BOOK_ID, "budget:write")
        command = CreateFundCommand.model_validate(payload)
        request_hash = self._hash_command(command)

        def run():
            book_id = self.books.ensure_default().book_id
            account = self.ledger.create_account(
                command.name,
                "fund",
                command.currency,
                institution_type="system",
                subtype="fund",
                institution="track-anywhere",
                book_id=book_id,
            )
            fund = self.budgets.create(name=command.name, account_id=account.account_id, currency=command.currency, book_id=book_id)
            self.audit.record(operation="fund.create", actor=actor, entity_ref=fund.fund_id, details=command.model_dump())
            return fund

        result = self.idempotency.run(
            key=idempotency_key,
            actor=actor,
            operation="fund.create",
            request_hash=request_hash,
            fn=run,
        )
        self._persist()
        return result

    def allocate_fund(self, token: str, payload: dict[str, Any], *, idempotency_key: str):
        command = FundAllocationCommand.model_validate(payload)
        fund = self.budgets.get(command.fund_id)
        if fund is None:
            raise NotFound(f"fund not found: {command.fund_id}")
        actor = self.actor_for_book(token, fund.book_id, "budget:write")
        source = self.ledger.get_account(command.source_account_id)
        if source.book_id != fund.book_id:
            raise ValidationError("fund allocation account must belong to the fund book")
        if source.currency != command.currency or fund.currency != command.currency:
            raise ValidationError("fund allocation currency must match source and fund currencies")
        request_hash = self._hash_command(command)

        def run():
            current_fund = self.budgets.require_current(command.fund_id, command.expected_version)
            transaction = self.ledger.create_transaction(
                command.memo or f"Allocate to {current_fund.name}",
                [
                    Posting(command.source_account_id, -command.amount, command.currency),
                    Posting(current_fund.account_id, command.amount, command.currency),
                ],
            )
            updated = self.budgets.allocate(command.fund_id, command.expected_version, command.amount, transaction.transaction_id)
            self.audit.record(
                operation="fund.allocate",
                actor=actor,
                entity_ref=updated.fund_id,
                details={"transaction_id": transaction.transaction_id, "amount": str(command.amount)},
            )
            return {"fund": updated, "transaction": transaction}

        result = self.idempotency.run(
            key=idempotency_key,
            actor=actor,
            operation="fund.allocate",
            request_hash=request_hash,
            fn=run,
        )
        self._persist()
        return result

    def spend_fund(self, token: str, payload: dict[str, Any], *, idempotency_key: str):
        command = FundSpendCommand.model_validate(payload)
        fund = self.budgets.get(command.fund_id)
        if fund is None:
            raise NotFound(f"fund not found: {command.fund_id}")
        actor = self.actor_for_book(token, fund.book_id, "budget:write")
        expense = self.ledger.get_account(command.expense_account_id)
        if expense.book_id != fund.book_id:
            raise ValidationError("fund spend account must belong to the fund book")
        if expense.currency != command.currency or fund.currency != command.currency:
            raise ValidationError("fund spend currency must match expense and fund currencies")
        request_hash = self._hash_command(command)

        def run():
            current_fund = self.budgets.require_current(command.fund_id, command.expected_version)
            transaction = self.ledger.create_transaction(
                command.memo or f"Spend from {current_fund.name}",
                [
                    Posting(current_fund.account_id, -command.amount, command.currency),
                    Posting(command.expense_account_id, command.amount, command.currency),
                ],
            )
            updated = self.budgets.spend(command.fund_id, command.expected_version, command.amount, transaction.transaction_id)
            self.audit.record(
                operation="fund.spend",
                actor=actor,
                entity_ref=updated.fund_id,
                details={"transaction_id": transaction.transaction_id, "amount": str(command.amount)},
            )
            return {"fund": updated, "transaction": transaction}

        result = self.idempotency.run(
            key=idempotency_key,
            actor=actor,
            operation="fund.spend",
            request_hash=request_hash,
            fn=run,
        )
        self._persist()
        return result

    def upload_attachment(
        self,
        token: str,
        *,
        filename: str,
        mime_type: str,
        content: bytes,
        idempotency_key: str,
    ):
        actor = self.actor_from_token(token, "attachment:write")
        scanner_available = self.config.attachment_scanner_available
        request_hash = sha256(content + filename.encode() + mime_type.encode() + str(scanner_available).encode()).hexdigest()

        def run():
            attachment = self.attachments.ingest(
                filename=filename,
                mime_type=mime_type,
                content=content,
                scanner_available=scanner_available,
            )
            draft = self.drafts.create(
                memo=f"Review attachment {attachment.original_filename}",
                proposed_postings=[],
                missing_fields=["amount", "source_account_id", "expense_account_id"],
                source="ocr",
                confidence=0.0,
                attachment_id=attachment.attachment_id,
            )
            self.audit.record(
                operation="attachment.upload",
                actor=actor,
                entity_ref=attachment.attachment_id,
                details={"attachment": asdict(attachment), "draft_id": draft.draft_id},
            )
            return {"attachment": attachment, "draft": draft}

        result = self.idempotency.run(
            key=idempotency_key,
            actor=actor,
            operation="attachment.upload",
            request_hash=request_hash,
            fn=run,
        )
        self._persist()
        return result

    def record_reconciliation_action(self, token: str, payload: dict[str, Any], *, idempotency_key: str):
        actor = self.actor_from_token(token, "ledger:confirm")
        command = ReconciliationActionCommand.model_validate(payload)
        request_hash = self._hash_command(command)

        def run():
            action = {
                "reconciliation_id": f"recon_{len(self.reconciliation_actions) + 1}",
                "summary": command.summary,
                "version": 1,
            }
            self.reconciliation_actions.append(action)
            self.audit.record(
                operation="reconciliation.record",
                actor=actor,
                entity_ref=action["reconciliation_id"],
                details=action,
            )
            return action

        result = self.idempotency.run(
            key=idempotency_key,
            actor=actor,
            operation="reconciliation.record",
            request_hash=request_hash,
            fn=run,
        )
        self._persist()
        return result
