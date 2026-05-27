from __future__ import annotations

from decimal import Decimal
from typing import Any

from .categories import Category
from .commands import BalanceAdjustmentCommand
from .errors import NotFound, ValidationError
from .ledger import Posting
from .transaction_builder import build_transaction


class BalanceUseCases:
    def adjust_balance(self, token: str, payload: dict[str, Any], *, idempotency_key: str):
        command = BalanceAdjustmentCommand.model_validate(payload)
        account = self.storage.get_account(command.account_id)
        actor = self.actor_for_book(token, account.book_id, "ledger:confirm")
        self.assets.validate_amount(command.currency, command.amount)
        request_hash = self._hash_command(command)

        def run():
            if account.currency != command.currency:
                raise ValidationError("balance adjustment currency must match account currency")
            adjustment_account_id = self._system_adjustment_account_id(command.currency, book_id=account.book_id)
            adjustment_account = self._transaction_account(adjustment_account_id)
            transaction = build_transaction(
                memo=command.memo,
                occurred_at=command.occurred_at,
                purpose=command.purpose,
                postings=[
                    Posting(command.account_id, command.amount, command.currency),
                    Posting(adjustment_account_id, -command.amount, command.currency),
                ],
                accounts=[account, adjustment_account],
                scale_lookup=self.assets.scale_for,
            )
            self.ledger.transactions[transaction.transaction_id] = transaction
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

        transaction, replay = self.idempotency.run(
            key=idempotency_key,
            actor=actor,
            operation="ledger.balance.adjust",
            request_hash=request_hash,
            fn=run,
        )
        if replay:
            self._persist_idempotency()
        else:
            self._persist_ledger_change(transaction)
        return transaction, replay

    def account_balance(self, token: str, account_id: str, *, include_drafts: bool = False) -> dict[str, Any]:
        account = self.storage.get_account(account_id)
        self.actor_for_book(token, account.book_id, "account:read")
        official = self.storage.account_balance(account_id)
        pending: dict[str, Decimal] = {}
        included_draft_ids: list[str] = []
        draft_count = self.storage.draft_count()
        if include_drafts:
            pending, included_draft_ids, draft_count = self.storage.draft_projection_for_account(account_id)
        currency = account.currency
        official_amount = official.get(currency, Decimal("0"))
        pending_amount = pending.get(currency, Decimal("0"))
        result = {
            "account_id": account_id,
            "currency": currency,
            "official_balance": {
                "amount": str(official_amount),
                "source": "confirmed_postings",
                "as_of_ledger_version": self.storage.confirmed_transaction_count(book_id=account.book_id),
            },
            "default_view": "official",
            "provenance": {
                "confirmed_transaction_count": self.storage.confirmed_transaction_count(book_id=account.book_id),
                "draft_count": draft_count,
            },
        }
        if include_drafts:
            result["projected_balance"] = {
                "amount": str(official_amount + pending_amount),
                "pending_impact": str(pending_amount),
                "included_draft_ids": included_draft_ids,
                "projection_version": draft_count,
            }
        return result

    def _system_adjustment_account_id(self, currency: str, *, book_id: str | None = None) -> str:
        book_id = book_id or self.books.ensure_default().book_id
        key = f"{book_id}:{currency}"
        account_id = self.adjustment_account_ids.get(key) or self.adjustment_account_ids.get(currency)
        if account_id is not None:
            return account_id
        account_id = self._find_system_account_id(
            type="system",
            currency=currency,
            book_id=book_id,
            subtype="system_adjustment",
        )
        if account_id is not None:
            self.adjustment_account_ids[key] = account_id
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
        account_id = self._find_system_account_id(
            type="system",
            currency=currency,
            book_id=book_id,
            subtype="fx_clearing",
        )
        if account_id is not None:
            return account_id
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
        account_id = self._find_system_account_id(type=kind, currency=currency, book_id=book_id, subtype=subtype)
        if account_id is not None:
            return account_id
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
        from_account = self._transaction_account(from_account_id)
        to_account = self._transaction_account(to_account_id)
        if category.kind == "expense" and to_account.type != "expense":
            raise ValidationError("expense category requires an expense to-account")
        if category.kind == "income" and from_account.type != "income":
            raise ValidationError("income category requires an income from-account")

    def _transaction_account(self, account_id: str):
        try:
            return self.storage.get_account(account_id)
        except NotFound:
            return self.ledger.get_account(account_id)

    def _find_system_account_id(self, *, type: str, currency: str, book_id: str, subtype: str) -> str | None:
        matches = self.storage.list_accounts(
            book_id=book_id,
            type=type,
            currency=currency,
            institution_type="system",
            subtype=subtype,
            institution="track-anywhere",
        )
        return matches[0].account_id if matches else None
