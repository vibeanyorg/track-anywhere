from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import TypeAdapter, ValidationError

from track_anywhere.application.entries import (
    AccountRef,
    AdjustmentEntryInput,
    CategoryRef,
    CommitEntryInput,
    ExpenseEntryInput,
    MoneyInput,
    PreparedEntry,
)
from track_anywhere.application.entries.contracts import (
    CreditCardPaymentEntryInput,
    EverydayEntryInput,
    IncomeEntryInput,
    RefundEntryInput,
    TransferEntryInput,
)
from track_anywhere.application.entries.errors import (
    EntryErrorCode,
    EntryGatewayError,
)


FIXTURE_PATH = (
    Path(__file__).resolve().parents[1] / "fixtures/everyday_entry_contracts.json"
)
ENTRY_ADAPTER = TypeAdapter(EverydayEntryInput)


def _fixtures() -> dict[str, object]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def test_canonical_entry_fixtures_cover_all_discriminated_variants() -> None:
    fixtures = _fixtures()
    entries = [ENTRY_ADAPTER.validate_python(value) for value in fixtures["entries"]]

    assert [type(entry) for entry in entries] == [
        ExpenseEntryInput,
        IncomeEntryInput,
        TransferEntryInput,
        CreditCardPaymentEntryInput,
        RefundEntryInput,
        AdjustmentEntryInput,
    ]
    assert [entry.kind for entry in entries] == [
        "expense",
        "income",
        "transfer",
        "credit_card_payment",
        "refund",
        "adjustment",
    ]


def test_money_keeps_exact_source_value_and_explicit_denomination() -> None:
    fixtures = _fixtures()
    major = ENTRY_ADAPTER.validate_python(fixtures["entries"][0]).amount
    minor = ENTRY_ADAPTER.validate_python(fixtures["minor_unit_expense"]).amount

    assert major.value == "53"
    assert major.denomination.value == "asset_unit"
    assert minor.value == "660"
    assert minor.denomination.value == "minor_unit"
    assert minor.source_text == "660分"
    assert "units" not in minor.model_dump()


@pytest.mark.parametrize("value", [0, 660.0, "0", "-1", "1e2", " 1", "1."])
def test_money_rejects_floats_zero_and_non_plain_decimal_values(
    value: object,
) -> None:
    with pytest.raises(ValidationError):
        MoneyInput(
            value=value,  # type: ignore[arg-type]
            denomination="asset_unit",
            asset_code="CNY",
            source_text="source",
        )


def test_adjustment_balance_accepts_exact_zero_without_weakening_amounts() -> None:
    adjustment = AdjustmentEntryInput(
        account=AccountRef(query="微信零钱通"),
        actual_balance={
            "value": "0",
            "denomination": "asset_unit",
            "asset_code": "CNY",
            "source_text": "余额为0",
        },
        occurred_at=datetime(2026, 7, 24, tzinfo=UTC),
    )

    assert adjustment.actual_balance.value == "0"


def test_account_and_category_refs_require_exactly_one_selector() -> None:
    with pytest.raises(ValidationError):
        AccountRef()
    with pytest.raises(ValidationError):
        AccountRef(
            account_id=UUID("00000000-0000-4000-8000-000000000001"),
            query="same account",
        )
    with pytest.raises(ValidationError):
        AccountRef(
            account_id=UUID("00000000-0000-4000-8000-000000000001"),
            last4="0001",
        )
    with pytest.raises(ValidationError):
        CategoryRef()
    with pytest.raises(ValidationError):
        CategoryRef(path=("食品", "外卖"), query="外卖")

    assert AccountRef(query="  工商银行  ").query == "工商银行"
    assert CategoryRef(path=(" 食品 ", " 外卖 ")).path == ("食品", "外卖")


def test_semantic_inputs_forbid_accounting_and_cross_dimension_fields() -> None:
    raw = _fixtures()["entries"][0]
    assert isinstance(raw, dict)

    for forbidden_field, value in (
        ("debit", "account"),
        ("credit", "account"),
        ("posting_id", "00000000-0000-4000-8000-000000000001"),
        ("units", "5300"),
        ("expense_account_id", "00000000-0000-4000-8000-000000000001"),
    ):
        with pytest.raises(ValidationError):
            ENTRY_ADAPTER.validate_python({**raw, forbidden_field: value})


def test_transfer_and_card_payment_forbid_identical_account_refs() -> None:
    amount = MoneyInput(
        value="10",
        denomination="asset_unit",
        asset_code="CNY",
        source_text="10",
    )
    account = AccountRef(query="same")

    with pytest.raises(ValidationError):
        TransferEntryInput(
            amount=amount,
            source_account=account,
            destination_account=account,
            occurred_at=datetime(2026, 7, 24, tzinfo=UTC),
        )
    with pytest.raises(ValidationError):
        CreditCardPaymentEntryInput(
            amount=amount,
            funding_account=account,
            card_account=account,
            occurred_at=datetime(2026, 7, 24, tzinfo=UTC),
        )


def test_prepared_entry_fixture_is_frozen_and_hides_commit_token_from_repr() -> None:
    prepared = PreparedEntry.model_validate(_fixtures()["ready_prepared_entry"])

    assert prepared.status.value == "ready"
    assert prepared.resolved.source_account_id == UUID(
        "00000000-0000-4000-8000-000000000052"
    )
    assert "opaque-commit-token" not in repr(prepared)
    with pytest.raises(ValidationError):
        prepared.status = "unsupported"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("status", "token", "clarifications"),
    [
        ("ready", None, []),
        (
            "needs_clarification",
            None,
            [],
        ),
        (
            "duplicate_suspected",
            "opaque-commit-token-with-at-least-32-characters",
            [
                {
                    "code": "duplicate_confirmation",
                    "field": "duplicate",
                    "prompt": "Possible duplicate",
                    "choices": [],
                }
            ],
        ),
        (
            "unsupported",
            "opaque-commit-token-with-at-least-32-characters",
            [],
        ),
    ],
)
def test_prepared_status_controls_commit_token_and_clarifications(
    status: str,
    token: str | None,
    clarifications: list[dict[str, object]],
) -> None:
    raw = _fixtures()["ready_prepared_entry"]
    assert isinstance(raw, dict)

    with pytest.raises(ValidationError):
        PreparedEntry.model_validate(
            {
                **raw,
                "status": status,
                "commit_token": token,
                "clarifications": clarifications,
            }
        )


def test_commit_contract_contains_only_opaque_intent_token_and_request_id() -> None:
    commit = CommitEntryInput.model_validate(_fixtures()["commit"])

    assert set(commit.model_dump()) == {"intent_id", "commit_token", "request_id"}
    assert "opaque-commit-token" not in repr(commit)
    with pytest.raises(ValidationError):
        CommitEntryInput.model_validate(
            {
                **_fixtures()["commit"],
                "amount": "53",
            }
        )


def test_gateway_errors_expose_stable_codes_without_dynamic_payloads() -> None:
    assert EntryErrorCode.INTENT_EXPIRED.value == "entry_intent_expired"
    assert len({code.value for code in EntryErrorCode}) == len(EntryErrorCode)

    error = EntryGatewayError(
        EntryErrorCode.ACCOUNT_AMBIGUOUS,
        "account selection is ambiguous",
        field="source_account",
    )
    assert error.code is EntryErrorCode.ACCOUNT_AMBIGUOUS
    assert error.field == "source_account"
    assert error.retryable is False
    assert str(error) == "account selection is ambiguous"
