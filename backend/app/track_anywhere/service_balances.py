from __future__ import annotations

from .service_balance_adjustments import BalanceAdjustmentUseCases
from .service_balance_queries import BalanceQueryUseCases
from .service_balance_system_accounts import SystemAccountUseCases


class BalanceUseCases(
    BalanceAdjustmentUseCases,
    BalanceQueryUseCases,
    SystemAccountUseCases,
):
    pass
