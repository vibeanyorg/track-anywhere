from __future__ import annotations

import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND = REPO_ROOT / "backend/app/track_anywhere"


def test_payment_instrument_use_cases_read_through_focused_repository():
    checked_files = [
        BACKEND / "service_payment_instruments.py",
        BACKEND / "service_credit_cards.py",
    ]
    offenders = []
    forbidden = re.compile(
        r"\bself\.storage\.(get_payment_instrument|get_payment_instrument_by_slug|list_payment_instruments)\b"
    )
    for path in checked_files:
        for line_number, line in enumerate(path.read_text().splitlines(), start=1):
            if forbidden.search(line):
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{line_number}: {line.strip()}")

    service_source = (BACKEND / "service_payment_instruments.py").read_text()
    repository_source = (BACKEND / "storage_repositories/payments.py").read_text()

    assert offenders == []
    assert "uow.payment_instruments.list_instruments" in service_source
    assert "uow.payment_instruments.get_instrument" in service_source
    assert "uow.payment_instruments.get_instrument_by_slug" in service_source
    assert "def list_instruments(" in repository_source
    assert "def get_instrument(" in repository_source
    assert "def get_instrument_by_slug(" in repository_source


def test_payment_instrument_use_cases_read_accounts_through_focused_repository():
    source = (BACKEND / "service_payment_instruments.py").read_text()
    forbidden = re.compile(r"\bself\.storage\.get_account\b")
    offenders = [
        f"service_payment_instruments.py:{line_number}: {line.strip()}"
        for line_number, line in enumerate(source.splitlines(), start=1)
        if forbidden.search(line)
    ]

    assert offenders == []
    assert "_get_account_from_storage" in source
