from __future__ import annotations

from datetime import UTC, datetime
import ast
import inspect
from uuid import UUID, uuid5

import pytest

from track_anywhere.domain import investments
from track_anywhere.domain.investments import allocation as allocation_module
from track_anywhere.domain.investments.allocation import (
    AllocationMethod,
    DuplicateLot,
    InvalidCostAllocation,
    InvestmentEvent,
    LotPosition,
    LotState,
    OverDisposal,
    SpecificLotRequest,
    WrongLotPool,
    reduce_investment_events,
    select_lot_allocations,
)
from track_anywhere.domain.investments.events import (
    InvestmentLotAcquired,
    InvestmentLotDisposed,
    LotDisposalAllocation,
)


COMMAND_ID = UUID("00000000-0000-4000-8000-000000000100")
LOT_ID = UUID("00000000-0000-4000-8000-000000000101")
LOT_ID_2 = UUID("00000000-0000-4000-8000-000000000102")
LOT_ID_3 = UUID("00000000-0000-4000-8000-000000000103")


def test_lot_reducer_is_exported_as_the_investment_domain_public_api() -> None:
    assert investments.AllocationMethod is AllocationMethod
    assert investments.InvestmentEvent is InvestmentEvent
    assert investments.LotState is LotState
    assert investments.SpecificLotRequest is SpecificLotRequest
    assert investments.reduce_investment_events is reduce_investment_events
    assert investments.select_lot_allocations is select_lot_allocations


def test_lot_reducer_has_no_float_literal_or_database_dependency() -> None:
    tree = ast.parse(inspect.getsource(allocation_module))

    assert not any(
        isinstance(node, ast.Constant) and isinstance(node.value, float)
        for node in ast.walk(tree)
    )
    assert not any(
        isinstance(node, (ast.Import, ast.ImportFrom))
        and (
            any(alias.name.startswith("sqlalchemy") for alias in node.names)
            if isinstance(node, ast.Import)
            else (node.module or "").startswith("sqlalchemy")
        )
        for node in ast.walk(tree)
    )


def test_fifo_selects_the_oldest_lot_and_derives_a_deterministic_allocation() -> None:
    state = LotState(
        lots=(
            LotPosition(
                lot_id=LOT_ID,
                instrument_asset_code="BTC",
                settlement_asset_code="CNY",
                effective_at=datetime(2026, 1, 1, tzinfo=UTC),
                source_position=1,
                remaining_quantity_units=100,
                remaining_cost_units=70,
            ),
        )
    )

    allocations = select_lot_allocations(
        state,
        instrument_asset_code="BTC",
        settlement_asset_code="CNY",
        quantity_units=40,
        method=AllocationMethod.FIFO,
        command_id=COMMAND_ID,
    )

    assert len(allocations) == 1
    assert allocations[0].lot_id == LOT_ID
    assert allocations[0].position == 0
    assert allocations[0].quantity_units == "40"
    assert allocations[0].cost_units == "28"
    assert allocations[0].allocation_id == uuid5(
        COMMAND_ID,
        f"0:{LOT_ID}:40:28",
    )


def _lot(
    lot_id: UUID,
    *,
    effective_at: datetime,
    source_position: int,
    quantity: int = 100,
    cost: int = 70,
    instrument: str = "BTC",
    settlement: str = "CNY",
) -> LotPosition:
    return LotPosition(
        lot_id=lot_id,
        instrument_asset_code=instrument,
        settlement_asset_code=settlement,
        effective_at=effective_at,
        source_position=source_position,
        remaining_quantity_units=quantity,
        remaining_cost_units=cost,
    )


def test_fifo_uses_effective_time_then_source_position_then_lot_id() -> None:
    later = datetime(2026, 2, 1, tzinfo=UTC)
    earlier = datetime(2026, 1, 1, tzinfo=UTC)
    state = LotState(
        lots=(
            _lot(LOT_ID_3, effective_at=later, source_position=1, quantity=30, cost=30),
            _lot(
                LOT_ID_2, effective_at=earlier, source_position=2, quantity=30, cost=30
            ),
            _lot(LOT_ID, effective_at=earlier, source_position=2, quantity=30, cost=30),
        )
    )

    allocations = select_lot_allocations(
        state,
        instrument_asset_code="BTC",
        settlement_asset_code="CNY",
        quantity_units=70,
        method=AllocationMethod.FIFO,
        command_id=COMMAND_ID,
    )

    assert [allocation.lot_id for allocation in allocations] == [
        LOT_ID,
        LOT_ID_2,
        LOT_ID_3,
    ]
    assert [allocation.position for allocation in allocations] == [0, 1, 2]
    assert [allocation.quantity_units for allocation in allocations] == [
        "30",
        "30",
        "10",
    ]


