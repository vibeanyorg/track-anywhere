from __future__ import annotations

import pytest
from pydantic import ValidationError as PydanticValidationError

from track_anywhere.commands import (
    BalanceAdjustmentCommand,
    CaptureDraftCommand,
    ConfirmDraftCommand,
    CreateAccountCommand,
    CreateRecurringItemCommand,
    FundAllocationCommand,
    FundSpendCommand,
    GenerateRecurringDraftsCommand,
    PostingSemanticsReviewResolutionsCommand,
    RecordInvestmentEventCommand,
    RecordExpenseCommand,
    RecordIncomeCommand,
    RecordTransactionCommand,
    ReverseTransactionCommand,
    SupersedeDraftCommand,
    UpdateCreditCardProfileCommand,
    UpdateRecurringItemCommand,
)
from track_anywhere.domain_commands import (
    RecordFxExchangeCommand,
    RecordPaymentProfileExpenseCommand,
    ReverseBookTransactionCommand,
)
from track_anywhere.errors import ValidationError
from track_anywhere.posting_semantics import PUBLIC_WRITE_FORBIDDEN_POSTING_FIELDS
from track_anywhere.service_system import _auto_rewrite_candidates, _normalize_posting_semantics_review_decision
from track_anywhere.storage_system import _debit_credit_side, _positive_decimal_string


@pytest.mark.parametrize(
    "command_cls,payload",
    [
        (
            RecordTransactionCommand,
            {
                "amount": "10",
                "currency": "USD",
                "from_account_id": "acc_cash",
                "to_account_id": "acc_card",
                "purpose": "payment",
            },
        ),
        (
            RecordExpenseCommand,
            {
                "amount": "10",
                "currency": "USD",
                "from_account_id": "acc_card",
                "category_id": "cat_food",
                "purpose": "food",
            },
        ),
        (
            RecordIncomeCommand,
            {
                "amount": "10",
                "currency": "USD",
                "to_account_id": "acc_cash",
                "category_id": "cat_salary",
                "purpose": "salary",
            },
        ),
        (
            BalanceAdjustmentCommand,
            {
                "account_id": "acc_cash",
                "amount": "10",
                "currency": "USD",
                "purpose": "adjustment",
            },
        ),
        (
            CreateAccountCommand,
            {
                "name": "Cash",
                "type": "asset",
                "currency": "USD",
                "opening_balance": "10",
            },
        ),
        (
            CaptureDraftCommand,
            {
                "memo": "draft lunch",
                "amount": "10",
                "currency": "USD",
                "source_account_id": "acc_cash",
                "expense_account_id": "acc_food",
            },
        ),
        (
            ConfirmDraftCommand,
            {
                "draft_id": "draft_1",
                "expected_version": 1,
            },
        ),
        (
            ReverseTransactionCommand,
            {
                "transaction_id": "txn_1",
                "memo": "reverse mistaken transaction",
            },
        ),
        (
            ReverseBookTransactionCommand,
            {
                "memo": "reverse mistaken book transaction",
            },
        ),
        (
            SupersedeDraftCommand,
            {
                "draft_id": "draft_1",
                "expected_version": 1,
                "replacement": {
                    "memo": "replacement lunch",
                    "amount": "10",
                    "currency": "USD",
                    "source_account_id": "acc_cash",
                    "expense_account_id": "acc_food",
                },
            },
        ),
        (
            RecordFxExchangeCommand,
            {
                "from_account_id": "acc_cny",
                "from_amount": "70",
                "from_currency": "CNY",
                "to_account_id": "acc_usd",
                "to_amount": "10",
                "to_currency": "USD",
            },
        ),
        (
            RecordInvestmentEventCommand,
            {
                "account_id": "acc_brokerage",
                "event_type": "buy",
                "amount": "10",
                "currency": "USD",
            },
        ),
        (
            FundAllocationCommand,
            {
                "fund_id": "fund_1",
                "source_account_id": "acc_cash",
                "amount": "10",
                "currency": "USD",
                "expected_version": 1,
            },
        ),
        (
            FundSpendCommand,
            {
                "fund_id": "fund_1",
                "expense_account_id": "acc_food",
                "amount": "10",
                "currency": "USD",
                "expected_version": 1,
            },
        ),
        (
            CreateRecurringItemCommand,
            {
                "name": "Subscription",
                "kind": "paid",
                "amount": "10",
                "currency": "USD",
                "recurrence": {"type": "monthly_day", "day": 1},
                "reminder_days": [1],
                "anchor_date": "2026-01-01",
                "source_account_id": "acc_cash",
                "category_id": "cat_subscription",
            },
        ),
        (
            UpdateRecurringItemCommand,
            {
                "amount": "10",
                "currency": "USD",
            },
        ),
        (
            GenerateRecurringDraftsCommand,
            {},
        ),
        (
            RecordPaymentProfileExpenseCommand,
            {
                "payment": "virtual_card",
                "amount": "10",
                "currency": "USD",
                "category_id": "cat_food",
                "purpose": "food",
            },
        ),
    ],
)
def test_public_posting_commands_reject_raw_posting_semantics(command_cls, payload):
    for forbidden_field in PUBLIC_WRITE_FORBIDDEN_POSTING_FIELDS:
        with pytest.raises(PydanticValidationError):
            command_cls.model_validate({**payload, forbidden_field: "not allowed"})


