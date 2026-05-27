from __future__ import annotations

import pytest
from pydantic import ValidationError

from track_anywhere.security import DeploymentSecurityConfig
from track_anywhere.service import FinanceService


def test_budget_target_rejects_legacy_merchant_type(tmp_path):
    service = FinanceService(DeploymentSecurityConfig(), database_url=f"sqlite:///{tmp_path / 'track-anywhere.sqlite3'}")
    token = service.owner_token
    book = service.get_book(token, "book_default")
    budget, _ = service.create_budget(
        token,
        book.book_id,
        {"name": "Counterparty Budget", "period": "monthly", "currency": "CNY", "total_amount": "300"},
        idempotency_key="counterparty-budget",
    )

    with pytest.raises(ValidationError):
        service.add_budget_target(
            token,
            book.book_id,
            budget.budget_id,
            {"target_type": "merchant", "target_id": "cp_didi"},
            idempotency_key="legacy-merchant-target",
        )
