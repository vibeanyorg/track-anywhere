from __future__ import annotations

import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND = REPO_ROOT / "backend/app/track_anywhere"


def test_credit_card_use_cases_read_catalog_through_focused_repositories():
    source = (BACKEND / "service_credit_cards.py").read_text()
    forbidden = re.compile(r"\bself\.storage\.(list_accounts|get_account|get_credit_card_profile_optional)\b")
    offenders = [
        f"service_credit_cards.py:{line_number}: {line.strip()}"
        for line_number, line in enumerate(source.splitlines(), start=1)
        if forbidden.search(line)
    ]
    repository_source = (BACKEND / "storage_repositories/finance.py").read_text()

    assert offenders == []
    assert "_get_account_from_storage" in source
    assert "uow.accounts.list_accounts" in source
    assert "uow.credit_cards.get_profile_optional" in source
    assert "uow.credit_cards.list_profiles" in source
    assert "uow.payment_instruments.list_instruments" in source
    assert "def get_profile_optional" in repository_source
    assert "def list_profiles" in repository_source


def test_credit_card_use_cases_read_balances_through_focused_helper():
    source = (BACKEND / "service_credit_cards.py").read_text()
    balance_source = (BACKEND / "service_balance_queries.py").read_text()
    ledger_repository_source = (BACKEND / "storage_repositories/ledger.py").read_text()

    assert "self.storage.account_balance" not in source
    assert "_account_balance_from_storage" in source
    assert "uow.ledger.account_balances" in source
    assert "def _account_balance_from_storage(" in balance_source
    assert "def account_balances" in ledger_repository_source