def test_natural_balance_command_fields_describe_liability_semantics():
    create_properties = CreateAccountCommand.model_json_schema()["properties"]
    adjustment_properties = BalanceAdjustmentCommand.model_json_schema()["properties"]

    assert "positive means initial debt" in create_properties["opening_balance"]["description"]
    assert "persisted postings still use debit/credit" in create_properties["opening_balance"]["description"]
    assert "positive increases debt" in adjustment_properties["amount"]["description"]
    assert "persisted postings still use debit/credit" in adjustment_properties["amount"]["description"]


def test_credit_card_flow_command_fields_describe_debit_credit_semantics():
    transaction_properties = RecordTransactionCommand.model_json_schema()["properties"]
    expense_properties = RecordExpenseCommand.model_json_schema()["properties"]

    assert "Positive business transfer amount" in transaction_properties["amount"]["description"]
    assert "persisted postings use positive debit/credit rows" in transaction_properties["amount"]["description"]
    assert "source is credited" in transaction_properties["from_account_id"]["description"]
    assert "increases the liability balance" in transaction_properties["from_account_id"]["description"]
    assert "asset-to-credit-card-liability transfers are repayments" in transaction_properties["to_account_id"]["description"]
    assert "decrease outstanding debt" in transaction_properties["to_account_id"]["description"]

    assert "Positive expense amount" in expense_properties["amount"]["description"]
    assert "persisted postings use positive debit/credit rows" in expense_properties["amount"]["description"]
    assert "credit-card liability account" in expense_properties["from_account_id"]["description"]
    assert "increases outstanding debt" in expense_properties["from_account_id"]["description"]


