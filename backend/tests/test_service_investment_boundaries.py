from __future__ import annotations

import ast
import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND = REPO_ROOT / "backend/app/track_anywhere"


def test_investment_use_cases_are_context_scoped():
    expected_modules = {
        "service_investment_events.py",
        "service_investment_performance.py",
        "service_investment_valuations.py",
        "service_investments.py",
    }
    for module in expected_modules:
        assert (BACKEND / module).exists()

    source = (BACKEND / "service_investments.py").read_text()
    tree = ast.parse(source)
    classes = [node for node in tree.body if isinstance(node, ast.ClassDef)]

    assert [node.name for node in classes] == ["InvestmentUseCases"]
    assert len(classes[0].body) == 1
    assert isinstance(classes[0].body[0], ast.Pass)
    assert "RecordInvestmentEventCommand" not in source
    assert "RecordInvestmentValuationCommand" not in source
    assert "investment_performance_report" not in source


def test_investment_use_cases_read_through_focused_repositories():
    checked_files = [
        BACKEND / "service_investment_events.py",
        BACKEND / "service_investment_performance.py",
        BACKEND / "service_investment_valuations.py",
    ]
    forbidden = re.compile(
        r"\bself\.storage\.(get_account|get_confirmed_transaction|list_confirmed_transactions)\b"
    )
    offenders = []
    for path in checked_files:
        for line_number, line in enumerate(path.read_text().splitlines(), start=1):
            if forbidden.search(line):
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{line_number}: {line.strip()}")

    events_source = (BACKEND / "service_investment_events.py").read_text()
    performance_source = (BACKEND / "service_investment_performance.py").read_text()
    valuations_source = (BACKEND / "service_investment_valuations.py").read_text()

    assert offenders == []
    assert "_get_account_from_storage" in events_source
    assert "_get_transaction_from_storage" in events_source
    assert "_get_account_from_storage" in performance_source
    assert "_list_transactions_from_storage" in performance_source
    assert "_get_account_from_storage" in valuations_source
