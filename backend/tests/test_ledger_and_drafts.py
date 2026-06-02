from __future__ import annotations

import sqlite3
from decimal import Decimal

import pytest

from track_anywhere.balance_semantics import (
    ACCOUNT_TYPE_BALANCE_SEMANTICS,
    balance_delta_semantics_for_account_type,
    liability_balance_view,
)
from track_anywhere.errors import IdempotencyConflict, StaleVersion, ValidationError
from track_anywhere.ledger import Posting, credit_posting, debit_posting, legacy_signed_posting
from track_anywhere.security import DeploymentSecurityConfig
from track_anywhere.service import FinanceService
from track_anywhere.transaction_builder import build_transaction


def test_confirmed_transactions_must_balance():
    service = FinanceService()
    cash, _ = service.create_account(
        service.owner_token,
        {"name": "Cash", "type": "asset", "currency": "CNY"},
        idempotency_key="acc-cash",
    )
    expense, _ = service.create_account(
        service.owner_token,
        {"name": "Food", "type": "expense", "currency": "CNY"},
        idempotency_key="acc-food",
    )
    service.ledger.accounts[cash.account_id] = cash
    service.ledger.accounts[expense.account_id] = expense

    with pytest.raises(ValidationError):
        service.ledger.create_transaction(
            "bad",
            [
                legacy_signed_posting(cash.account_id, Decimal("-38"), "CNY"),
                legacy_signed_posting(expense.account_id, Decimal("37"), "CNY"),
            ],
            allow_legacy_signed=True,
        )


def test_raw_posting_defaults_to_debit_credit_and_requires_explicit_side():
    service = FinanceService()
    cash = service.ledger.create_account("Cash", "asset", "USD")
    expense = service.ledger.create_account("Food", "expense", "USD")

    with pytest.raises(ValidationError, match="debit/credit posting requires side"):
        service.ledger.create_transaction(
            "raw posting without side",
            [
                Posting(cash.account_id, Decimal("5"), "USD"),
                Posting(expense.account_id, Decimal("5"), "USD"),
            ],
        )


def test_ledger_create_transaction_rejects_new_legacy_signed_postings_by_default():
    service = FinanceService()
    cash = service.ledger.create_account("Cash", "asset", "USD")
    expense = service.ledger.create_account("Food", "expense", "USD")

    with pytest.raises(ValidationError, match="new confirmed transactions must use debit_credit semantics"):
        service.ledger.create_transaction(
            "legacy signed new write",
            [
                legacy_signed_posting(cash.account_id, Decimal("-5"), "USD"),
                legacy_signed_posting(expense.account_id, Decimal("5"), "USD"),
            ],
        )


def test_transaction_builder_rejects_new_legacy_signed_postings_by_default():
    service = FinanceService()
    cash = service.ledger.create_account("Cash", "asset", "USD")
    expense = service.ledger.create_account("Food", "expense", "USD")

    with pytest.raises(ValidationError, match="new confirmed transactions must use debit_credit semantics"):
        build_transaction(
            memo="legacy signed new write",
            postings=[
                legacy_signed_posting(cash.account_id, Decimal("-5"), "USD"),
                legacy_signed_posting(expense.account_id, Decimal("5"), "USD"),
            ],
            accounts=[cash, expense],
        )


def test_legacy_signed_reversal_is_rewritten_to_debit_credit():
    service = FinanceService()
    cash = service.ledger.create_account("Cash", "asset", "USD")
    expense = service.ledger.create_account("Food", "expense", "USD")
    transaction = service.ledger.create_transaction(
        "legacy expense",
        [
            legacy_signed_posting(cash.account_id, Decimal("-5"), "USD"),
            legacy_signed_posting(expense.account_id, Decimal("5"), "USD"),
        ],
        allow_legacy_signed=True,
    )

    reversal = service.ledger.reverse_transaction(transaction.transaction_id, "reverse legacy")

    assert [(posting.amount, posting.amount_semantics, posting.side) for posting in reversal.postings] == [
        (Decimal("5"), "debit_credit", "debit"),
        (Decimal("5"), "debit_credit", "credit"),
    ]