def test_api_only_posting_writer_amount_fields_describe_debit_credit_semantics():
    capture_properties = CaptureDraftCommand.model_json_schema()["properties"]
    income_properties = RecordIncomeCommand.model_json_schema()["properties"]
    recurring_create_properties = CreateRecurringItemCommand.model_json_schema()["properties"]
    recurring_update_properties = UpdateRecurringItemCommand.model_json_schema()["properties"]
    payment_profile_properties = RecordPaymentProfileExpenseCommand.model_json_schema()["properties"]
    fx_properties = RecordFxExchangeCommand.model_json_schema()["properties"]
    investment_properties = RecordInvestmentEventCommand.model_json_schema()["properties"]
    fund_allocation_properties = FundAllocationCommand.model_json_schema()["properties"]
    fund_spend_properties = FundSpendCommand.model_json_schema()["properties"]

    assert "Positive draft business amount" in capture_properties["amount"]["description"]
    assert "confirmed postings use positive debit/credit rows" in capture_properties["amount"]["description"]
    assert "Positive income amount" in income_properties["amount"]["description"]
    assert "persisted postings use positive debit/credit rows" in income_properties["amount"]["description"]
    assert "Positive recurring item amount" in recurring_create_properties["amount"]["description"]
    assert "generated postings use positive debit/credit rows" in recurring_create_properties["amount"]["description"]
    assert "Positive recurring item amount" in recurring_update_properties["amount"]["description"]
    assert "generated postings use positive debit/credit rows" in recurring_update_properties["amount"]["description"]
    assert "Positive payment-profile expense amount" in payment_profile_properties["amount"]["description"]
    assert "persisted postings use positive debit/credit rows" in payment_profile_properties["amount"]["description"]
    assert "Positive source-currency amount" in fx_properties["from_amount"]["description"]
    assert "persisted postings use positive debit/credit rows" in fx_properties["from_amount"]["description"]
    assert "Positive target-currency amount" in fx_properties["to_amount"]["description"]
    assert "persisted postings use positive debit/credit rows" in fx_properties["to_amount"]["description"]
    assert "Positive optional fee amount" in fx_properties["fee_amount"]["description"]
    assert "persisted postings use positive debit/credit rows" in fx_properties["fee_amount"]["description"]
    assert "Positive investment event amount" in investment_properties["amount"]["description"]
    assert "persisted postings use positive debit/credit rows" in investment_properties["amount"]["description"]
    assert "Positive fund allocation amount" in fund_allocation_properties["amount"]["description"]
    assert "persisted postings use positive debit/credit rows" in fund_allocation_properties["amount"]["description"]
    assert "Positive fund spend amount" in fund_spend_properties["amount"]["description"]
    assert "persisted postings use positive debit/credit rows" in fund_spend_properties["amount"]["description"]


def test_credit_card_profile_numeric_fields_are_not_ledger_posting_amounts():
    properties = UpdateCreditCardProfileCommand.model_json_schema()["properties"]

    assert "not a ledger posting amount" in properties["credit_limit"]["description"]
    assert "not a ledger posting amount" in properties["available_credit"]["description"]
    assert "not a ledger posting amount or natural liability balance" in properties["available_credit"]["description"]
    assert "derived_available_credit" in properties["available_credit"]["description"]
    assert "not automatically posted as a ledger expense" in properties["annual_fee"]["description"]


def test_supersede_draft_replacement_rejects_nested_raw_posting_semantics():
    payload = {
        "draft_id": "draft_1",
        "expected_version": 1,
        "replacement": {
            "memo": "replacement lunch",
            "amount": "10",
            "currency": "USD",
            "source_account_id": "acc_cash",
            "expense_account_id": "acc_food",
        },
    }

    for forbidden_field in PUBLIC_WRITE_FORBIDDEN_POSTING_FIELDS:
        with pytest.raises(PydanticValidationError):
            SupersedeDraftCommand.model_validate(
                {
                    **payload,
                    "replacement": {
                        **payload["replacement"],
                        forbidden_field: "not allowed",
                    },
                }
            )


def test_posting_semantics_review_decisions_validate_legacy_amount_and_row_reference():
    valid_decision = {
        "transaction_id": "txn_1",
        "position": 0,
        "account_id": "acc_card",
        "currency": "USD",
        "legacy_amount": "-9.36",
        "action": "confirm_as_outstanding_liability",
    }

    command = PostingSemanticsReviewResolutionsCommand.model_validate({"decisions": [valid_decision]})
    assert command.decisions[0].legacy_amount == "-9.36"
    with pytest.raises(PydanticValidationError):
        PostingSemanticsReviewResolutionsCommand.model_validate(
            {"decisions": [{**valid_decision, "legacy_amount": "not-money"}]}
        )
    with pytest.raises(PydanticValidationError):
        PostingSemanticsReviewResolutionsCommand.model_validate(
            {"decisions": [{**valid_decision, "legacy_amount": "0"}]}
        )
    missing_reference = dict(valid_decision)
    missing_reference.pop("transaction_id")
    with pytest.raises(PydanticValidationError):
        PostingSemanticsReviewResolutionsCommand.model_validate({"decisions": [missing_reference]})
    missing_position = dict(valid_decision)
    missing_position.pop("position")
    with pytest.raises(PydanticValidationError):
        PostingSemanticsReviewResolutionsCommand.model_validate({"decisions": [missing_position]})
    with pytest.raises(PydanticValidationError):
        PostingSemanticsReviewResolutionsCommand.model_validate(
            {"decisions": [{**valid_decision, "position": True}]}
        )
    with pytest.raises(PydanticValidationError):
        PostingSemanticsReviewResolutionsCommand.model_validate(
            {"decisions": [{**valid_decision, "schema_version": "v1"}]}
        )
    conflicting_reference = {
        **valid_decision,
        "record_ref": "draft:txn_1",
        "transaction_id": "txn_1",
    }
    with pytest.raises(PydanticValidationError):
        PostingSemanticsReviewResolutionsCommand.model_validate({"decisions": [conflicting_reference]})


