from __future__ import annotations

from uuid import UUID

import pytest
from pydantic import ValidationError

from track_anywhere.domain.investments.events import (
    InvestmentLotAcquired,
    InvestmentLotDisposed,
    LotDisposalAllocation,
)
from track_anywhere.domain.journal.events import (
    ExternalReferenceKind,
    FinancialExternalReference,
    FinancialExternalReferenceCorrected,
    JournalPostingFact,
    JournalTransactionPosted,
    JournalTransactionReversed,
    ReversalReasonCode,
)
from track_anywhere.domain.journal.models import PostingSide, TransactionKind
from track_anywhere.domain.reporting.events import (
    ReportingDimension,
    ReportingLine,
    ReportingLineKind,
    ReportingLinesAssigned,
    ReportingLinesCleared,
)


TRANSACTION_ID = UUID("00000000-0000-4000-8000-000000000002")
OTHER_TRANSACTION_ID = UUID("00000000-0000-4000-8000-000000000003")
DESCRIPTION_REF = UUID("00000000-0000-4000-8000-000000000004")
ACCOUNT_ID_1 = UUID("00000000-0000-4000-8000-000000000005")
ACCOUNT_ID_2 = UUID("00000000-0000-4000-8000-000000000006")
POSTING_ID_1 = UUID("00000000-0000-4000-8000-000000000007")
POSTING_ID_2 = UUID("00000000-0000-4000-8000-000000000008")
LOT_ID = UUID("00000000-0000-4000-8000-00000000000b")
LOT_ID_2 = UUID("00000000-0000-4000-8000-00000000000c")
ALLOCATION_ID_1 = UUID("00000000-0000-4000-8000-00000000000d")
ALLOCATION_ID_2 = UUID("00000000-0000-4000-8000-00000000000e")
LINE_ID = UUID("00000000-0000-4000-8000-00000000000f")
LINE_VERSION_ID = UUID("00000000-0000-4000-8000-000000000010")
CATALOG_ID = UUID("00000000-0000-4000-8000-000000000011")
DIMENSION_ID = UUID("00000000-0000-4000-8000-000000000012")


def _posting(
    *,
    posting_id: UUID = POSTING_ID_1,
    position: int = 0,
    side: PostingSide = PostingSide.DEBIT,
    units: str = "70000",
) -> JournalPostingFact:
    return JournalPostingFact(
        posting_id=posting_id,
        position=position,
        account_id=ACCOUNT_ID_1 if position == 0 else ACCOUNT_ID_2,
        asset_code="CNY",
        side=side,
        units=units,
    )


def _postings() -> tuple[JournalPostingFact, JournalPostingFact]:
    return (
        _posting(),
        _posting(
            posting_id=POSTING_ID_2,
            position=1,
            side=PostingSide.CREDIT,
        ),
    )


def _posted_event() -> JournalTransactionPosted:
    return JournalTransactionPosted(
        transaction_id=TRANSACTION_ID,
        kind=TransactionKind.STANDARD,
        postings=_postings(),
        description_ref=DESCRIPTION_REF,
        external_references=(
            FinancialExternalReference(
                provider_code="stripe",
                kind=ExternalReferenceKind.PROVIDER_TRANSACTION,
                reference="pi_123456",
            ),
        ),
    )


def test_posted_event_serializes_exact_enums_uuids_and_unit_strings() -> None:
    event = _posted_event()

    dumped = event.model_dump(mode="json")

    assert dumped["transaction_id"] == str(TRANSACTION_ID)
    assert dumped["kind"] == "standard"
    assert dumped["postings"][0]["side"] == "debit"
    assert dumped["postings"][0]["posting_id"] == str(POSTING_ID_1)
    assert dumped["postings"][0]["units"] == "70000"
    assert isinstance(dumped["postings"][0]["units"], str)
    assert event.postings == _postings()
    assert isinstance(event.postings, tuple)
    assert (
        JournalTransactionPosted.model_validate_json(event.model_dump_json()) == event
    )


