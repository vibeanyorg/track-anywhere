from __future__ import annotations

from .service_accounts import AccountUseCases
from .service_categories import CategoryUseCases
from .service_credit_cards import CreditCardUseCases
from .service_users import UserUseCases


class CatalogUseCases(
    AccountUseCases,
    CategoryUseCases,
    CreditCardUseCases,
    UserUseCases,
):
    pass
