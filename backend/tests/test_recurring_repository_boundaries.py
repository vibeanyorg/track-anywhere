from __future__ import annotations

import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND = REPO_ROOT / "backend/app/track_anywhere"


def test_recurring_item_use_cases_read_through_focused_repository():
    checked_files = [
        BACKEND / "service_recurring_item_commands.py",
        BACKEND / "service_recurring_item_queries.py",
        BACKEND / "service_recurring_reminders.py",
        BACKEND / "service_recurring_drafts.py",
    ]
    offenders = []
    forbidden = re.compile(r"\bself\.storage\.(get_recurring_item|list_recurring_items)\b")
    for path in checked_files:
        for line_number, line in enumerate(path.read_text().splitlines(), start=1):
            if forbidden.search(line):
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{line_number}: {line.strip()}")

    query_source = (BACKEND / "service_recurring_item_queries.py").read_text()
    workflow_repo = (BACKEND / "storage_repositories/workflow.py").read_text()

    assert offenders == []
    assert "uow.recurring.list_items" in query_source
    assert "uow.recurring.get_item" in query_source
    assert "def list_items(" in workflow_repo
    assert "def get_item(" in workflow_repo


def test_recurring_item_validation_reads_accounts_and_categories_through_focused_repositories():
    source = (BACKEND / "service_recurring_item_validation.py").read_text()
    forbidden = re.compile(r"\bself\.storage\.(get_account|get_category)\b")
    offenders = [
        f"service_recurring_item_validation.py:{line_number}: {line.strip()}"
        for line_number, line in enumerate(source.splitlines(), start=1)
        if forbidden.search(line)
    ]

    assert offenders == []
    assert "_get_account_from_storage" in source
    assert "_get_category_from_storage" in source