def test_posted_event_round_trips_through_a_jsonb_mapping() -> None:
    event = _posted_event()

    raw = event.model_dump(mode="json")
    restored = JournalTransactionPosted.model_validate(raw)

    assert restored == event
    assert isinstance(restored.postings, tuple)
    assert isinstance(restored.external_references, tuple)


def test_non_card_refund_round_trips_with_explicit_semantics() -> None:
    event = JournalTransactionPosted(
        transaction_id=TRANSACTION_ID,
        kind=TransactionKind.REFUND,
        original_transaction_id=OTHER_TRANSACTION_ID,
        postings=_postings(),
    )

    raw = event.model_dump(mode="json")
    restored = JournalTransactionPosted.model_validate(raw)

    assert raw["kind"] == "refund"
    assert raw["original_transaction_id"] == str(OTHER_TRANSACTION_ID)
    assert restored.kind is TransactionKind.REFUND
    assert restored.original_transaction_id == OTHER_TRANSACTION_ID


@pytest.mark.parametrize(
    ("model", "event_type"),
    [
        (JournalTransactionPosted, "JournalTransactionPosted"),
        (JournalTransactionReversed, "JournalTransactionReversed"),
        (
            FinancialExternalReferenceCorrected,
            "FinancialExternalReferenceCorrected",
        ),
        (ReportingLinesAssigned, "ReportingLinesAssigned"),
        (ReportingLinesCleared, "ReportingLinesCleared"),
        (InvestmentLotAcquired, "InvestmentLotAcquired"),
        (InvestmentLotDisposed, "InvestmentLotDisposed"),
    ],
)
def test_event_discriminators_are_exact_class_metadata_not_payload_fields(
    model: type,
    event_type: str,
) -> None:
    assert model.event_type == event_type
    assert model.schema_version == 1
    assert "event_id" not in model.model_fields
    assert "event_type" not in model.model_fields
    assert "schema_version" not in model.model_fields


@pytest.mark.parametrize(
    "postings",
    [
        (_posting(),),
        (_posting(), _posting(posting_id=POSTING_ID_1, position=1)),
        (_posting(), _posting(posting_id=POSTING_ID_2, position=0)),
        (_posting(), _posting(posting_id=POSTING_ID_2, position=2)),
    ],
    ids=["too-few", "duplicate-id", "duplicate-position", "position-gap"],
)
def test_journal_events_require_complete_deterministic_posting_sets(
    postings: tuple[JournalPostingFact, ...],
) -> None:
    with pytest.raises(ValidationError):
        JournalTransactionPosted(
            transaction_id=TRANSACTION_ID,
            kind=TransactionKind.STANDARD,
            postings=postings,
        )


@pytest.mark.parametrize(
    "units",
    ["0", "00", "01", "-1", "+1", " 1", "1 ", "1e2", "1.0", "9" * 39],
)
def test_all_canonical_unit_fields_reject_noncanonical_or_out_of_range_values(
    units: str,
) -> None:
    with pytest.raises(ValidationError):
        _posting(units=units)


@pytest.mark.parametrize("value", [True, 0.0, "0"])
def test_ordered_record_positions_reject_coerced_scalar_types(value: object) -> None:
    records = (
        (JournalPostingFact, _posting()),
        (ReportingLine, _reporting_line()),
        (LotDisposalAllocation, _allocation()),
    )

    for model, record in records:
        raw = record.model_dump(mode="json")
        raw["position"] = value

        with pytest.raises(ValidationError):
            model.model_validate(raw)


@pytest.mark.parametrize("value", [123, True, object()])
@pytest.mark.parametrize("field", ["units", "asset_code"])
def test_posting_string_facts_reject_non_string_inputs(
    field: str,
    value: object,
) -> None:
    raw = _posting().model_dump(mode="json")
    raw[field] = value

    with pytest.raises(ValidationError):
        JournalPostingFact.model_validate(raw)


