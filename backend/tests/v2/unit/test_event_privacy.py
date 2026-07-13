from __future__ import annotations

from collections.abc import Mapping
from types import UnionType
from typing import Any, get_args, get_origin
from uuid import UUID

import pytest
from pydantic import BaseModel, ValidationError

from track_anywhere.domain.investments.events import (
    InvestmentLotAcquired,
    InvestmentLotDisposed,
    LotDisposalAllocation,
)
from track_anywhere.domain.journal.events import (
    FinancialExternalReference,
    FinancialExternalReferenceCorrected,
    JournalPostingFact,
    JournalTransactionPosted,
    JournalTransactionReversed,
)
from track_anywhere.domain.journal.models import PostingSide, TransactionKind
from track_anywhere.domain.privacy import FORBIDDEN_EVENT_FIELD_NAMES
from track_anywhere.domain.reporting.events import (
    ReportingLine,
    ReportingLinesAssigned,
    ReportingLinesCleared,
)


ALL_EVENT_MODELS = (
    JournalTransactionPosted,
    JournalTransactionReversed,
    FinancialExternalReferenceCorrected,
    ReportingLinesAssigned,
    ReportingLinesCleared,
    InvestmentLotAcquired,
    InvestmentLotDisposed,
)
ALL_NESTED_MODELS = (
    JournalPostingFact,
    FinancialExternalReference,
    ReportingLine,
    LotDisposalAllocation,
)
ALL_CONTRACT_MODELS = ALL_EVENT_MODELS + ALL_NESTED_MODELS


def _schema_property_names(value: object) -> set[str]:
    if isinstance(value, Mapping):
        names = set(value.get("properties", {}))
        for nested in value.values():
            names.update(_schema_property_names(nested))
        return names
    if isinstance(value, list):
        names: set[str] = set()
        for nested in value:
            names.update(_schema_property_names(nested))
        return names
    return set()


def _contains_mutable_container(annotation: object) -> bool:
    origin = get_origin(annotation)
    if origin in (dict, list, set):
        return True
    if origin in (UnionType,):
        return any(_contains_mutable_container(arg) for arg in get_args(annotation))
    return any(_contains_mutable_container(arg) for arg in get_args(annotation))


def _minimal_posted_event() -> JournalTransactionPosted:
    return JournalTransactionPosted(
        transaction_id=UUID("00000000-0000-4000-8000-000000000002"),
        kind=TransactionKind.STANDARD,
        postings=(
            JournalPostingFact(
                posting_id=UUID("00000000-0000-4000-8000-000000000003"),
                position=0,
                account_id=UUID("00000000-0000-4000-8000-000000000004"),
                asset_code="CNY",
                side=PostingSide.DEBIT,
                units="1",
            ),
            JournalPostingFact(
                posting_id=UUID("00000000-0000-4000-8000-000000000005"),
                position=1,
                account_id=UUID("00000000-0000-4000-8000-000000000006"),
                asset_code="CNY",
                side=PostingSide.CREDIT,
                units="1",
            ),
        ),
    )


def test_every_event_and_nested_contract_is_frozen_and_forbids_extra_fields() -> None:
    for model in ALL_CONTRACT_MODELS:
        assert issubclass(model, BaseModel)
        assert model.model_config.get("frozen") is True
        assert model.model_config.get("extra") == "forbid"
        assert model.model_config.get("hide_input_in_errors") is True

    event = _minimal_posted_event()
    with pytest.raises(ValidationError, match="frozen"):
        event.transaction_id = UUID("00000000-0000-4000-8000-000000000099")
    with pytest.raises(ValidationError, match="frozen"):
        event.postings[0].units = "2"


def test_contract_annotations_never_expose_mutable_or_arbitrary_payload_containers() -> (
    None
):
    for model in ALL_CONTRACT_MODELS:
        for field in model.model_fields.values():
            assert not _contains_mutable_container(field.annotation), (
                model.__name__,
                field.annotation,
            )
            assert field.annotation not in (Any, object)


def test_every_event_json_schema_is_pii_minimized_recursively() -> None:
    for model in ALL_EVENT_MODELS:
        schema = model.model_json_schema()
        field_names = {name.casefold() for name in _schema_property_names(schema)}
        assert field_names.isdisjoint(FORBIDDEN_EVENT_FIELD_NAMES), model.__name__


@pytest.mark.parametrize(
    "forbidden_name",
    [
        "memo",
        "merchant_name",
        "attachment_content",
        "credential",
        "idempotency_key",
    ],
)
def test_raw_pii_secret_and_idempotency_fields_are_rejected(
    forbidden_name: str,
) -> None:
    payload = _minimal_posted_event().model_dump(mode="python")
    payload[forbidden_name] = "must-not-enter-the-ledger"

    with pytest.raises(
        ValidationError, match="Extra inputs are not permitted"
    ) as error:
        JournalTransactionPosted.model_validate(payload)
    assert "must-not-enter-the-ledger" not in str(error.value)


def test_privacy_vocabulary_covers_human_text_attachments_and_secrets() -> None:
    assert {
        "memo",
        "purpose",
        "merchant_name",
        "merchant_display_name",
        "attachment_name",
        "attachment_content",
        "attachment_bytes",
        "credential",
        "token",
        "secret",
        "idempotency_key",
    } <= FORBIDDEN_EVENT_FIELD_NAMES


def test_description_ref_is_the_only_contract_field_that_can_reference_sidecar_text() -> (
    None
):
    field_names = {name for model in ALL_CONTRACT_MODELS for name in model.model_fields}
    text_like_names = {
        name
        for name in field_names
        if any(word in name for word in ("description", "memo", "purpose", "merchant"))
    }

    assert text_like_names == {"description_ref"}
