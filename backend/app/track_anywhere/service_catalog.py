from __future__ import annotations

from .service_accounts import AccountUseCases
from .service_categories import CategoryUseCases
from .service_counterparties import CounterpartyUseCases
from .service_credit_cards import CreditCardUseCases
from .service_payment_instruments import PaymentInstrumentUseCases
from .service_users import UserUseCases


class CatalogUseCases(
    AccountUseCases,
    CategoryUseCases,
    CounterpartyUseCases,
    CreditCardUseCases,
    PaymentInstrumentUseCases,
    UserUseCases,
):
    pass
