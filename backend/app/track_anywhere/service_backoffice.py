from __future__ import annotations


class BackofficeUseCases:
    def require_backoffice(self, token: str) -> None:
        self.actor_from_token(token, "user:write")

    def backoffice_books(self):
        return self.storage.list_books()

    def backoffice_book_members(self):
        return self.storage.list_book_members()

    def backoffice_accounts(
        self,
        *,
        book_id: str | None = None,
        type: str | None = None,
        currency: str | None = None,
        institution_type: str | None = None,
        subtype: str | None = None,
    ):
        return self.storage.list_accounts(
            book_id=book_id,
            type=type,
            currency=currency,
            institution_type=institution_type,
            subtype=subtype,
        )

    def backoffice_users(self):
        return self.storage.list_users()

    def backoffice_auth_identities(self):
        return self.storage.list_auth_identities()

    def backoffice_categories(self):
        return self.storage.list_categories(book_id=None, status=None)

    def backoffice_transactions(self, *, book_id: str | None = None, category_id: str | None = None):
        book_ids = [book_id] if book_id is not None else [book.book_id for book in self.storage.list_books()]
        transactions = [
            transaction
            for current_book_id in book_ids
            for transaction in self.storage.list_all_confirmed_transactions(book_id=current_book_id)
        ]
        if category_id is None:
            return transactions
        return [
            transaction
            for transaction in transactions
            if any(line.category_id == category_id for line in transaction.lines)
        ]

    def backoffice_recurring_items(self):
        return self.storage.list_recurring_items(book_id=None, status=None)

    def backoffice_audit_events(self):
        return self.storage.list_audit_events()
