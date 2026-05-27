from __future__ import annotations

from .service_ledger_queries import LedgerQueryUseCases
from .service_ledger_records import LedgerRecordUseCases
from .service_ledger_reversals import LedgerReversalUseCases
from .service_ledger_transfers import LedgerTransferUseCases


class LedgerUseCases(
    LedgerQueryUseCases,
    LedgerTransferUseCases,
    LedgerRecordUseCases,
    LedgerReversalUseCases,
):
    pass