def test_debit_credit_reversal_rejects_missing_side_instead_of_guessing():
    service = FinanceService()
    cash = service.ledger.create_account("Cash", "asset", "USD")
    expense = service.ledger.create_account("Food", "expense", "USD")
    transaction = service.ledger.create_transaction(
        "expense",
        [
            credit_posting(cash.account_id, Decimal("5"), "USD"),
            debit_posting(expense.account_id, Decimal("5"), "USD"),
        ],
    )
    transaction.postings[0].side = None

    with pytest.raises(ValidationError, match="posting side must be debit or credit"):
        service.ledger.reverse_transaction(transaction.transaction_id, "reverse corrupted debit/credit")


def test_reversal_rejects_unknown_posting_semantics_instead_of_legacy_fallback():
    service = FinanceService()
    cash = service.ledger.create_account("Cash", "asset", "USD")
    expense = service.ledger.create_account("Food", "expense", "USD")
    transaction = service.ledger.create_transaction(
        "expense",
        [
            credit_posting(cash.account_id, Decimal("5"), "USD"),
            debit_posting(expense.account_id, Decimal("5"), "USD"),
        ],
    )
    transaction.postings[0].amount_semantics = "mystery"  # type: ignore[assignment]

    with pytest.raises(ValidationError, match="posting amount semantics must be legacy_signed or debit_credit"):
        service.ledger.reverse_transaction(transaction.transaction_id, "reverse corrupted semantics")


def test_draft_capture_confirm_and_balance_projection():
    service = FinanceService()
    cash, _ = service.create_account(
        service.owner_token,
        {"name": "Cash", "type": "asset", "currency": "CNY", "opening_balance": "100"},
        idempotency_key="acc-cash",
    )
    expense, _ = service.create_account(
        service.owner_token,
        {"name": "Food", "type": "expense", "currency": "CNY"},
        idempotency_key="acc-food",
    )
    draft, replay = service.capture_draft(
        service.owner_token,
        {
            "memo": "Spent 38 on coffee",
            "amount": "38",
            "currency": "CNY",
            "source_account_id": cash.account_id,
            "expense_account_id": expense.account_id,
            "confidence": 0.95,
        },
        idempotency_key="capture-coffee",
    )

    assert replay is False
    assert draft.state == "ready_to_confirm"
    projected = service.account_balance(service.owner_token, cash.account_id, include_drafts=True)
    assert projected["official_balance"]["amount"] == "100"
    assert projected["official_balance"]["amount_semantics"] == ACCOUNT_TYPE_BALANCE_SEMANTICS["asset"]
    assert projected["projected_balance"]["amount"] == "62"
    assert projected["projected_balance"]["amount_semantics"] == ACCOUNT_TYPE_BALANCE_SEMANTICS["asset"]
    assert projected["projected_balance"]["pending_impact"] == "-38"
    assert projected["projected_balance"]["pending_impact_semantics"] == balance_delta_semantics_for_account_type("asset")

    transaction, confirm_replay = service.confirm_draft(
        service.owner_token,
        {"draft_id": draft.draft_id, "expected_version": draft.version},
        idempotency_key="confirm-coffee",
    )

    assert confirm_replay is False
    assert transaction.transaction_id.startswith("txn_")
    assert service.account_balance(service.owner_token, cash.account_id)["official_balance"]["amount"] == "62"


