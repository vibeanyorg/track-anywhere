from __future__ import annotations

from .service_investment_events import InvestmentEventUseCases
from .service_investment_performance import InvestmentPerformanceUseCases
from .service_investment_valuations import InvestmentValuationUseCases


class InvestmentUseCases(
    InvestmentEventUseCases,
    InvestmentValuationUseCases,
    InvestmentPerformanceUseCases,
):
    pass