def test_specific_id_preserves_the_explicit_request_order() -> None:
    state = LotState(
        lots=(
            _lot(
                LOT_ID,
                effective_at=datetime(2026, 1, 1, tzinfo=UTC),
                source_position=1,
            ),
            _lot(
                LOT_ID_2,
                effective_at=datetime(2026, 2, 1, tzinfo=UTC),
                source_position=2,
            ),
        )
    )

    allocations = select_lot_allocations(
        state,
        instrument_asset_code="BTC",
        settlement_asset_code="CNY",
        quantity_units=100,
        method=AllocationMethod.SPECIFIC_ID,
        command_id=COMMAND_ID,
        specific_lots=(
            SpecificLotRequest(lot_id=LOT_ID_2, quantity_units=60),
            SpecificLotRequest(lot_id=LOT_ID, quantity_units=40),
        ),
    )

    assert [allocation.lot_id for allocation in allocations] == [LOT_ID_2, LOT_ID]
    assert [allocation.position for allocation in allocations] == [0, 1]
    assert [allocation.cost_units for allocation in allocations] == ["42", "28"]


def test_full_consumption_takes_all_remaining_cost() -> None:
    state = LotState(
        lots=(
            _lot(
                LOT_ID,
                effective_at=datetime(2026, 1, 1, tzinfo=UTC),
                source_position=1,
                quantity=3,
                cost=10,
            ),
        )
    )

    allocation = select_lot_allocations(
        state,
        instrument_asset_code="BTC",
        settlement_asset_code="CNY",
        quantity_units=3,
        method=AllocationMethod.FIFO,
        command_id=COMMAND_ID,
    )[0]

    assert allocation.cost_units == "10"


def test_partial_allocation_fails_closed_when_integer_cost_is_zero() -> None:
    state = LotState(
        lots=(
            _lot(
                LOT_ID,
                effective_at=datetime(2026, 1, 1, tzinfo=UTC),
                source_position=1,
                quantity=100,
                cost=1,
            ),
        )
    )

    with pytest.raises(InvalidCostAllocation):
        select_lot_allocations(
            state,
            instrument_asset_code="BTC",
            settlement_asset_code="CNY",
            quantity_units=1,
            method=AllocationMethod.FIFO,
            command_id=COMMAND_ID,
        )


def test_over_disposal_fails_without_returning_partial_allocations() -> None:
    state = LotState(
        lots=(
            _lot(
                LOT_ID,
                effective_at=datetime(2026, 1, 1, tzinfo=UTC),
                source_position=1,
                quantity=10,
                cost=10,
            ),
        )
    )

    with pytest.raises(OverDisposal):
        select_lot_allocations(
            state,
            instrument_asset_code="BTC",
            settlement_asset_code="CNY",
            quantity_units=11,
            method=AllocationMethod.FIFO,
            command_id=COMMAND_ID,
        )


def test_specific_id_rejects_duplicate_lots_and_wrong_pool() -> None:
    state = LotState(
        lots=(
            _lot(
                LOT_ID,
                effective_at=datetime(2026, 1, 1, tzinfo=UTC),
                source_position=1,
            ),
            _lot(
                LOT_ID_2,
                effective_at=datetime(2026, 1, 1, tzinfo=UTC),
                source_position=2,
                instrument="ETH",
            ),
        )
    )

    with pytest.raises(DuplicateLot):
        select_lot_allocations(
            state,
            instrument_asset_code="BTC",
            settlement_asset_code="CNY",
            quantity_units=2,
            method=AllocationMethod.SPECIFIC_ID,
            command_id=COMMAND_ID,
            specific_lots=(
                SpecificLotRequest(lot_id=LOT_ID, quantity_units=1),
                SpecificLotRequest(lot_id=LOT_ID, quantity_units=1),
            ),
        )

    with pytest.raises(WrongLotPool):
        select_lot_allocations(
            state,
            instrument_asset_code="BTC",
            settlement_asset_code="CNY",
            quantity_units=1,
            method=AllocationMethod.SPECIFIC_ID,
            command_id=COMMAND_ID,
            specific_lots=(SpecificLotRequest(lot_id=LOT_ID_2, quantity_units=1),),
        )


def _acquired(
    lot_id: UUID,
    *,
    quantity: str = "100",
    cost: str = "70",
    instrument: str = "BTC",
    settlement: str = "CNY",
) -> InvestmentLotAcquired:
    return InvestmentLotAcquired(
        transaction_id=uuid5(lot_id, "transaction"),
        lot_id=lot_id,
        instrument_asset_code=instrument,
        settlement_asset_code=settlement,
        quantity_units=quantity,
        cost_units=cost,
    )