def test_credit_card_draft_projection_exposes_liability_amount_semantics():
    service = FinanceService()
    token = service.owner_token
    card, _ = service.create_account(
        token,
        {"name": "Visa", "type": "liability", "currency": "USD", "subtype": "credit_card"},
        idempotency_key="acc-card-draft",
    )
    expense, _ = service.create_account(
        token,
        {"name": "Domains", "type": "expense", "currency": "USD"},
        idempotency_key="acc-domains-draft",
    )
    draft, _ = service.capture_draft(
        token,
        {
            "memo": "Domain renewal",
            "amount": "11.08",
            "currency": "USD",
            "source_account_id": card.account_id,
            "expense_account_id": expense.account_id,
            "confidence": 0.95,
        },
        idempotency_key="draft-card-domain",
    )

    projected = service.account_balance(token, card.account_id, include_drafts=True)

    assert projected["official_balance"]["amount"] == "0"
    assert projected["official_balance"]["amount_semantics"] == ACCOUNT_TYPE_BALANCE_SEMANTICS["liability"]
    assert projected["projected_balance"]["amount"] == "11.08"
    assert projected["projected_balance"]["amount_semantics"] == ACCOUNT_TYPE_BALANCE_SEMANTICS["liability"]
    assert projected["projected_balance"]["pending_impact"] == "11.08"
    assert projected["projected_balance"]["pending_impact_semantics"] == balance_delta_semantics_for_account_type("liability")
    assert projected["projected_balance"]["included_draft_ids"] == [draft.draft_id]
    assert projected["projected_liability_balance"] == liability_balance_view(Decimal("11.08"))


def test_fund_account_balance_uses_fund_natural_balance_semantics():
    service = FinanceService()
    fund, _ = service.create_account(
        service.owner_token,
        {"name": "Emergency Fund", "type": "fund", "currency": "CNY", "opening_balance": "100"},
        idempotency_key="acc-fund-balance-semantics",
    )

    balance = service.account_balance(service.owner_token, fund.account_id)

    assert balance["balance_semantics"] == ACCOUNT_TYPE_BALANCE_SEMANTICS["fund"]
    assert balance["official_balance"]["amount"] == "100"
    assert balance["official_balance"]["amount_semantics"] == ACCOUNT_TYPE_BALANCE_SEMANTICS["fund"]


def test_system_account_balance_uses_system_natural_balance_semantics():
    service = FinanceService()
    system_account, _ = service.create_account(
        service.owner_token,
        {"name": "FX Clearing", "type": "system", "currency": "USD24", "opening_balance": "0"},
        idempotency_key="acc-system-balance-semantics",
    )

    balance = service.account_balance(service.owner_token, system_account.account_id)

    assert balance["balance_semantics"] == ACCOUNT_TYPE_BALANCE_SEMANTICS["system"]
    assert balance["official_balance"]["amount"] == "0"
    assert balance["official_balance"]["amount_semantics"] == ACCOUNT_TYPE_BALANCE_SEMANTICS["system"]


def test_idempotent_capture_replay_and_conflict():
    service = FinanceService()
    first, replay = service.capture_draft(
        service.owner_token,
        {"memo": "quick note", "currency": "CNY"},
        idempotency_key="capture-1",
    )
    second, replayed = service.capture_draft(
        service.owner_token,
        {"memo": "quick note", "currency": "CNY"},
        idempotency_key="capture-1",
    )
    assert first.draft_id == second.draft_id
    assert replay is False
    assert replayed is True

    with pytest.raises(IdempotencyConflict):
        service.capture_draft(
            service.owner_token,
            {"memo": "different note", "currency": "CNY"},
            idempotency_key="capture-1",
        )


def test_stale_version_rejected_on_confirm():
    service = FinanceService()
    cash, _ = service.create_account(
        service.owner_token,
        {"name": "Cash", "type": "asset", "currency": "CNY"},
        idempotency_key="a1",
    )
    expense, _ = service.create_account(
        service.owner_token,
        {"name": "Food", "type": "expense", "currency": "CNY"},
        idempotency_key="a2",
    )
    draft, _ = service.capture_draft(
        service.owner_token,
        {
            "memo": "Spent 10",
            "amount": "10",
            "source_account_id": cash.account_id,
            "expense_account_id": expense.account_id,
        },
        idempotency_key="d1",
    )
    service.reject_draft(
        service.owner_token,
        {"draft_id": draft.draft_id, "expected_version": draft.version},
        idempotency_key="reject-before-stale-confirm",
    )
    with pytest.raises(StaleVersion):
        service.confirm_draft(
            service.owner_token,
            {"draft_id": draft.draft_id, "expected_version": 1},
            idempotency_key="c1",
        )


