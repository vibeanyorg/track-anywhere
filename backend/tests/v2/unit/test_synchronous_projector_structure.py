from __future__ import annotations

from types import MappingProxyType

from track_anywhere.domain.credit_cards.events import CreditCardTransactionRecorded
from track_anywhere.domain.investments.events import (
    InvestmentLotAcquired,
    InvestmentLotDisposed,
)
from track_anywhere.domain.journal.events import (
    FinancialExternalReferenceCorrected,
    JournalTransactionPosted,
    JournalTransactionReversed,
)
from track_anywhere.domain.reporting.events import (
    ReportingLinesAssigned,
    ReportingLinesCleared,
)
from track_anywhere.infrastructure.projections import synchronous


def test_synchronous_applier_registry_is_complete_typed_and_split_by_event_family() -> (
    None
):
    registry = getattr(synchronous, "SYNCHRONOUS_APPLIERS", None)
    assert isinstance(registry, MappingProxyType)

    expected_modules = {
        CreditCardTransactionRecorded: (
            "track_anywhere.infrastructure.projections."
            "synchronous_appliers.credit_cards"
        ),
        JournalTransactionPosted: (
            "track_anywhere.infrastructure.projections.synchronous_appliers.journal"
        ),
        JournalTransactionReversed: (
            "track_anywhere.infrastructure.projections.synchronous_appliers.journal"
        ),
        FinancialExternalReferenceCorrected: (
            "track_anywhere.infrastructure.projections.synchronous_appliers.journal"
        ),
        ReportingLinesAssigned: (
            "track_anywhere.infrastructure.projections.synchronous_appliers.reporting"
        ),
        ReportingLinesCleared: (
            "track_anywhere.infrastructure.projections.synchronous_appliers.reporting"
        ),
        InvestmentLotAcquired: (
            "track_anywhere.infrastructure.projections.synchronous_appliers.investments"
        ),
        InvestmentLotDisposed: (
            "track_anywhere.infrastructure.projections.synchronous_appliers.investments"
        ),
    }

    assert set(registry) == set(expected_modules)
    assert {
        payload_type: registration.handler.__module__
        for payload_type, registration in registry.items()
    } == expected_modules
    assert all(
        registration.payload_type is payload_type
        for payload_type, registration in registry.items()
    )


def test_synchronous_projector_contains_only_orchestration_not_domain_appliers() -> (
    None
):
    assert not {
        name
        for name in vars(synchronous.SynchronousProjector)
        if name.startswith("_apply_") or name.startswith("_validate_")
    }
