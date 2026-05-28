from __future__ import annotations

import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND = REPO_ROOT / "backend/app/track_anywhere"


def test_report_use_cases_read_transactions_through_focused_repository():
    checked_files = [
        BACKEND / "service_reports.py",
        BACKEND / "service_category_reporting.py",
    ]
    forbidden = re.compile(r"\bself\.storage\.list_all_confirmed_transactions\b")
    offenders = []
    for path in checked_files:
        for line_number, line in enumerate(path.read_text().splitlines(), start=1):
            if forbidden.search(line):
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{line_number}: {line.strip()}")

    reports_source = (BACKEND / "service_reports.py").read_text()
    category_source = (BACKEND / "service_category_reporting.py").read_text()
    ledger_source = (BACKEND / "service_ledger_queries.py").read_text()

    assert offenders == []
    assert "_list_all_transactions_from_storage" in reports_source
    assert "_list_all_transactions_from_storage" in category_source
    assert "uow.transactions.list_all_confirmed_transactions" in ledger_source