def test_reducer_applies_stored_disposal_allocations_without_rerunning_fifo() -> None:
    allocation_id = UUID("00000000-0000-4000-8000-000000000110")
    events = (
        InvestmentEvent(
            source_position=1,
            effective_at=datetime(2026, 1, 1, tzinfo=UTC),
            payload=_acquired(LOT_ID),
        ),
        InvestmentEvent(
            source_position=2,
            effective_at=datetime(2026, 2, 1, tzinfo=UTC),
            payload=_acquired(LOT_ID_2),
        ),
        InvestmentEvent(
            source_position=3,
            effective_at=datetime(2026, 3, 1, tzinfo=UTC),
            payload=InvestmentLotDisposed(
                transaction_id=UUID("00000000-0000-4000-8000-000000000111"),
                instrument_asset_code="BTC",
                settlement_asset_code="CNY",
                quantity_units="100",
                proceeds_units="80",
                cost_basis_units="70",
                allocations=(
                    LotDisposalAllocation(
                        allocation_id=allocation_id,
                        lot_id=LOT_ID_2,
                        position=0,
                        quantity_units="100",
                        cost_units="70",
                    ),
                ),
            ),
        ),
    )

    state = reduce_investment_events(events)
    by_id = {lot.lot_id: lot for lot in state.lots}

    assert by_id[LOT_ID].remaining_quantity_units == 100
    assert by_id[LOT_ID].remaining_cost_units == 70
    assert by_id[LOT_ID_2].remaining_quantity_units == 0
    assert by_id[LOT_ID_2].remaining_cost_units == 0


def test_reducer_treats_frozen_cost_as_history_instead_of_rederiving_it() -> None:
    events = (
        InvestmentEvent(
            source_position=1,
            effective_at=datetime(2026, 1, 1, tzinfo=UTC),
            payload=_acquired(LOT_ID),
        ),
        InvestmentEvent(
            source_position=2,
            effective_at=datetime(2026, 2, 1, tzinfo=UTC),
            payload=InvestmentLotDisposed(
                transaction_id=UUID("00000000-0000-4000-8000-000000000114"),
                instrument_asset_code="BTC",
                settlement_asset_code="CNY",
                quantity_units="40",
                proceeds_units="50",
                cost_basis_units="29",
                allocations=(
                    LotDisposalAllocation(
                        allocation_id=UUID("00000000-0000-4000-8000-000000000115"),
                        lot_id=LOT_ID,
                        position=0,
                        quantity_units="40",
                        # The current selector would derive 28. Historical replay
                        # must apply the frozen 29 instead of rerunning that rule.
                        cost_units="29",
                    ),
                ),
            ),
        ),
    )

    lot = reduce_investment_events(events).lots[0]

    assert lot.remaining_quantity_units == 60
    assert lot.remaining_cost_units == 41


def test_reducer_rejects_duplicate_acquisitions_over_disposal_and_wrong_pool() -> None:
    acquired = InvestmentEvent(
        source_position=1,
        effective_at=datetime(2026, 1, 1, tzinfo=UTC),
        payload=_acquired(LOT_ID, quantity="10", cost="10"),
    )
    duplicate = InvestmentEvent(
        source_position=2,
        effective_at=datetime(2026, 1, 2, tzinfo=UTC),
        payload=_acquired(LOT_ID, quantity="10", cost="10"),
    )

    with pytest.raises(DuplicateLot):
        reduce_investment_events((acquired, duplicate))

    def disposal(*, instrument: str, quantity: str) -> InvestmentEvent:
        return InvestmentEvent(
            source_position=2,
            effective_at=datetime(2026, 2, 1, tzinfo=UTC),
            payload=InvestmentLotDisposed(
                transaction_id=UUID("00000000-0000-4000-8000-000000000112"),
                instrument_asset_code=instrument,
                settlement_asset_code="CNY",
                quantity_units=quantity,
                proceeds_units="20",
                cost_basis_units=quantity,
                allocations=(
                    LotDisposalAllocation(
                        allocation_id=UUID("00000000-0000-4000-8000-000000000113"),
                        lot_id=LOT_ID,
                        position=0,
                        quantity_units=quantity,
                        cost_units=quantity,
                    ),
                ),
            ),
        )

    with pytest.raises(OverDisposal):
        reduce_investment_events((acquired, disposal(instrument="BTC", quantity="11")))
    with pytest.raises(WrongLotPool):
        reduce_investment_events((acquired, disposal(instrument="ETH", quantity="10")))