def test_agent_scope_and_audit_redaction():
    service = FinanceService()
    agent_token = service.issue_agent_credential(service.owner_token, {"capture:draft"})
    draft, _ = service.capture_draft(
        agent_token,
        {"memo": "agent draft", "currency": "CNY"},
        idempotency_key="agent-draft",
    )
    assert draft.source == "agent"

    last_event = service.audit.events[-1]
    assert last_event.actor_type == "agent"
    assert last_event.details["command"]["memo"] == "[REDACTED]"

    issue_event = service.audit.events[-2]
    assert issue_event.details == {"scopes": ["capture:draft"], "auth_kind": "api_key", "key_prefix": None}


def test_posting_currency_must_match_account_currency():
    service = FinanceService()
    cash = service.ledger.create_account("Cash", "asset", "CNY")
    food = service.ledger.create_account("Food", "expense", "USD")

    with pytest.raises(ValidationError):
        service.ledger.create_transaction(
            "bad cross-currency posting",
            [
                legacy_signed_posting(cash.account_id, Decimal("-10"), "USD"),
                legacy_signed_posting(food.account_id, Decimal("10"), "USD"),
            ],
            allow_legacy_signed=True,
        )


def test_record_transaction_rejects_cross_currency_accounts():
    service = FinanceService()
    cash, _ = service.create_account(
        service.owner_token,
        {"name": "Cash CNY", "type": "asset", "currency": "CNY"},
        idempotency_key="cross-currency-cash",
    )
    food, _ = service.create_account(
        service.owner_token,
        {"name": "Food USD", "type": "expense", "currency": "USD"},
        idempotency_key="cross-currency-food",
    )

    with pytest.raises(ValidationError):
        service.record_transaction(
            service.owner_token,
            {
                "amount": "10",
                "currency": "USD",
                "from_account_id": cash.account_id,
                "to_account_id": food.account_id,
                "purpose": "must fail",
            },
            idempotency_key="cross-currency-transaction",
        )


def test_expense_record_uses_debit_credit_for_credit_card_purchase():
    service = FinanceService()
    token = service.owner_token
    card, _ = service.create_account(
        token,
        {"name": "Visa", "type": "liability", "currency": "USD", "subtype": "credit_card"},
        idempotency_key="dc-card",
    )
    category, _ = service.create_category(
        token,
        {"kind": "expense", "name": "Domains"},
        idempotency_key="dc-domain-category",
    )

    transaction, replay = service.record_expense(
        token,
        {
            "from_account_id": card.account_id,
            "amount": "11.08",
            "currency": "USD",
            "category_id": category.category_id,
            "purpose": "domain renewal",
        },
        idempotency_key="dc-card-expense",
    )

    assert replay is False
    card_posting = next(posting for posting in transaction.postings if posting.account_id == card.account_id)
    expense_posting = next(posting for posting in transaction.postings if posting.account_id != card.account_id)
    assert (card_posting.side, card_posting.amount, card_posting.amount_semantics) == (
        "credit",
        Decimal("11.08"),
        "debit_credit",
    )
    assert (expense_posting.side, expense_posting.amount, expense_posting.amount_semantics) == (
        "debit",
        Decimal("11.08"),
        "debit_credit",
    )
    assert transaction.lines[0].amount == Decimal("11.08")
    card_balance = service.account_balance(token, card.account_id)
    assert card_balance["account_type"] == "liability"
    assert card_balance["balance_semantics"] == ACCOUNT_TYPE_BALANCE_SEMANTICS["liability"]
    assert card_balance["official_balance"]["amount"] == "11.08"
    assert card_balance["official_balance"]["amount_semantics"] == ACCOUNT_TYPE_BALANCE_SEMANTICS["liability"]
    assert card_balance["liability_balance"] == liability_balance_view(Decimal("11.08"))


