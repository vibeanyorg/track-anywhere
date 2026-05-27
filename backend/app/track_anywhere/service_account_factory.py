from __future__ import annotations

from uuid import uuid4

from .books import DEFAULT_BOOK_ID
from .ledger import Account


class AccountFactory:
    @staticmethod
    def _new_account(
        name: str,
        type: str,
        currency: str,
        *,
        institution_type: str | None = None,
        subtype: str | None = None,
        institution: str | None = None,
        book_id: str = DEFAULT_BOOK_ID,
    ) -> Account:
        return Account(
            account_id=f"acc_{uuid4().hex}",
            name=name,
            type=type,
            currency=currency,
            institution_type=institution_type,
            subtype=subtype,
            institution=institution,
            book_id=book_id,
        )
