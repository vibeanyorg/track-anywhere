from __future__ import annotations

import ast
import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND = REPO_ROOT / "backend/app/track_anywhere"


def test_balance_use_cases_are_context_scoped():
    expected_modules = {
        "service_balance_adjustments.py",
        "service_balance_queries.py",
        "service_balance_system_accounts.py",
        "service_balances.py",
    }
    for module in expected_modules:
        assert (BACKEND / module).exists()

    source = (BACKEND / "service_balances.py").read_text()
    tree = ast.parse(source)
    classes = [node for node in tree.body if isinstance(node, ast.ClassDef)]

    assert [node.name for node in classes] == ["BalanceUseCases"]
    assert len(classes[0].body) == 1
    assert isinstance(classes[0].body[0], ast.Pass)
    assert "BalanceAdjustmentCommand" not in source
    assert "build_transaction" not in source
    assert "_system_adjustment_account" not in source


def test_system_account_helpers_read_through_focused_repository():
    source = (BACKEND / "service_balance_system_accounts.py").read_text()
    forbidden = re.compile(r"\bself\.storage\.(get_account|list_accounts)\b")
    offenders = [
        f"service_balance_system_accounts.py:{line_number}: {line.strip()}"
        for line_number, line in enumerate(source.splitlines(), start=1)
        if forbidden.search(line)
    ]

    assert offenders == []
    assert "_get_account_from_storage" in source
    assert "_list_accounts_from_storage" in source


def test_balance_use_cases_read_accounts_through_focused_repository():
    checked_files = [
        BACKEND / "service_balance_queries.py",
        BACKEND / "service_balance_adjustments.py",
    ]
    forbidden = re.compile(r"\bself\.storage\.get_account\b")
    offenders = []
    for path in checked_files:
        for line_number, line in enumerate(path.read_text().splitlines(), start=1):
            if forbidden.search(line):
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{line_number}: {line.strip()}")

    query_source = (BACKEND / "service_balance_queries.py").read_text()
    adjustment_source = (BACKEND / "service_balance_adjustments.py").read_text()

    assert offenders == []
    assert "_get_account_from_storage" in query_source
    assert "_get_account_from_storage" in adjustment_source
