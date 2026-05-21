from __future__ import annotations

from decimal import Decimal
from typing import Any

from .categories import Category
from .commands import BalanceAdjustmentCommand
from .errors import ValidationError
from .ledger import Posting


class BalanceUseCases:
    def adjust_balance(self, token: str, payload: dict[str, Any], *, idempotency_key: str):
        command = BalanceAdjustmentCommand.model_validate(payload)
        account = self.ledger.get_account(command.account_id)
        actor = self.actor_for_book(token, account.book_id, "ledger:confirm")
        self.assets.validate_amount(command.currency, command.amount)
        request_hash = self._hash_command(command)

        def run():
            if account.currency != command.currency:
                raise ValidationError("balance adjustment currency must match account currency")
            adjustment_account_id = self._system_adjustment_account_id(command.currency, book_id=account.book_id)
            transaction = self.ledger.create_transaction(
                memo=command.memo,
                occurred_at=command.occurred_at,
                purpose=command.purpose,
                postings=[
                    Posting(command.account_id, command.amount, command.currency),
                    Posting(adjustment_account_id, -command.amount, command.currency),
                ],
            )
            self.audit.record(
                operation="ledger.balance.adjust",
                actor=actor,
                entity_ref=command.account_id,
                details={
                    **command.model_dump(mode="json"),
                    "transaction_id": transaction.transaction_id,
                    "offset_account_id": adjustment_account_id,
                },
            )
            return transaction

        result = self.idempotency.run(
            key=idempotency_key,
            actor=actor,
            operation="ledger.balance.adjust",
            request_hash=request_hash,
            fn=run,
        )
        self._persist()
        return result

    def account_balance(self, token: str, account_id: str, *, include_drafts: bool = False) -> dict[str, Any]:
        account = self.ledger.get_account(account_id)
        self.actor_for_book(token, account.book_id, "account:read")
        official = self.ledger.balance(account_id)
        pending = self.drafts.projected_impact(account_id) if include_drafts else {}
        currency = account.currency
        official_amount = official.get(currency, Decimal("0"))
        pending_amount = pending.get(currency, Decimal("0"))
        result = {
            "account_id": account_id,
            "currency": currency,
            "official_balance": {
                "amount": str(official_amount),
                "source": "confirmed_postings",
                "as_of_ledger_version": len(self.ledger.transactions),
            },
            "default_view": "official",
            "provenance": {
                "confirmed_transaction_count": len(self.ledger.transactions),
                "draft_count": len(self.drafts.drafts),
            },
        }
        if include_drafts:
            result["projected_balance"] = {
                "amount": str(official_amount + pending_amount),
                "pending_impact": str(pending_amount),
                "included_draft_ids": [
                    draft.draft_id
                    for draft in self.drafts.drafts.values()
                    if any(posting.account_id == account_id for posting in draft.proposed_postings)
                ],
                "projection_version": len(self.drafts.drafts),
            }
        return result

    def _system_adjustment_account_id(self, currency: str, *, book_id: str | None = None) -> str:
        book_id = book_id or self.books.ensure_default().book_id
        key = f"{book_id}:{currency}"
        account_id = self.adjustment_account_ids.get(key) or self.adjustment_account_ids.get(currency)
        if account_id is not None:
            return account_id
        account = self.ledger.create_account(
            f"System balance adjustments {currency}",
            "system",
            currency,
            institution_type="system",
            subtype="system_adjustment",
            institution="track-anywhere",
            book_id=book_id,
        )
        self.adjustment_account_ids[key] = account.account_id
        return account.account_id

    def _system_fx_clearing_account_id(self, currency: str, *, book_id: str | None = None) -> str:
        book_id = book_id or self.books.ensure_default().book_id
        for account in self.ledger.accounts.values():
            if (
                account.type == "system"
                and account.currency == currency
                and account.book_id == book_id
                and account.institution_type == "system"
                and account.subtype == "fx_clearing"
                and account.institution == "track-anywhere"
            ):
                return account.account_id
        account = self.ledger.create_account(
            f"System FX clearing {currency}",
            "system",
            currency,
            institution_type="system",
            subtype="fx_clearing",
            institution="track-anywhere",
            book_id=book_id,
        )
        return account.account_id

    def _system_category_account_id(self, kind: str, currency: str, *, book_id: str | None = None) -> str:
        book_id = book_id or self.books.ensure_default().book_id
        subtype = f"{kind}_clearing"
        for account in self.ledger.accounts.values():
            if (
                account.type == kind
                and account.currency == currency
                and account.book_id == book_id
                and account.institution_type == "system"
                and account.subtype == subtype
                and account.institution == "track-anywhere"
            ):
                return account.account_id
        account = self.ledger.create_account(
            f"System {kind} {currency}",
            kind,
            currency,
            institution_type="system",
            subtype=subtype,
            institution="track-anywhere",
            book_id=book_id,
        )
        return account.account_id

    def _validate_transaction_category(self, category: Category, *, from_account_id: str, to_account_id: str) -> None:
        from_account = self.ledger.get_account(from_account_id)
        to_account = self.ledger.get_account(to_account_id)
        if category.kind == "expense" and to_account.type != "expense":
            raise ValidationError("expense category requires an expense to-account")
        if category.kind == "income" and from_account.type != "income":
            raise ValidationError("income category requires an income from-account")
