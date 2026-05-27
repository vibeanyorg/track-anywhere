from __future__ import annotations

from dataclasses import asdict
from hashlib import sha256
from typing import Any

from . import commands
from .books import DEFAULT_BOOK_ID
from .errors import NotFound, ValidationError
from .service_payment_profiles import PaymentProfileUseCases
from .ledger import Posting
from .transaction_builder import build_transaction


class FinancialUseCases(PaymentProfileUseCases):
    def create_fund(self, token: str, payload: dict[str, Any], *, idempotency_key: str):
        actor = self.actor_for_book(token, DEFAULT_BOOK_ID, "budget:write")
        command = commands.CreateFundCommand.model_validate(payload)
        self.assets.ensure(command.currency)
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

        fund, replay = self.idempotency.run(
            key=idempotency_key,
            actor=actor,
            operation="fund.create",
            request_hash=request_hash,
            fn=run,
        )
        self._persist_replay_or(replay, lambda: self._persist_finance_change(funds=(fund,)))
        return fund, replay

    def allocate_fund(self, token: str, payload: dict[str, Any], *, idempotency_key: str):
        command = commands.FundAllocationCommand.model_validate(payload)
        fund = self.budgets.get(command.fund_id)
        if fund is None:
            raise NotFound(f"fund not found: {command.fund_id}")
        actor = self.actor_for_book(token, fund.book_id, "budget:write")
        source = self.storage.get_account(command.source_account_id)
        if source.book_id != fund.book_id:
            raise ValidationError("fund allocation account must belong to the fund book")
        if source.currency != command.currency or fund.currency != command.currency:
            raise ValidationError("fund allocation currency must match source and fund currencies")
        self.assets.validate_amount(command.currency, command.amount)
        request_hash = self._hash_command(command)

        def run():
            current_fund = self.budgets.require_current(command.fund_id, command.expected_version)
            fund_account = self.storage.get_account(current_fund.account_id)
            transaction = build_transaction(
                memo=command.memo,
                purpose="fund_allocation",
                postings=[
                    Posting(command.source_account_id, -command.amount, command.currency),
                    Posting(current_fund.account_id, command.amount, command.currency),
                ],
                accounts=[source, fund_account],
                book_id=fund.book_id,
                scale_lookup=self.assets.scale_for,
            )
            updated = self.budgets.allocate(command.fund_id, command.expected_version, command.amount, transaction.transaction_id)
            self.audit.record(
                operation="fund.allocate",
                actor=actor,
                entity_ref=updated.fund_id,
                details={"transaction_id": transaction.transaction_id, "amount": str(command.amount)},
            )
            return {"fund": updated, "transaction": transaction}

        result, replay = self.idempotency.run(
            key=idempotency_key,
            actor=actor,
            operation="fund.allocate",
            request_hash=request_hash,
            fn=run,
        )
        self._persist_replay_or(replay, lambda: self._persist_finance_change(funds=(result["fund"],), transactions=(result["transaction"],)))
        return result, replay

    def spend_fund(self, token: str, payload: dict[str, Any], *, idempotency_key: str):
        command = commands.FundSpendCommand.model_validate(payload)
        fund = self.budgets.get(command.fund_id)
        if fund is None:
            raise NotFound(f"fund not found: {command.fund_id}")
        actor = self.actor_for_book(token, fund.book_id, "budget:write")
        expense = self.storage.get_account(command.expense_account_id)
        if expense.book_id != fund.book_id:
            raise ValidationError("fund spend account must belong to the fund book")
        if expense.currency != command.currency or fund.currency != command.currency:
            raise ValidationError("fund spend currency must match expense and fund currencies")
        self.assets.validate_amount(command.currency, command.amount)
        request_hash = self._hash_command(command)

        def run():
            current_fund = self.budgets.require_current(command.fund_id, command.expected_version)
            fund_account = self.storage.get_account(current_fund.account_id)
            transaction = build_transaction(
                memo=command.memo,
                purpose="fund_spend",
                postings=[
                    Posting(current_fund.account_id, -command.amount, command.currency),
                    Posting(command.expense_account_id, command.amount, command.currency),
                ],
                accounts=[fund_account, expense],
                book_id=fund.book_id,
                scale_lookup=self.assets.scale_for,
            )
            updated = self.budgets.spend(command.fund_id, command.expected_version, command.amount, transaction.transaction_id)
            self.audit.record(
                operation="fund.spend",
                actor=actor,
                entity_ref=updated.fund_id,
                details={"transaction_id": transaction.transaction_id, "amount": str(command.amount)},
            )
            return {"fund": updated, "transaction": transaction}

        result, replay = self.idempotency.run(
            key=idempotency_key,
            actor=actor,
            operation="fund.spend",
            request_hash=request_hash,
            fn=run,
        )
        self._persist_replay_or(replay, lambda: self._persist_finance_change(funds=(result["fund"],), transactions=(result["transaction"],)))
        return result, replay

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

        result, replay = self.idempotency.run(
            key=idempotency_key,
            actor=actor,
            operation="attachment.upload",
            request_hash=request_hash,
            fn=run,
        )
        self._persist_replay_or(replay, lambda: self._persist_attachment_change(attachments=(result["attachment"],), drafts=(result["draft"],)))
        return result, replay

    def record_reconciliation_action(self, token: str, payload: dict[str, Any], *, idempotency_key: str):
        actor = self.actor_from_token(token, "ledger:confirm")
        command = commands.ReconciliationActionCommand.model_validate(payload)
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

        action, replay = self.idempotency.run(
            key=idempotency_key,
            actor=actor,
            operation="reconciliation.record",
            request_hash=request_hash,
            fn=run,
        )
        self._persist_replay_or(replay, lambda: self._persist_finance_change(actions=(action,)))
        return action, replay
