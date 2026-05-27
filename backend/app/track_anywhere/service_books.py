from __future__ import annotations

from .service_book_accounts import BookAccountUseCases
from .service_book_budgets import BookBudgetUseCases
from .service_book_categories import BookCategoryUseCases
from .service_book_core import BookCoreUseCases
from .service_book_ledger import BookLedgerUseCases


class BookUseCases(
    BookCoreUseCases,
    BookAccountUseCases,
    BookLedgerUseCases,
    BookCategoryUseCases,
    BookBudgetUseCases,
):
    pass
