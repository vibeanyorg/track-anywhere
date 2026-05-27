from __future__ import annotations

from .service_payment_profile_expenses import PaymentProfileExpenseUseCases
from .service_payment_profile_lifecycle import PaymentProfileLifecycleUseCases


class PaymentProfileUseCases(
    PaymentProfileLifecycleUseCases,
    PaymentProfileExpenseUseCases,
):
    pass