def test_posting_semantics_review_decisions_reject_raw_target_side():
    decision = {
        "transaction_id": "txn_1",
        "position": 0,
        "account_id": "acc_card",
        "currency": "USD",
        "legacy_amount": "-9.36",
        "action": "confirm_as_outstanding_liability",
        "target_side": "credit",
    }

    with pytest.raises(PydanticValidationError):
        PostingSemanticsReviewResolutionsCommand.model_validate({"decisions": [decision]})


def test_posting_semantics_service_normalization_rejects_raw_fields_and_conflicting_refs():
    decision = {
        "transaction_id": "txn_1",
        "position": 0,
        "account_id": "acc_card",
        "currency": "USD",
        "legacy_amount": "-9.36",
        "action": "confirm_as_outstanding_liability",
    }

    normalized = _normalize_posting_semantics_review_decision(decision)

    assert normalized["target_side"] == "credit"
    assert normalized["target_amount"] == "9.36"
    assert normalized["position"] == "0"
    with pytest.raises(ValidationError):
        _normalize_posting_semantics_review_decision({**decision, "target_side": "credit"})
    with pytest.raises(ValidationError, match="amount_semantics"):
        _normalize_posting_semantics_review_decision({**decision, "amount_semantics": "legacy_signed"})
    copied_recommendation = {
        **decision,
        "amount_semantics": "legacy_signed",
        "inferred_side_from_legacy_sign": "credit",
        "inferred_positive_amount": "9.36",
        "recommended_action": "manual_review_required_credit_card_semantics",
        "recommendation_reason": "choose whether this row represents charge or payment",
        "resolution_options": [
            {
                "action": "confirm_as_outstanding_liability",
                "target_side": "credit",
                "target_amount": "9.36",
            }
        ],
    }
    with pytest.raises(ValidationError, match="read-only recommendation.*amount_semantics"):
        _normalize_posting_semantics_review_decision(copied_recommendation)
    with pytest.raises(ValidationError):
        _normalize_posting_semantics_review_decision(
            {**decision, "record_ref": "draft:txn_1", "transaction_id": "txn_1"}
        )


def test_posting_semantics_storage_rewrite_guards_reject_invalid_targets():
    with pytest.raises(ValidationError, match="target_side must be debit or credit"):
        _debit_credit_side({"target_side": "increase"}, field="target_side", source="rewrite candidate")
    with pytest.raises(ValidationError, match="target_amount must be positive"):
        _positive_decimal_string({"target_amount": "-1"}, field="target_amount", source="rewrite candidate")
    with pytest.raises(ValidationError, match="target_amount must be a decimal string"):
        _positive_decimal_string({"target_amount": "not-money"}, field="target_amount", source="rewrite candidate")
    assert _positive_decimal_string({"target_amount": "+5"}, field="target_amount", source="rewrite candidate") == "5"


def test_auto_rewrite_candidates_reject_duplicate_rows_and_count_mismatch():
    candidate = {
        "transaction_id": "txn_1",
        "position": 0,
        "account_id": "acc_cash",
        "currency": "USD",
        "legacy_amount": "-5",
        "target_side": "credit",
        "target_amount": "5",
    }

    with pytest.raises(ValidationError, match="candidate count mismatch"):
        _auto_rewrite_candidates(
            {
                "counts": {"auto_rewrite_candidates": 2},
                "auto_rewrite_candidates": [candidate],
            }
        )
    with pytest.raises(ValidationError, match="must not contain duplicate rows"):
        _auto_rewrite_candidates(
            {
                "counts": {"auto_rewrite_candidates": 2},
                "auto_rewrite_candidates": [candidate, dict(candidate)],
            }
        )