def test_multiple_credit_card_expenses_accumulate_outstanding_liability():
    service = FinanceService()
    token = service.owner_token
    card, _ = service.create_account(
        token,
        {"name": "Visa", "type": "liability", "currency": "USD", "subtype": "credit_card"},
        idempotency_key="dc-card-accumulation-card",
    )
    category, _ = service.create_category(
        token,
        {"kind": "expense", "name": "Domains"},
        idempotency_key="dc-card-accumulation-category",
    )

    first, _ = service.record_expense(
        token,
        {
            "from_account_id": card.account_id,
            "amount": "1.72",
            "currency": "USD",
            "category_id": category.category_id,
            "purpose": "prior card expense",
        },
        idempotency_key="dc-card-accumulation-first",
    )
    second, _ = service.record_expense(
        token,
        {
            "from_account_id": card.account_id,
            "amount": "11.08",
            "currency": "USD",
            "category_id": category.category_id,
            "purpose": "domain renewal",
        },
        idempotency_key="dc-card-accumulation-second",
    )

    first_card_posting = next(posting for posting in first.postings if posting.account_id == card.account_id)
    second_card_posting = next(posting for posting in second.postings if posting.account_id == card.account_id)
    assert (first_card_posting.side, first_card_posting.amount, first_card_posting.amount_semantics) == (
        "credit",
        Decimal("1.72"),
        "debit_credit",
    )
    assert (second_card_posting.side, second_card_posting.amount, second_card_posting.amount_semantics) == (
        "credit",
        Decimal("11.08"),
        "debit_credit",
    )
    card_balance = service.account_balance(token, card.account_id)
    assert card_balance["official_balance"]["amount"] == "12.80"
    assert card_balance["official_balance"]["amount_semantics"] == ACCOUNT_TYPE_BALANCE_SEMANTICS["liability"]
    assert card_balance["liability_balance"] == liability_balance_view(Decimal("12.80"))


def test_tx_record_uses_debit_credit_for_credit_card_payment():
    service = FinanceService()
    token = service.owner_token
    bank, _ = service.create_account(
        token,
        {"name": "Checking", "type": "asset", "currency": "USD", "opening_balance": "100"},
        idempotency_key="dc-payment-bank",
    )
    card, _ = service.create_account(
        token,
        {"name": "Visa", "type": "liability", "currency": "USD", "opening_balance": "20", "subtype": "credit_card"},
        idempotency_key="dc-payment-card",
    )

    transaction, replay = service.record_transaction(
        token,
        {
            "amount": "5",
            "currency": "USD",
            "from_account_id": bank.account_id,
            "to_account_id": card.account_id,
            "purpose": "credit card payment",
        },
        idempotency_key="dc-card-payment",
    )

    assert replay is False
    bank_posting = next(posting for posting in transaction.postings if posting.account_id == bank.account_id)
    card_posting = next(posting for posting in transaction.postings if posting.account_id == card.account_id)
    assert (bank_posting.side, bank_posting.amount, bank_posting.amount_semantics) == (
        "credit",
        Decimal("5"),
        "debit_credit",
    )
    assert (card_posting.side, card_posting.amount, card_posting.amount_semantics) == (
        "debit",
        Decimal("5"),
        "debit_credit",
    )
    assert service.account_balance(token, bank.account_id)["official_balance"]["amount"] == "95"
    assert service.account_balance(token, card.account_id)["official_balance"]["amount"] == "15"