@pytest.mark.parametrize("value", [123, True, object()])
@pytest.mark.parametrize("field", ["provider_code", "reference"])
def test_external_reference_string_facts_reject_non_string_inputs(
    field: str,
    value: object,
) -> None:
    raw = FinancialExternalReference(
        provider_code="stripe",
        kind=ExternalReferenceKind.PROVIDER_TRANSACTION,
        reference="pi_123456",
    ).model_dump(mode="json")
    raw[field] = value

    with pytest.raises(ValidationError):
        FinancialExternalReference.model_validate(raw)


def test_canonical_units_accept_the_38_digit_boundary() -> None:
    boundary = str(10**38 - 1)

    posting = _posting(units=boundary)
    acquired = InvestmentLotAcquired(
        transaction_id=TRANSACTION_ID,
        lot_id=LOT_ID,
        instrument_asset_code="BTC",
        settlement_asset_code="CNY",
        quantity_units=boundary,
        cost_units=boundary,
        fee_units=boundary,
    )

    assert posting.units == boundary
    assert acquired.model_dump(mode="json")["cost_units"] == boundary


def test_reversal_is_self_contained_and_authenticates_the_original_event() -> None:
    event = JournalTransactionReversed(
        reversal_transaction_id=TRANSACTION_ID,
        reverses_transaction_id=OTHER_TRANSACTION_ID,
        original_event_id=UUID("00000000-0000-4000-8000-000000000099"),
        original_event_hash="a" * 64,
        reason_code=ReversalReasonCode.USER_CORRECTION,
        inverse_postings=_postings(),
        description_ref=DESCRIPTION_REF,
    )

    dumped = event.model_dump(mode="json")
    assert dumped["reason_code"] == "user_correction"
    assert dumped["inverse_postings"] == [
        posting.model_dump(mode="json") for posting in _postings()
    ]


@pytest.mark.parametrize(
    "event_hash",
    ["a" * 63, "a" * 65, "A" * 64, "g" * 64, " a" * 63],
)
def test_reversal_requires_a_canonical_lowercase_sha256(event_hash: str) -> None:
    with pytest.raises(ValidationError):
        JournalTransactionReversed(
            reversal_transaction_id=TRANSACTION_ID,
            reverses_transaction_id=OTHER_TRANSACTION_ID,
            original_event_id=UUID("00000000-0000-4000-8000-000000000099"),
            original_event_hash=event_hash,
            reason_code=ReversalReasonCode.DUPLICATE,
            inverse_postings=_postings(),
        )


def test_external_reference_correction_carries_previous_and_corrected_values() -> None:
    correction = FinancialExternalReferenceCorrected(
        transaction_id=TRANSACTION_ID,
        provider_code="stripe",
        reference_kind=ExternalReferenceKind.PROVIDER_TRANSACTION,
        previous_reference="pi_wrong",
        corrected_reference="pi_right",
    )
    first_assignment = FinancialExternalReferenceCorrected(
        transaction_id=TRANSACTION_ID,
        provider_code="stripe",
        reference_kind=ExternalReferenceKind.PROVIDER_TRANSACTION,
        previous_reference=None,
        corrected_reference="pi_first",
    )

    assert correction.model_dump(mode="json") == {
        "transaction_id": str(TRANSACTION_ID),
        "provider_code": "stripe",
        "reference_kind": "provider_transaction",
        "previous_reference": "pi_wrong",
        "corrected_reference": "pi_right",
    }
    assert first_assignment.previous_reference is None


def _reporting_line(
    *,
    line_id: UUID = LINE_ID,
    position: int = 0,
) -> ReportingLine:
    return ReportingLine(
        line_id=line_id,
        line_version_id=LINE_VERSION_ID,
        catalog_id=CATALOG_ID,
        position=position,
        asset_code="CNY",
        units="70000",
        line_kind=ReportingLineKind.EXPENSE,
        dimension=ReportingDimension.CATEGORY,
        dimension_id=DIMENSION_ID,
        description_ref=DESCRIPTION_REF,
    )


