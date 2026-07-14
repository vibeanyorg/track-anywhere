from __future__ import annotations

import pytest
from pydantic import ValidationError

from track_anywhere.domain.backfill.events import (
    ExactImportedDecimal,
    HistoricalCategoryActivityImported,
    HistoricalCategoryActivityKind,
    HistoricalInvestmentActivityImported,
    HistoricalInvestmentActivityKind,
    HistoricalReportingLineImported,
    HistoricalReportingLineKind,
)
from track_anywhere.serialization.event_registry import PRODUCTION_EVENT_REGISTRY


ROW_HASH = "a" * 64


def test_historical_investment_event_preserves_exact_nullable_source_facts() -> None:
    payload = HistoricalInvestmentActivityImported(
        source_event_id="inv-source-1",
        source_account_id="account-source-1",
        activity_kind=HistoricalInvestmentActivityKind.BUY,
        settlement_asset_code="CNY",
        cash_amount=ExactImportedDecimal(unscaled_units="5000000", scale=2),
        quantity=ExactImportedDecimal(unscaled_units="4933886", scale=2),
        nav=ExactImportedDecimal(unscaled_units="10160", scale=4),
        source_version=1,
        source_row_hash=ROW_HASH,
    )

    assert payload.model_dump(mode="json") == {
        "activity_kind": "buy",
        "cash_amount": {"scale": 2, "unscaled_units": "5000000"},
        "nav": {"scale": 4, "unscaled_units": "10160"},
        "quantity": {"scale": 2, "unscaled_units": "4933886"},
        "settlement_asset_code": "CNY",
        "source_account_id": "account-source-1",
        "source_event_id": "inv-source-1",
        "source_row_hash": ROW_HASH,
        "source_version": 1,
    }
    assert PRODUCTION_EVENT_REGISTRY.dump_registered(payload)["quantity"] == {
        "scale": 2,
        "unscaled_units": "4933886",
    }

    missing_quantity = payload.model_copy(update={"quantity": None, "nav": None})
    assert PRODUCTION_EVENT_REGISTRY.dump_registered(missing_quantity)["nav"] is None


@pytest.mark.parametrize(
    ("unscaled_units", "scale"),
    [("0", 0), ("01", 0), ("1.0", 1), ("1", -1), ("1", 31)],
)
def test_imported_decimal_rejects_noncanonical_or_unbounded_values(
    unscaled_units: str,
    scale: int,
) -> None:
    with pytest.raises(ValidationError):
        ExactImportedDecimal(unscaled_units=unscaled_units, scale=scale)


def test_historical_fx_line_preserves_line_type_and_exact_asset_units() -> None:
    payload = HistoricalReportingLineImported(
        source_line_id="line-source-1",
        source_transaction_id="transaction-source-1",
        transaction_id="00000000-0000-4000-8000-000000000001",
        line_kind=HistoricalReportingLineKind.FX_FEE,
        position=1,
        asset_code="CNY",
        amount=ExactImportedDecimal(unscaled_units="10", scale=2),
        source_version=1,
        source_row_hash=ROW_HASH,
    )

    dumped = PRODUCTION_EVENT_REGISTRY.dump_registered(payload)
    assert dumped["line_kind"] == "fx_fee"
    assert dumped["amount"] == {"scale": 2, "unscaled_units": "10"}


def test_historical_category_event_binds_source_semantics_by_hash() -> None:
    payload = HistoricalCategoryActivityImported(
        source_event_id="classification-source-1",
        activity_kind=HistoricalCategoryActivityKind.RECLASSIFY,
        source_category_id="category-before",
        target_category_id="category-after",
        affected_line_count=1,
        source_actor_hash="e" * 64,
        source_version=1,
        before_hash="b" * 64,
        after_hash="c" * 64,
        rollback_hash="d" * 64,
        source_row_hash=ROW_HASH,
    )

    dumped = PRODUCTION_EVENT_REGISTRY.dump_registered(payload)
    assert dumped["activity_kind"] == "reclassify"
    assert dumped["before_hash"] == "b" * 64
    assert dumped["after_hash"] == "c" * 64


@pytest.mark.parametrize(
    "changes",
    [
        {"source_category_id": None},
        {"source_actor_hash": None},
        {"target_category_id": None},
    ],
)
def test_historical_reclassification_requires_complete_source_identity(
    changes: dict[str, object],
) -> None:
    values: dict[str, object] = {
        "source_event_id": "classification-source-1",
        "activity_kind": HistoricalCategoryActivityKind.RECLASSIFY,
        "source_category_id": "category-before",
        "target_category_id": "category-after",
        "affected_line_count": 1,
        "source_actor_hash": "e" * 64,
        "source_version": 1,
        "before_hash": "b" * 64,
        "after_hash": "c" * 64,
        "rollback_hash": "d" * 64,
        "source_row_hash": ROW_HASH,
    }
    values.update(changes)

    with pytest.raises(ValidationError):
        HistoricalCategoryActivityImported(**values)


def test_historical_category_create_requires_null_target() -> None:
    common: dict[str, object] = {
        "source_event_id": "classification-source-1",
        "activity_kind": HistoricalCategoryActivityKind.CREATE,
        "source_category_id": "category-created",
        "affected_line_count": 0,
        "source_actor_hash": "e" * 64,
        "source_version": 1,
        "before_hash": "b" * 64,
        "after_hash": "c" * 64,
        "rollback_hash": "d" * 64,
        "source_row_hash": ROW_HASH,
    }

    payload = HistoricalCategoryActivityImported(
        **common,
        target_category_id=None,
    )
    assert payload.target_category_id is None
    with pytest.raises(ValidationError):
        HistoricalCategoryActivityImported(
            **common,
            target_category_id="category-invalid-target",
        )