def test_liability_opening_balance_uses_natural_debit_credit_semantics():
    service = FinanceService()
    token = service.owner_token

    owed_card, _ = service.create_account(
        token,
        {
            "name": "Opening Debt Card",
            "type": "liability",
            "currency": "USD",
            "opening_balance": "9.36",
            "subtype": "credit_card",
        },
        idempotency_key="dc-opening-liability-owed",
    )
    overpaid_card, _ = service.create_account(
        token,
        {
            "name": "Opening Overpaid Card",
            "type": "liability",
            "currency": "USD",
            "opening_balance": "-1.72",
            "subtype": "credit_card",
        },
        idempotency_key="dc-opening-liability-overpaid",
    )

    transactions = service._list_all_transactions_from_storage(book_id="book_default")
    owed_transaction = next(
        transaction
        for transaction in transactions
        if transaction.memo == "Opening balance: Opening Debt Card"
    )
    owed_posting = next(posting for posting in owed_transaction.postings if posting.account_id == owed_card.account_id)
    assert (owed_posting.side, owed_posting.amount, owed_posting.amount_semantics) == (
        "credit",
        Decimal("9.36"),
        "debit_credit",
    )

    overpaid_transaction = next(
        transaction
        for transaction in transactions
        if transaction.memo == "Opening balance: Opening Overpaid Card"
    )
    overpaid_posting = next(
        posting for posting in overpaid_transaction.postings if posting.account_id == overpaid_card.account_id
    )
    assert (overpaid_posting.side, overpaid_posting.amount, overpaid_posting.amount_semantics) == (
        "debit",
        Decimal("1.72"),
        "debit_credit",
    )

    owed_balance = service.account_balance(token, owed_card.account_id)
    assert owed_balance["official_balance"]["amount"] == "9.36"
    assert owed_balance["official_balance"]["amount_semantics"] == ACCOUNT_TYPE_BALANCE_SEMANTICS["liability"]
    assert owed_balance["liability_balance"] == liability_balance_view(Decimal("9.36"))

    overpaid_balance = service.account_balance(token, overpaid_card.account_id)
    assert overpaid_balance["official_balance"]["amount"] == "-1.72"
    assert overpaid_balance["official_balance"]["amount_semantics"] == ACCOUNT_TYPE_BALANCE_SEMANTICS["liability"]
    assert overpaid_balance["liability_balance"] == liability_balance_view(Decimal("-1.72"))


def test_liability_balance_adjustment_uses_natural_debit_credit_semantics():
    service = FinanceService()
    token = service.owner_token
    card, _ = service.create_account(
        token,
        {"name": "Adjust Card", "type": "liability", "currency": "USD", "subtype": "credit_card"},
        idempotency_key="dc-adjust-liability-card",
    )

    debt_transaction, replay = service.adjust_balance(
        token,
        {
            "account_id": card.account_id,
            "amount": "11.08",
            "currency": "USD",
            "purpose": "increase card debt",
            "memo": "increase card debt",
        },
        idempotency_key="dc-adjust-liability-increase",
    )

    assert replay is False
    debt_posting = next(posting for posting in debt_transaction.postings if posting.account_id == card.account_id)
    assert (debt_posting.side, debt_posting.amount, debt_posting.amount_semantics) == (
        "credit",
        Decimal("11.08"),
        "debit_credit",
    )
    assert service.account_balance(token, card.account_id)["official_balance"]["amount"] == "11.08"

    overpayment_transaction, replay = service.adjust_balance(
        token,
        {
            "account_id": card.account_id,
            "amount": "-12.80",
            "currency": "USD",
            "purpose": "reduce card debt into overpayment",
            "memo": "reduce card debt into overpayment",
        },
        idempotency_key="dc-adjust-liability-overpayment",
    )

    assert replay is False
    overpayment_posting = next(
        posting for posting in overpayment_transaction.postings if posting.account_id == card.account_id
    )
    assert (overpayment_posting.side, overpayment_posting.amount, overpayment_posting.amount_semantics) == (
        "debit",
        Decimal("12.80"),
        "debit_credit",
    )
    overpayment_balance = service.account_balance(token, card.account_id)
    assert overpayment_balance["official_balance"]["amount"] == "-1.72"
    assert overpayment_balance["official_balance"]["amount_semantics"] == ACCOUNT_TYPE_BALANCE_SEMANTICS["liability"]
    assert overpayment_balance["liability_balance"] == liability_balance_view(Decimal("-1.72"))


