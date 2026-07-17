from __future__ import annotations

from uuid import UUID

import pytest
from pydantic import ValidationError

from track_anywhere.application.imports.contracts import (
    FrozenFinancialHistoryPlan,
    PlannedAccount,
    PlannedAsset,
    PlannedCategory,
    PlannedCategoryVersion,
    PlannedLedgerEvent,
    PlannedProtectedContent,
    parse_canonical_plan_bytes,
)
from track_anywhere.application.privacy.service import ImportArchiveRecordCounts


SAFE_ID = UUID("00000000-0000-4000-8000-000000000001")


def test_planned_asset_is_strict_frozen_and_hides_catalog_text() -> None:
    asset = PlannedAsset(
        asset_code="TST",
        kind="synthetic",
        ledger_scale=2,
        input_scale=2,
        display_scale=2,
        current_name="fixture-name",
        status="active",
    )

    assert "fixture-name" not in repr(asset)
    with pytest.raises(ValidationError, match="frozen"):
        asset.ledger_scale = 3  # type: ignore[misc]
    with pytest.raises(ValidationError):
        PlannedAsset(
            asset_code="TST",
            kind="synthetic",
            ledger_scale="2",  # type: ignore[arg-type]
            input_scale=2,
            display_scale=2,
            current_name="fixture-name",
            status="active",
        )
    with pytest.raises(ValidationError):
        PlannedAsset(
            asset_code="TST",
            kind="synthetic",
            ledger_scale=2,
            input_scale=2,
            display_scale=2,
            current_name="fixture-name",
            status="active",
            unexpected=True,  # type: ignore[call-arg]
        )


def test_planned_protected_content_has_fixed_base64_json_and_safe_repr() -> None:
    content = PlannedProtectedContent(
        sidecar_id=SAFE_ID,
        kind="transaction_description",
        canonical_plaintext=b'{"fixture":"safe"}',
        record_counts=None,
    )

    encoded = content.model_dump_json()
    assert "eyJmaXh0dXJlIjoic2FmZSJ9" in encoded
    assert "fixture" not in repr(content)
    assert PlannedProtectedContent.model_validate_json(encoded) == content


def test_planned_catalog_contracts_hide_text_and_financial_expectations() -> None:
    account = PlannedAccount(
        account_id=SAFE_ID,
        asset_code="TST",
        account_type="asset",
        account_subtype=None,
        system_role=None,
        current_name="fixture-account",
        status="active",
        expected_natural_units=123,
        close_after_import=False,
    )
    version = PlannedCategoryVersion(
        category_version_id=UUID("00000000-0000-4000-8000-000000000003"),
        parent_category_id=None,
        name="fixture-category",
        status="active",
        change_reason_code="frozen_import",
    )
    category = PlannedCategory(
        category_id=UUID("00000000-0000-4000-8000-000000000002"),
        parent_category_id=None,
        current_name="fixture-category",
        current_version_id=version.category_version_id,
        status="active",
        version=version,
    )

    rendered = repr((account, category))
    assert "fixture-account" not in rendered
    assert "fixture-category" not in rendered
    assert "123" not in rendered
    assert category.version.category_version_id == category.current_version_id


def test_financial_history_plan_rejects_noncanonical_fixed_counts() -> None:
    archive = PlannedProtectedContent(
        sidecar_id=SAFE_ID,
        kind="import_archive",
        canonical_plaintext=b'{"record_type":"fixture"}\n',
        record_counts=ImportArchiveRecordCounts(
            classification_audit_records=0,
            investment_activities=0,
            investment_valuations=0,
            uncategorized_fx_reporting_facts=0,
            institution_metadata_records=0,
            counterparty_records=0,
            omission_records=0,
        ),
    )

    with pytest.raises(ValidationError, match="fixed plan counts") as captured:
        FrozenFinancialHistoryPlan(
            contract_version=1,
            target_book_id=SAFE_ID,
            source_dump_hash="a" * 64,
            manifest_hash="b" * 64,
            card_review_hash="c" * 64,
            assets=(),
            accounts=(),
            categories=(),
            descriptions=(),
            archive=archive,
            events=(),
            expected_terminal_hash="0" * 64,
            quarantine_count=0,
        )
    assert "fixture" not in str(captured.value)


@pytest.mark.parametrize(
    "raw",
    (
        b'{"contract_version":1,"contract_version":1}',
        b'{"contract_version":NaN}',
    ),
)
def test_plan_parser_rejects_duplicate_and_nonfinite_json(raw: bytes) -> None:
    with pytest.raises(ValueError, match="canonical financial history plan"):
        parse_canonical_plan_bytes(raw)


def test_plan_contract_rejects_duplicate_catalog_and_event_identities() -> None:
    from track_anywhere.application.imports.event_compiler import (
        compile_current_v2_events,
    )

    from backend.tests.v2.imports.test_plan_topology import IDS, _pending

    asset = PlannedAsset(
        asset_code="TST",
        kind="synthetic",
        ledger_scale=2,
        input_scale=2,
        display_scale=2,
        current_name="fixture-name",
        status="active",
    )
    account = PlannedAccount(
        account_id=IDS[4],
        asset_code="TST",
        account_type="asset",
        account_subtype=None,
        system_role=None,
        current_name="fixture-account",
        status="active",
        expected_natural_units=0,
        close_after_import=False,
    )
    version = PlannedCategoryVersion(
        category_version_id=IDS[15],
        parent_category_id=None,
        name="fixture-category",
        status="active",
        change_reason_code="frozen_import",
    )
    category = PlannedCategory(
        category_id=IDS[14],
        parent_category_id=None,
        current_name="fixture-category",
        current_version_id=version.category_version_id,
        status="active",
        version=version,
    )
    description = PlannedProtectedContent(
        sidecar_id=IDS[7],
        kind="transaction_description",
        canonical_plaintext=b'{"fixture":"safe"}',
        record_counts=None,
    )
    archive = PlannedProtectedContent(
        sidecar_id=IDS[16],
        kind="import_archive",
        canonical_plaintext=b'{"record_type":"fixture"}\n',
        record_counts=ImportArchiveRecordCounts(
            classification_audit_records=43,
            investment_activities=6,
            investment_valuations=0,
            uncategorized_fx_reporting_facts=5,
            institution_metadata_records=117,
            counterparty_records=2,
            omission_records=5,
        ),
    )
    event: PlannedLedgerEvent = compile_current_v2_events(
        target_book_id=IDS[0], events=(_pending(),)
    )[0]

    with pytest.raises(ValidationError, match="duplicate plan identity"):
        FrozenFinancialHistoryPlan(
            contract_version=1,
            target_book_id=IDS[0],
            source_dump_hash="a" * 64,
            manifest_hash="b" * 64,
            card_review_hash="c" * 64,
            assets=(asset,) * 20,
            accounts=(account,) * 121,
            categories=(category,) * 37,
            descriptions=(description,) * 138,
            archive=archive,
            events=(event,) * 176,
            expected_terminal_hash=event.event_hash,
            quarantine_count=0,
        )
