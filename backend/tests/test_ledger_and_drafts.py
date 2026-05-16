from __future__ import annotations

from decimal import Decimal

import pytest

from track_anywhere.errors import IdempotencyConflict, StaleVersion, ValidationError
from track_anywhere.ledger import Posting
from track_anywhere.security import DeploymentSecurityConfig
from track_anywhere.service import FinanceService


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

    with pytest.raises(ValidationError):
        service.ledger.create_transaction(
            "bad",
            [
                Posting(cash.account_id, Decimal("-38"), "CNY"),
                Posting(expense.account_id, Decimal("37"), "CNY"),
            ],
        )


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
    assert projected["projected_balance"]["pending_impact"] == "-38"

    transaction, confirm_replay = service.confirm_draft(
        service.owner_token,
        {"draft_id": draft.draft_id, "expected_version": draft.version},
        idempotency_key="confirm-coffee",
    )

    assert confirm_replay is False
    assert transaction.transaction_id.startswith("txn_")
    assert service.account_balance(service.owner_token, cash.account_id)["official_balance"]["amount"] == "62"


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
    draft.version += 1
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
    assert issue_event.details["token"] == "[REDACTED]"
