from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from track_anywhere.api import app
from track_anywhere.posting_semantics import (
    POSTING_SEMANTICS_REVIEW_DECISION_FORBIDDEN_FIELDS,
    POSTING_SEMANTICS_REVIEW_DECISION_INPUT_FIELDS,
    POSTING_SEMANTICS_REVIEW_RECOMMENDATION_READ_ONLY_FIELDS,
    PUBLIC_WRITE_FORBIDDEN_POSTING_FIELDS,
    canonical_posting_semantics_metadata,
)


def test_public_api_v1_route_snapshot():
    assert app is not None
    actual = {
        "paths": {
            path: sorted(method for method in details if method in {"get", "post", "put", "patch", "delete"})
            for path, details in sorted(app.openapi()["paths"].items())
        }
    }
    expected = json.loads((Path(__file__).parent / "snapshots" / "public-api-v1.json").read_text())

    assert actual == expected


def test_public_mutation_routes_expose_request_schemas():
    assert app is not None
    openapi = app.openapi()

    schema_refs = {}
    for path, details in openapi["paths"].items():
        if path in {"/api/v1/attachments", "/api/v1/auth/dev-token", "/api/v1/auth/logout", "/api/v1/session/dev-local"}:
            continue
        for method in ("post", "patch"):
            if method in details:
                schema_refs[path] = details[method]["requestBody"]["content"]["application/json"]["schema"].get("$ref")

    assert schema_refs["/api/v1/accounts"].endswith("/CreateAccountCommand")
    assert schema_refs["/api/v1/drafts/capture"].endswith("/CaptureDraftCommand")
    assert schema_refs["/api/v1/ledger/transactions"].endswith("/RecordTransactionCommand")
    assert schema_refs["/api/v1/payment-instruments"].endswith("/CreatePaymentInstrumentCommand")
    assert schema_refs["/api/v1/payment-profiles"].endswith("/CreatePaymentProfileCommand")
    assert schema_refs["/api/v1/payment-profiles/{payment}/expenses"].endswith("/RecordPaymentProfileExpenseBody")
    assert schema_refs["/api/v1/recurring/items"].endswith("/CreateRecurringItemCommand")
    assert schema_refs["/api/v1/recurring/items/{recurring_id}"].endswith("/UpdateRecurringItemCommand")
    assert schema_refs["/api/v1/recurring/drafts"].endswith("/GenerateRecurringDraftsCommand")
    assert schema_refs["/api/v1/system/posting-semantics-rewrite"].endswith("/PostingSemanticsRewriteCommand")
    assert schema_refs["/api/v1/system/posting-semantics-review-resolutions"].endswith("/PostingSemanticsReviewResolutionsCommand")
    assert schema_refs["/api/v1/credentials/revoke"].endswith("/RevokeCredentialCommand")
    assert all(ref and not ref.endswith("/dict_str__Any_") for ref in schema_refs.values())


def test_public_openapi_describes_debit_credit_command_semantics():
    assert app is not None
    schemas = app.openapi()["components"]["schemas"]
    capture_properties = schemas["CaptureDraftCommand"]["properties"]
    transaction_properties = schemas["RecordTransactionCommand"]["properties"]
    expense_properties = schemas["RecordExpenseCommand"]["properties"]
    income_properties = schemas["RecordIncomeCommand"]["properties"]
    recurring_create_properties = schemas["CreateRecurringItemCommand"]["properties"]
    recurring_update_properties = schemas["UpdateRecurringItemCommand"]["properties"]
    payment_profile_expense_properties = schemas["RecordPaymentProfileExpenseBody"]["properties"]
    fx_properties = schemas["RecordFxExchangeCommand"]["properties"]
    investment_properties = schemas["RecordInvestmentEventCommand"]["properties"]
    fund_allocation_properties = schemas["FundAllocationCommand"]["properties"]
    fund_spend_properties = schemas["FundSpendCommand"]["properties"]

    assert "Positive draft business amount" in capture_properties["amount"]["description"]
    assert "confirmed postings use positive debit/credit rows" in capture_properties["amount"]["description"]
    assert "Positive business transfer amount" in transaction_properties["amount"]["description"]
    assert "persisted postings use positive debit/credit rows" in transaction_properties["amount"]["description"]
    assert "source is credited" in transaction_properties["from_account_id"]["description"]
    assert "asset-to-credit-card-liability transfers are repayments" in transaction_properties["to_account_id"]["description"]
    assert "decrease outstanding debt" in transaction_properties["to_account_id"]["description"]

    assert "Positive expense amount" in expense_properties["amount"]["description"]
    assert "persisted postings use positive debit/credit rows" in expense_properties["amount"]["description"]
    assert "credit-card liability account" in expense_properties["from_account_id"]["description"]
    assert "increases outstanding debt" in expense_properties["from_account_id"]["description"]
    assert "Positive income amount" in income_properties["amount"]["description"]
    assert "persisted postings use positive debit/credit rows" in income_properties["amount"]["description"]
    assert "Positive recurring item amount" in recurring_create_properties["amount"]["description"]
    assert "generated postings use positive debit/credit rows" in recurring_create_properties["amount"]["description"]
    assert "Positive recurring item amount" in recurring_update_properties["amount"]["description"]
    assert "generated postings use positive debit/credit rows" in recurring_update_properties["amount"]["description"]

    assert "Positive payment-profile expense amount" in payment_profile_expense_properties["amount"]["description"]
    assert "persisted postings use positive debit/credit rows" in payment_profile_expense_properties["amount"]["description"]
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


