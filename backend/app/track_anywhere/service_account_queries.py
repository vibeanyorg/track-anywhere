from __future__ import annotations

from .books import DEFAULT_BOOK_ID
from .ledger import Account


class AccountQueryUseCases:
    def list_accounts(
        self,
        token: str,
        *,
        name: str | None = None,
        type: str | None = None,
        currency: str | None = None,
        institution_type: str | None = None,
        subtype: str | None = None,
        institution: str | None = None,
        book_id: str | None = DEFAULT_BOOK_ID,
    ) -> list[Account]:
        if book_id is not None:
            self.actor_for_book(token, book_id, "account:read")
        else:
            self.actor_from_token(token, "account:read")
        return self.storage.list_accounts(
            book_id=book_id,
            name=name,
            type=type,
            currency=currency,
            institution_type=institution_type,
            subtype=subtype,
            institution=institution,
        )

    def get_account(self, token: str, account_id: str) -> Account:
        account = self.storage.get_account(account_id)
        self.actor_for_book(token, account.book_id, "account:read")
        return account
