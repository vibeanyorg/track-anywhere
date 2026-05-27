from __future__ import annotations

from .service_attachments import AttachmentUseCases
from .service_funds import FundUseCases
from .service_payment_profiles import PaymentProfileUseCases
from .service_reconciliation import ReconciliationUseCases


class FinancialUseCases(
    PaymentProfileUseCases,
    FundUseCases,
    AttachmentUseCases,
    ReconciliationUseCases,
):
    pass