def test_public_openapi_exposes_posting_semantics_contract_extensions():
    assert app is not None
    schemas = app.openapi()["components"]["schemas"]
    expected = {
        **canonical_posting_semantics_metadata(),
        "scope": "public_write_raw_posting_input_guard",
        "request_schema_rule": (
            "public write schemas reject raw posting internals; this guard does not mean every command writes postings"
        ),
        "forbidden_input_fields": list(PUBLIC_WRITE_FORBIDDEN_POSTING_FIELDS),
    }

    posting_writer_paths = [
        "/api/v1/accounts",
        "/api/v1/books/{book_id}/accounts",
        "/api/v1/books/{book_id}/recurring/drafts",
        "/api/v1/books/{book_id}/recurring/items",
        "/api/v1/books/{book_id}/transactions",
        "/api/v1/books/{book_id}/transactions/{transaction_id}/reverse",
        "/api/v1/drafts/capture",
        "/api/v1/drafts/confirm",
        "/api/v1/drafts/supersede",
        "/api/v1/expenses",
        "/api/v1/funds/allocate",
        "/api/v1/funds/spend",
        "/api/v1/incomes",
        "/api/v1/investments/events",
        "/api/v1/ledger/adjustments",
        "/api/v1/ledger/fx-exchanges",
        "/api/v1/ledger/reverse",
        "/api/v1/ledger/transactions",
        "/api/v1/payment-profiles/{payment}/expenses",
        "/api/v1/recurring/drafts",
        "/api/v1/recurring/items",
        "/api/v1/recurring/items/{recurring_id}",
    ]
    paths = app.openapi()["paths"]
    for path in posting_writer_paths:
        method = "patch" if "patch" in paths[path] else "post"
        ref = paths[path][method]["requestBody"]["content"]["application/json"]["schema"]["$ref"]
        schema_name = ref.rsplit("/", 1)[-1]
        assert schemas[schema_name]["x-posting-semantics"] == expected

    assert schemas["CreateBudgetCommand"]["x-posting-semantics"] == expected

    assert schemas["PostingSemanticsReviewDecisionCommand"]["x-posting-semantics"] == {
        **canonical_posting_semantics_metadata(),
        "scope": "posting_semantics_review_decision_input_guard",
        "allowed_input_fields": list(POSTING_SEMANTICS_REVIEW_DECISION_INPUT_FIELDS),
        "recommendation_read_only_fields": list(POSTING_SEMANTICS_REVIEW_RECOMMENDATION_READ_ONLY_FIELDS),
        "forbidden_input_fields": list(POSTING_SEMANTICS_REVIEW_DECISION_FORBIDDEN_FIELDS),
    }


def test_public_openapi_distinguishes_non_posting_amount_fields():
    assert app is not None
    schemas = app.openapi()["components"]["schemas"]
    budget_properties = schemas["CreateBudgetCommand"]["properties"]
    budget_target_properties = schemas["CreateBudgetTargetCommand"]["properties"]
    payment_profile_properties = schemas["CreatePaymentProfileCommand"]["properties"]
    investment_event_properties = schemas["RecordInvestmentEventCommand"]["properties"]
    investment_valuation_properties = schemas["RecordInvestmentValuationCommand"]["properties"]

    assert "not a ledger posting amount" in budget_properties["total_amount"]["description"]
    assert "not a ledger posting amount" in budget_target_properties["amount"]["description"]
    assert "not a ledger posting amount" in payment_profile_properties["settlement_rate"]["description"]
    assert "not a ledger posting amount" in investment_event_properties["units"]["description"]
    assert "not a ledger posting amount" in investment_event_properties["nav"]["description"]
    assert "valuation snapshot, not a ledger posting amount" in investment_valuation_properties["value"]["description"]
