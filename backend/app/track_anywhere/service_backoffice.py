from __future__ import annotations


class BackofficeUseCases:
    def require_backoffice(self, token: str) -> None:
        self.actor_from_token(token, "user:write")

    def backoffice_books(self):
        return self.books.list(status=None)

    def backoffice_book_members(self):
        return sorted(
            self.books.members.values(),
            key=lambda member: (member.book_id, member.user_id, member.role),
        )

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
        return self.users.list()

    def backoffice_auth_identities(self):
        return sorted(
            self.auth_identities.identities.values(),
            key=lambda identity: (identity.provider, identity.subject, identity.identity_id),
        )

    def backoffice_categories(self):
        return self.categories.list(status=None)

    def backoffice_transactions(self, *, book_id: str | None = None, category_id: str | None = None):
        book_ids = [book_id] if book_id is not None else [book.book_id for book in self.books.list(status=None)]
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
        return self.recurring.list(status=None)

    def backoffice_audit_events(self):
        return self.audit.events
