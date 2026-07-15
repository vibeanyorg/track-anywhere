from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from ....domain.credit_cards.events import CreditCardTransactionRecorded
from ....domain.investments.events import (
    InvestmentLotAcquired,
    InvestmentLotDisposed,
)
from ....domain.journal.events import (
    FinancialExternalReferenceCorrected,
    JournalTransactionPosted,
    JournalTransactionReversed,
)
from ....domain.privacy import EventContract
from ....domain.reporting.events import (
    ReportingLinesAssigned,
    ReportingLinesCleared,
)
from .contracts import TypedEventApplier, typed_event_applier
from .credit_cards import apply_credit_card_recorded
from .investments import (
    apply_investment_lot_acquired,
    apply_investment_lot_disposed,
)
from .journal import (
    apply_external_reference_corrected,
    apply_journal_posted,
    apply_journal_reversed,
)
from .reporting import (
    apply_reporting_lines_assigned,
    apply_reporting_lines_cleared,
)


_REGISTRATIONS = (
    typed_event_applier(CreditCardTransactionRecorded, apply_credit_card_recorded),
    typed_event_applier(JournalTransactionPosted, apply_journal_posted),
    typed_event_applier(JournalTransactionReversed, apply_journal_reversed),
    typed_event_applier(ReportingLinesAssigned, apply_reporting_lines_assigned),
    typed_event_applier(ReportingLinesCleared, apply_reporting_lines_cleared),
    typed_event_applier(
        FinancialExternalReferenceCorrected,
        apply_external_reference_corrected,
    ),
    typed_event_applier(InvestmentLotAcquired, apply_investment_lot_acquired),
    typed_event_applier(InvestmentLotDisposed, apply_investment_lot_disposed),
)

SYNCHRONOUS_APPLIERS: Mapping[type[EventContract], TypedEventApplier] = (
    MappingProxyType(
        {registration.payload_type: registration for registration in _REGISTRATIONS}
    )
)

if len(SYNCHRONOUS_APPLIERS) != len(_REGISTRATIONS):
    raise RuntimeError("duplicate synchronous projection applier registration")


__all__ = ["SYNCHRONOUS_APPLIERS"]
