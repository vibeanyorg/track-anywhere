import pytest

from track_anywhere.errors import PolicyDenied, ValidationError
from track_anywhere.security import DeploymentSecurityConfig
from track_anywhere.service import FinanceService


def service_for(tmp_path):
    db_path = tmp_path / "recurring.sqlite3"
    return FinanceService(
        DeploymentSecurityConfig(),
        database_url=f"sqlite:///{db_path}",
    )


def paid_support(service, token):
    account, _ = service.create_account(
        token,
        {
            "name": "USD Wallet",
            "type": "asset",
            "currency": "USD",
            "opening_balance": "1000",
        },
        idempotency_key="account-usd-wallet",
    )
    category, _ = service.create_category(
        token,
        {"kind": "expense", "primary": "Subscriptions", "secondary": "AI"},
        idempotency_key="category-subscriptions-ai",
    )
    return account, category


def paid_payload(account, category, **overrides):
    payload = {
        "name": "ChatGPT",
        "kind": "paid",
        "provider": "OpenAI",
        "amount": "20",
        "currency": "USD",
        "recurrence": {"type": "monthly_day", "day": 15},
        "anchor_date": "2026-06-15",
        "reminder_days": [3, 2, 1],
        "source_account_id": account.account_id,
        "category_id": category.category_id,
    }
    payload.update(overrides)
    return payload


def test_monthly_paid_item_returns_natural_day_reminders(tmp_path):
    service = service_for(tmp_path)
    token = service.owner_token
    account, category = paid_support(service, token)

    item, replay = service.create_recurring_item(
        token,
        paid_payload(account, category),
        idempotency_key="recurring-chatgpt",
    )

    assert replay is False
    assert item.name == "ChatGPT"
    assert item.status == "active"

    before_anchor = service.check_recurring_reminders(token, as_of="2026-05-12", window_days=0)
    assert before_anchor["reminders"] == []

    due = service.check_recurring_reminders(token, as_of="2026-06-12", window_days=0)
    assert len(due["reminders"]) == 1
    reminder = due["reminders"][0]
    assert reminder["recurring_id"] == item.recurring_id
    assert reminder["name"] == "ChatGPT"
    assert reminder["renewal_date"] == "2026-06-15"
    assert reminder["lead_days"] == 3
    assert reminder["amount"] == "20"
    assert reminder["currency"] == "USD"

    quiet = service.check_recurring_reminders(token, as_of="2026-06-11", window_days=0)
    assert quiet["reminders"] == []


def test_yearly_items_support_prior_week_reminder_patterns(tmp_path):
    service = service_for(tmp_path)
    token = service.owner_token
    account, category = paid_support(service, token)

    porkbun, _ = service.create_recurring_item(
        token,
        paid_payload(
            account,
            category,
            name="Porkbun vibeany.io",
            provider="Porkbun",
            reference="vibeany.io",
            amount="40",
            recurrence={"type": "yearly_date", "month": 8, "day": 10},
            anchor_date="2026-08-10",
            reminder_days=[7, 6, 5, 4, 3, 2, 1],
        ),
        idempotency_key="recurring-porkbun-vibeany",
    )
    cloudcone, _ = service.create_recurring_item(
        token,
        paid_payload(
            account,
            category,
            name="CloudCone VPS",
            provider="CloudCone",
            amount="12.99",
            recurrence={"type": "yearly_date", "month": 12, "day": 10},
            anchor_date="2026-12-10",
            reminder_days=[7],
        ),
        idempotency_key="recurring-cloudcone-vps",
    )

    august = service.check_recurring_reminders(token, as_of="2026-08-03", window_days=0)
    assert august["reminders"][0]["recurring_id"] == porkbun.recurring_id
    assert august["reminders"][0]["reference"] == "vibeany.io"
    assert august["reminders"][0]["lead_days"] == 7

    december = service.check_recurring_reminders(token, as_of="2026-12-03", window_days=0)
    assert december["reminders"][0]["recurring_id"] == cloudcone.recurring_id
    assert december["reminders"][0]["amount"] == "12.99"


