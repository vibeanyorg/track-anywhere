from __future__ import annotations

import ast
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
