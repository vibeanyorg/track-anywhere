from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
from typing import Any

from .commands import (
    CreateFundCommand,
    FundAllocationCommand,
    FundSpendCommand,
    ReconciliationActionCommand,
    RecordInvestmentEventCommand,
)
from .errors import ValidationError
from .investments import InvestmentEvent, investment_performance_report
from .ledger import Posting


class FinancialUseCases:
    def record_investment_event(self, token: str, payload: dict[str, Any], *, idempotency_key: str) -> tuple[InvestmentEvent, bool]:
        actor = self.actor_from_token(token, "investment:write")
        command = RecordInvestmentEventCommand.model_validate(payload)
        account = self.ledger.get_account(command.account_id)
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
        self.actor_from_token(token, "investment:read")
        if account_id is not None:
            self.ledger.get_account(account_id)
        return self.investments.list(account_id)

    def investment_performance(self, token: str, account_id: str, *, as_of: str | None = None):
        self.actor_from_token(token, "investment:read")
        account = self.ledger.get_account(account_id)
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
        actor = self.actor_from_token(token, "budget:write")
        command = CreateFundCommand.model_validate(payload)
        request_hash = self._hash_command(command)

        def run():
            account = self.ledger.create_account(
                command.name,
                "fund",
                command.currency,
                institution_type="system",
                subtype="fund",
                institution="track-anywhere",
            )
            fund = self.budgets.create(name=command.name, account_id=account.account_id, currency=command.currency)
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
        actor = self.actor_from_token(token, "budget:write")
        command = FundAllocationCommand.model_validate(payload)
        request_hash = self._hash_command(command)

        def run():
            fund = self.budgets.require_current(command.fund_id, command.expected_version)
            transaction = self.ledger.create_transaction(
                command.memo or f"Allocate to {fund.name}",
                [
                    Posting(command.source_account_id, -command.amount, command.currency),
                    Posting(fund.account_id, command.amount, command.currency),
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
        actor = self.actor_from_token(token, "budget:write")
        command = FundSpendCommand.model_validate(payload)
        request_hash = self._hash_command(command)

        def run():
            fund = self.budgets.require_current(command.fund_id, command.expected_version)
            transaction = self.ledger.create_transaction(
                command.memo or f"Spend from {fund.name}",
                [
                    Posting(fund.account_id, -command.amount, command.currency),
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