def test_complete_draft_rejects_cross_currency_accounts():
    service = FinanceService()
    cash, _ = service.create_account(
        service.owner_token,
        {"name": "Cash CNY", "type": "asset", "currency": "CNY"},
        idempotency_key="draft-cross-cash",
    )
    food, _ = service.create_account(
        service.owner_token,
        {"name": "Food USD", "type": "expense", "currency": "USD"},
        idempotency_key="draft-cross-food",
    )

    with pytest.raises(ValidationError):
        service.capture_draft(
            service.owner_token,
            {
                "memo": "bad draft",
                "amount": "10",
                "currency": "USD",
                "source_account_id": cash.account_id,
                "expense_account_id": food.account_id,
            },
            idempotency_key="draft-cross-capture",
        )


def test_category_summary_uses_persisted_lines_only(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'track-anywhere.sqlite3'}"
    service = FinanceService(DeploymentSecurityConfig(), database_url=database_url)
    token = service.owner_token
    cash, _ = service.create_account(
        token,
        {"name": "Legacy Cash", "type": "asset", "currency": "CNY"},
        idempotency_key="legacy-line-cash",
    )
    expense_account, _ = service.create_account(
        token,
        {"name": "Legacy Food Account", "type": "expense", "currency": "CNY"},
        idempotency_key="legacy-line-food-account",
    )
    category, _ = service.create_category(
        token,
        {"kind": "expense", "name": "Food"},
        idempotency_key="legacy-line-category",
    )
    budget, _ = service.create_budget(
        token,
        "book_default",
        {"name": "Food Budget", "period": "monthly", "currency": "CNY", "total_amount": "100"},
        idempotency_key="legacy-line-budget",
    )
    service.add_budget_target(
        token,
        "book_default",
        budget.budget_id,
        {"target_type": "category_node", "target_id": category.category_id},
        idempotency_key="legacy-line-budget-target",
    )
    service.ledger.accounts[cash.account_id] = cash
    service.ledger.accounts[expense_account.account_id] = expense_account
    transaction = service.ledger.create_transaction(
        "line-only categorized tx",
        [
            legacy_signed_posting(cash.account_id, Decimal("-12"), "CNY"),
            legacy_signed_posting(expense_account.account_id, Decimal("12"), "CNY"),
        ],
        allow_legacy_signed=True,
    )
    service._commit_ledger_change(transaction, allow_legacy_signed_postings=True)

    assert transaction.lines == []
    summary = service.category_summary(token, kind="expense", currency="CNY")

    assert summary["groups"] == []
    assert transaction.lines == []
    service._add_category_line_for_transaction(transaction, category, accounts=(cash, expense_account))
    service._commit_ledger_change(transaction)

    restarted = FinanceService(DeploymentSecurityConfig(), database_url=database_url)
    restarted_summary = restarted.category_summary(token, kind="expense", currency="CNY")
    assert restarted_summary["groups"][0]["amount"] == "12"
    spending = restarted.spending_report(token, "book_default", group_by="category_parent", currency="CNY")
    execution = restarted.budget_execution_report(token, "book_default", budget.budget_id)
    assert spending["groups"] == [{"key": "Food", "currency": "CNY", "amount": "12", "line_count": 1}]
    assert execution["spent"] == "12"
    assert restarted.storage.get_confirmed_transaction(transaction.transaction_id).lines[0].category_id == category.category_id

    with sqlite3.connect(tmp_path / "track-anywhere.sqlite3") as connection:
        line_count = connection.execute(
            "select count(*) from transaction_lines where transaction_id = ?",
            (transaction.transaction_id,),
        ).fetchone()[0]
    assert line_count == 1