def test_due_paid_item_generates_explicit_draft_without_confirming(tmp_path):
    service = service_for(tmp_path)
    token = service.owner_token
    account, category = paid_support(service, token)
    item, _ = service.create_recurring_item(
        token,
        paid_payload(account, category),
        idempotency_key="recurring-chatgpt",
    )
    transaction_count = len(service.ledger.transactions)

    result, replay = service.generate_recurring_drafts(
        token,
        {"as_of": "2026-06-16"},
        idempotency_key="draft-chatgpt-june",
    )

    assert replay is False
    assert result["created"][0]["recurring_id"] == item.recurring_id
    assert result["created"][0]["renewal_date"] == "2026-06-15"
    draft_id = result["created"][0]["draft_id"]
    draft = service.drafts.get(draft_id)
    assert draft.source == "recurring"
    assert draft.category_id == category.category_id
    assert draft.metadata["recurring_id"] == item.recurring_id
    assert draft.metadata["renewal_date"] == "2026-06-15"
    assert draft.state == "ready_to_confirm"
    assert len(service.ledger.transactions) == transaction_count

    replayed, replay = service.generate_recurring_drafts(
        token,
        {"as_of": "2026-06-16"},
        idempotency_key="draft-chatgpt-june",
    )
    assert replay is True
    assert replayed == result

    duplicate, replay = service.generate_recurring_drafts(
        token,
        {"as_of": "2026-06-16"},
        idempotency_key="draft-chatgpt-june-different-key",
    )
    assert replay is False
    assert duplicate["created"] == []
    assert duplicate["skipped"][0]["reason"] == "already_generated"

    restarted = FinanceService(
        DeploymentSecurityConfig(),
        database_url=service.storage.database_url,
    )
    after_restart, replay = restarted.generate_recurring_drafts(
        token,
        {"as_of": "2026-06-16"},
        idempotency_key="draft-chatgpt-june-after-restart",
    )
    assert replay is False
    assert after_restart["created"] == []
    assert after_restart["skipped"][0]["reason"] == "already_generated"

    persisted_draft = restarted.drafts.get(draft_id)
    transaction, replay = restarted.confirm_draft(
        token,
        {"draft_id": draft_id, "expected_version": persisted_draft.version},
        idempotency_key="confirm-chatgpt-june",
    )
    assert replay is False
    assert transaction.category_id == category.category_id


def test_reminder_only_item_never_generates_money_draft(tmp_path):
    service = service_for(tmp_path)
    token = service.owner_token

    item, _ = service.create_recurring_item(
        token,
        {
            "name": "Cloud credit voucher reset",
            "kind": "reminder_only",
            "recurrence": {"type": "yearly_date", "month": 9, "day": 1},
            "anchor_date": "2026-09-01",
            "reminder_days": [3, 2, 1],
        },
        idempotency_key="recurring-voucher-reset",
    )

    due = service.check_recurring_reminders(token, as_of="2026-08-29", window_days=0)
    assert due["reminders"][0]["recurring_id"] == item.recurring_id

    result, replay = service.generate_recurring_drafts(
        token,
        {"as_of": "2026-09-02"},
        idempotency_key="draft-voucher-reset",
    )
    assert replay is False
    assert result["created"] == []
    assert result["skipped"][0]["reason"] == "not_paid"
    assert service.drafts.drafts == {}


def test_generated_drafts_require_recurring_write_and_capture_draft(tmp_path):
    service = service_for(tmp_path)
    token = service.owner_token
    account, category = paid_support(service, token)
    service.create_recurring_item(
        token,
        paid_payload(account, category),
        idempotency_key="recurring-chatgpt",
    )
    recurring_only, _ = service.issue_agent_credential_command(
        token,
        {"scopes": ["recurring:write"]},
        idempotency_key="token-recurring-only",
    )
    confirm_only, _ = service.issue_agent_credential_command(
        token,
        {"scopes": ["recurring:write", "ledger:confirm"]},
        idempotency_key="token-wrong-scope",
    )
    capture_token, _ = service.issue_agent_credential_command(
        token,
        {"scopes": ["recurring:write", "capture:draft"]},
        idempotency_key="token-capture-scope",
    )

    with pytest.raises(PolicyDenied):
        service.generate_recurring_drafts(
            recurring_only["token"],
            {"as_of": "2026-06-16"},
            idempotency_key="draft-recurring-only",
        )
    with pytest.raises(PolicyDenied):
        service.generate_recurring_drafts(
            confirm_only["token"],
            {"as_of": "2026-06-16"},
            idempotency_key="draft-wrong-scope",
        )

    result, replay = service.generate_recurring_drafts(
        capture_token["token"],
        {"as_of": "2026-06-16"},
        idempotency_key="draft-capture-scope",
    )
    assert replay is False
    assert len(result["created"]) == 1


def test_recurring_validation_rejects_unsupported_calendar_edges(tmp_path):
    service = service_for(tmp_path)
    token = service.owner_token
    account, category = paid_support(service, token)

    with pytest.raises(ValidationError):
        service.create_recurring_item(
            token,
            paid_payload(
                account,
                category,
                recurrence={"type": "monthly_day", "day": 31},
            ),
            idempotency_key="recurring-month-end",
        )
    with pytest.raises(ValidationError):
        service.create_recurring_item(
            token,
            paid_payload(
                account,
                category,
                recurrence={"type": "yearly_date", "month": 2, "day": 29},
                anchor_date="2028-02-29",
            ),
            idempotency_key="recurring-leap-day",
        )
