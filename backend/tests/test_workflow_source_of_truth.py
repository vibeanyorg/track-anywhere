from __future__ import annotations

from datetime import date
from decimal import Decimal

from track_anywhere.security import DeploymentSecurityConfig
from track_anywhere.service import FinanceService


def test_recurring_reminders_and_draft_generation_use_database_source_of_truth(tmp_path):
    database_path = tmp_path / "track-anywhere.sqlite3"
    service = FinanceService(DeploymentSecurityConfig(), database_url=f"sqlite:///{database_path}")
    token = service.owner_token
    account, _ = service.create_account(
        token,
        {
            "name": "Recurring Truth Cash",
            "type": "asset",
            "currency": "USD",
            "opening_balance": "100",
        },
        idempotency_key="recurring-truth-account",
    )
    parent, _ = service.create_category(
        token,
        {"kind": "expense", "name": "Recurring Truth Expense"},
        idempotency_key="recurring-truth-parent-category",
    )
    category, _ = service.create_category(
        token,
        {"kind": "expense", "name": "Recurring Truth AI", "parent_id": parent.category_id},
        idempotency_key="recurring-truth-category",
    )
    recurring, _ = service.create_recurring_item(
        token,
        {
            "name": "DB Truth Subscription",
            "kind": "paid",
            "provider": "OpenAI",
            "amount": "20",
            "currency": "USD",
            "recurrence": {"type": "monthly_day", "day": 15},
            "anchor_date": "2026-06-15",
            "reminder_days": [3],
            "source_account_id": account.account_id,
            "category_id": category.category_id,
        },
        idempotency_key="recurring-truth-item",
    )

    stale = service.recurring.items[recurring.recurring_id]
    stale.name = "stale memory subscription"
    stale.last_draft_renewal_date = date(2026, 6, 15)

    reminders = service.check_recurring_reminders(token, as_of="2026-06-12", window_days=0)
    assert reminders["reminders"][0]["name"] == "DB Truth Subscription"

    result, replay = service.generate_recurring_drafts(
        token,
        {"as_of": "2026-06-16"},
        idempotency_key="recurring-truth-draft",
    )
    assert replay is False
    assert len(result["created"]) == 1
    draft = service.drafts.get(result["created"][0]["draft_id"])
    assert draft.memo == "Recurring renewal: DB Truth Subscription (2026-06-15)"
    assert service.get_recurring_item(token, recurring.recurring_id).last_draft_renewal_date == date(2026, 6, 15)


def test_draft_projection_and_confirmation_use_database_source_of_truth(tmp_path):
    database_path = tmp_path / "track-anywhere.sqlite3"
    service = FinanceService(DeploymentSecurityConfig(), database_url=f"sqlite:///{database_path}")
    token = service.owner_token
    cash, _ = service.create_account(
        token,
        {"name": "Draft Truth Cash", "type": "asset", "currency": "CNY", "opening_balance": "100"},
        idempotency_key="draft-truth-cash",
    )
    expense, _ = service.create_account(
        token,
        {"name": "Draft Truth Expense", "type": "expense", "currency": "CNY"},
        idempotency_key="draft-truth-expense",
    )
    draft, _ = service.capture_draft(
        token,
        {
            "memo": "DB truth draft",
            "amount": "38",
            "currency": "CNY",
            "source_account_id": cash.account_id,
            "expense_account_id": expense.account_id,
        },
        idempotency_key="draft-truth-capture",
    )
    expected_version = draft.version

    stale = service.drafts.drafts[draft.draft_id]
    stale.state = "rejected"
    stale.version += 10
    stale.proposed_postings[0].amount = Decimal("999")

    projected = service.account_balance(token, cash.account_id, include_drafts=True)
    assert projected["projected_balance"]["pending_impact"] == "-38"
    assert projected["projected_balance"]["included_draft_ids"] == [draft.draft_id]

    transaction, replay = service.confirm_draft(
        token,
        {"draft_id": draft.draft_id, "expected_version": expected_version},
        idempotency_key="draft-truth-confirm",
    )
    assert replay is False
    assert transaction.postings[0].amount == Decimal("38")
    assert transaction.postings[0].side == "credit"
    assert transaction.postings[0].amount_semantics == "debit_credit"


def test_draft_projection_skips_invalid_semantics_and_missing_account_type(tmp_path):
    database_path = tmp_path / "draft-projection-invalid-semantics.sqlite3"
    service = FinanceService(DeploymentSecurityConfig(), database_url=f"sqlite:///{database_path}")
    token = service.owner_token
    cash, _ = service.create_account(
        token,
        {"name": "Projection Dirty Cash", "type": "asset", "currency": "USD", "opening_balance": "100"},
        idempotency_key="projection-dirty-cash",
    )
    expense, _ = service.create_account(
        token,
        {"name": "Projection Dirty Expense", "type": "expense", "currency": "USD"},
        idempotency_key="projection-dirty-expense",
    )
    draft, _ = service.capture_draft(
        token,
        {
            "memo": "dirty draft",
            "amount": "10",
            "currency": "USD",
            "source_account_id": cash.account_id,
            "expense_account_id": expense.account_id,
        },
        idempotency_key="projection-dirty-draft",
    )
    draft.proposed_postings[0].amount_semantics = "unknown"  # type: ignore[assignment]
    service.storage._read_drafts = {draft.draft_id: draft}
    service.storage._read_accounts = {cash.account_id: cash, expense.account_id: expense}

    projected = service.account_balance(token, cash.account_id, include_drafts=True)

    assert projected["projected_balance"]["pending_impact"] == "0"
    assert projected["projected_balance"]["included_draft_ids"] == []

    draft.proposed_postings[0].account_id = "acc_missing"
    service.storage._read_accounts = {}
    totals, included_draft_ids, draft_count = service.storage.draft_projection_for_account("acc_missing")

    assert totals == {}
    assert included_draft_ids == []
    assert draft_count == 1