def test_reporting_assignment_is_a_nonempty_replace_all_snapshot() -> None:
    event = ReportingLinesAssigned(
        transaction_id=TRANSACTION_ID,
        classification_revision=3,
        lines=(_reporting_line(),),
    )

    dumped = event.model_dump(mode="json")
    assert dumped["classification_revision"] == 3
    assert dumped["lines"][0]["line_kind"] == "expense"
    assert dumped["lines"][0]["dimension"] == "category"
    assert dumped["lines"][0]["line_version_id"] == str(LINE_VERSION_ID)
    assert dumped["lines"][0]["catalog_id"] == str(CATALOG_ID)


def test_reporting_assignment_round_trips_through_a_jsonb_mapping() -> None:
    event = ReportingLinesAssigned(
        transaction_id=TRANSACTION_ID,
        classification_revision=3,
        lines=(_reporting_line(),),
    )

    raw = event.model_dump(mode="json")
    restored = ReportingLinesAssigned.model_validate(raw)

    assert restored == event
    assert isinstance(restored.lines, tuple)


@pytest.mark.parametrize("value", [True, 1.0, "1"])
def test_classification_revisions_reject_coerced_scalar_types(value: object) -> None:
    assigned = ReportingLinesAssigned(
        transaction_id=TRANSACTION_ID,
        classification_revision=3,
        lines=(_reporting_line(),),
    ).model_dump(mode="json")
    cleared = ReportingLinesCleared(
        transaction_id=TRANSACTION_ID,
        classification_revision=4,
    ).model_dump(mode="json")
    assigned["classification_revision"] = value
    cleared["classification_revision"] = value

    with pytest.raises(ValidationError):
        ReportingLinesAssigned.model_validate(assigned)
    with pytest.raises(ValidationError):
        ReportingLinesCleared.model_validate(cleared)


@pytest.mark.parametrize(
    ("revision", "lines"),
    [
        (0, (_reporting_line(),)),
        (1, ()),
        (1, (_reporting_line(), _reporting_line(position=1))),
        (
            1,
            (
                _reporting_line(),
                _reporting_line(
                    line_id=UUID("00000000-0000-4000-8000-000000000021"),
                    position=2,
                ),
            ),
        ),
    ],
    ids=["nonpositive-revision", "empty", "duplicate-line-id", "position-gap"],
)
def test_reporting_assignment_rejects_incomplete_or_ambiguous_snapshots(
    revision: int,
    lines: tuple[ReportingLine, ...],
) -> None:
    with pytest.raises(ValidationError):
        ReportingLinesAssigned(
            transaction_id=TRANSACTION_ID,
            classification_revision=revision,
            lines=lines,
        )


def test_reporting_clear_is_an_explicit_revision_without_an_empty_lines_field() -> None:
    event = ReportingLinesCleared(
        transaction_id=TRANSACTION_ID,
        classification_revision=4,
    )

    assert event.model_dump(mode="json") == {
        "transaction_id": str(TRANSACTION_ID),
        "classification_revision": 4,
    }
    with pytest.raises(ValidationError):
        ReportingLinesCleared(
            transaction_id=TRANSACTION_ID,
            classification_revision=4,
            lines=(),
        )


def test_investment_acquisition_serializes_exact_lot_facts() -> None:
    event = InvestmentLotAcquired(
        transaction_id=TRANSACTION_ID,
        lot_id=LOT_ID,
        instrument_asset_code="BTC",
        settlement_asset_code="CNY",
        quantity_units="100000000",
        cost_units="70000",
        fee_units="10",
    )

    assert event.model_dump(mode="json") == {
        "transaction_id": str(TRANSACTION_ID),
        "lot_id": str(LOT_ID),
        "instrument_asset_code": "BTC",
        "settlement_asset_code": "CNY",
        "quantity_units": "100000000",
        "cost_units": "70000",
        "fee_units": "10",
    }


