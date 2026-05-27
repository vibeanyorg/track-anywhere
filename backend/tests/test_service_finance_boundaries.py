from __future__ import annotations

import ast
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND = REPO_ROOT / "backend/app/track_anywhere"


def test_financial_use_cases_are_context_scoped():
    expected_modules = {
        "service_attachments.py",
        "service_finance.py",
        "service_funds.py",
        "service_reconciliation.py",
    }
    for module in expected_modules:
        assert (BACKEND / module).exists()

    source = (BACKEND / "service_finance.py").read_text()
    tree = ast.parse(source)
    classes = [node for node in tree.body if isinstance(node, ast.ClassDef)]

    assert [node.name for node in classes] == ["FinancialUseCases"]
    assert len(classes[0].body) == 1
    assert isinstance(classes[0].body[0], ast.Pass)
    assert "sha256" not in source
    assert "CreateFundCommand" not in source
    assert "ReconciliationActionCommand" not in source
