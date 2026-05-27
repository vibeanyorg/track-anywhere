from __future__ import annotations

import ast
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