def _allocation(
    *,
    allocation_id: UUID = ALLOCATION_ID_1,
    lot_id: UUID = LOT_ID,
    position: int = 0,
    quantity_units: str = "60",
    cost_units: str = "42",
) -> LotDisposalAllocation:
    return LotDisposalAllocation(
        allocation_id=allocation_id,
        lot_id=lot_id,
        position=position,
        quantity_units=quantity_units,
        cost_units=cost_units,
    )


def _allocations() -> tuple[LotDisposalAllocation, LotDisposalAllocation]:
    return (
        _allocation(),
        _allocation(
            allocation_id=ALLOCATION_ID_2,
            lot_id=LOT_ID_2,
            position=1,
            quantity_units="40",
            cost_units="28",
        ),
    )


def test_disposal_freezes_total_cost_basis_from_the_final_allocations() -> None:
    event = InvestmentLotDisposed(
        transaction_id=TRANSACTION_ID,
        instrument_asset_code="BTC",
        settlement_asset_code="CNY",
        quantity_units="100",
        proceeds_units="80",
        cost_basis_units="70",
        fee_units="2",
        allocations=_allocations(),
    )

    assert event.model_dump(mode="json")["cost_basis_units"] == "70"

    with pytest.raises(ValidationError, match="cost basis"):
        InvestmentLotDisposed(
            transaction_id=TRANSACTION_ID,
            instrument_asset_code="BTC",
            settlement_asset_code="CNY",
            quantity_units="100",
            proceeds_units="80",
            cost_basis_units="69",
            fee_units="2",
            allocations=_allocations(),
        )

    with pytest.raises(ValidationError):
        InvestmentLotDisposed(
            transaction_id=TRANSACTION_ID,
            instrument_asset_code="BTC",
            settlement_asset_code="CNY",
            quantity_units="100",
            proceeds_units="80",
            fee_units="2",
            allocations=_allocations(),
        )


def test_disposal_freezes_the_final_ordered_lot_allocation_decision() -> None:
    event = InvestmentLotDisposed(
        transaction_id=TRANSACTION_ID,
        instrument_asset_code="BTC",
        settlement_asset_code="CNY",
        quantity_units="100",
        proceeds_units="80",
        cost_basis_units="70",
        fee_units="2",
        allocations=_allocations(),
    )

    dumped = event.model_dump(mode="json")
    assert dumped["allocations"] == [
        allocation.model_dump(mode="json") for allocation in _allocations()
    ]
    assert isinstance(event.allocations, tuple)


@pytest.mark.parametrize(
    "allocations",
    [
        (),
        (_allocation(), _allocation(position=1)),
        (_allocation(), _allocation(allocation_id=ALLOCATION_ID_2, position=1)),
        (
            _allocation(),
            _allocation(
                allocation_id=ALLOCATION_ID_2,
                lot_id=LOT_ID_2,
                position=2,
            ),
        ),
        (
            _allocation(quantity_units="59"),
            _allocation(
                allocation_id=ALLOCATION_ID_2,
                lot_id=LOT_ID_2,
                position=1,
                quantity_units="40",
            ),
        ),
    ],
    ids=["empty", "duplicate-allocation-id", "duplicate-lot-id", "gap", "quantity"],
)
def test_disposal_rejects_incomplete_or_ambiguous_allocation_sets(
    allocations: tuple[LotDisposalAllocation, ...],
) -> None:
    with pytest.raises(ValidationError):
        InvestmentLotDisposed(
            transaction_id=TRANSACTION_ID,
            instrument_asset_code="BTC",
            settlement_asset_code="CNY",
            quantity_units="100",
            proceeds_units="80",
            cost_basis_units="70",
            fee_units=None,
            allocations=allocations,
        )
