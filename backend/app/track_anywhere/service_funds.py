from __future__ import annotations

from .service_fund_catalog import FundCatalogUseCases
from .service_fund_flows import FundFlowUseCases


class FundUseCases(
    FundCatalogUseCases,
    FundFlowUseCases,
):
    pass
