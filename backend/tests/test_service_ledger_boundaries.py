from __future__ import annotations

import ast
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND = REPO_ROOT / "backend/app/track_anywhere"


def test_ledger_write_use_cases_are_context_scoped():
    expected_modules = {
        "service_ledger.py",
        "service_ledger_records.py",
        "service_ledger_reversals.py",
        "service_ledger_transfers.py",
    }
    for module in expected_modules:
        assert (BACKEND / module).exists()

    source = (BACKEND / "service_ledger.py").read_text()
    tree = ast.parse(source)
    classes = [node for node in tree.body if isinstance(node, ast.ClassDef)]

    assert [node.name for node in classes] == ["LedgerUseCases"]
    assert len(classes[0].body) == 1
    assert isinstance(classes[0].body[0], ast.Pass)
    assert "RecordExpenseCommand" not in source
    assert "RecordTransactionCommand" not in source
    assert "ReverseTransactionCommand" not in source
