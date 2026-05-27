from __future__ import annotations

from .service_account_commands import AccountCommandUseCases
from .service_account_factory import AccountFactory
from .service_account_queries import AccountQueryUseCases
from .service_account_summary import AccountSummaryUseCases


class AccountUseCases(
    AccountQueryUseCases,
    AccountCommandUseCases,
    AccountSummaryUseCases,
    AccountFactory,
):
    pass
