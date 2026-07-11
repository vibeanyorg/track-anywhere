from __future__ import annotations

from .api_config import attachment_scanner_from_env, deployment_config_from_env as _deployment_config_from_env
from .service import FinanceService


service = FinanceService(
    _deployment_config_from_env(),
    persist_on_initialize=False,
    attachment_scanner=attachment_scanner_from_env(),
)
