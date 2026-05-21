from __future__ import annotations

import pytest

from track_anywhere.errors import ValidationError
from track_anywhere.security import DeploymentSecurityConfig
from track_anywhere.service import FinanceService


def test_failed_recurring_update_does_not_mutate_item(tmp_path):
    service = FinanceService(
        DeploymentSecurityConfig(),
        database_url=f"sqlite:///{tmp_path / 'recurring-update.sqlite3'}",
    )
    token = service.owner_token
    account, _ = service.create_account(
        token,
        {"name": "USD Wallet", "type": "asset", "currency": "USD", "opening_balance": "100"},
        idempotency_key="rec-update-account",
    )
    category, _ = service.create_category(
        token,
        {"kind": "expense", "name": "Subscriptions"},
        idempotency_key="rec-update-category",
    )
    item, _ = service.create_recurring_item(
        token,
        {
            "name": "ChatGPT",
            "kind": "paid",
            "amount": "20",
            "currency": "USD",
            "recurrence": {"type": "monthly_day", "day": 15},
            "anchor_date": "2026-06-15",
            "reminder_days": [3, 2, 1],
            "source_account_id": account.account_id,
            "category_id": category.category_id,
        },
        idempotency_key="rec-update-item",
    )

    with pytest.raises(ValidationError):
        service.update_recurring_item(
            token,
            item.recurring_id,
            {"currency": "CNY"},
            idempotency_key="rec-update-invalid",
        )

    current = service.get_recurring_item(token, item.recurring_id)
    assert current.currency == "USD"
    assert current.version == item.version
