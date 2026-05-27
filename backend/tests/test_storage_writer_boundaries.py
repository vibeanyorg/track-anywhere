from __future__ import annotations

import ast
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND = REPO_ROOT / "backend/app/track_anywhere"


def test_storage_writer_facade_stays_context_scoped():
    expected_modules = {
        "storage_audit_idempotency_writers.py",
        "storage_upsert_writers.py",
        "storage_writers.py",
    }
    for module in expected_modules:
        assert (BACKEND / module).exists()

    source = (BACKEND / "storage_writers.py").read_text()
    tree = ast.parse(source)
    classes = [node for node in tree.body if isinstance(node, ast.ClassDef)]

    assert [node.name for node in classes] == ["StorageWriters"]
    assert len(classes[0].body) == 1
    assert isinstance(classes[0].body[0], ast.Pass)
    assert "TransactionRecord" not in source
    assert "DraftRecord" not in source
    assert "InvestmentEventRecord" not in source
    assert "IdempotencyReceiptRecord" not in source
    assert "LedgerStorageWriters" not in source
    assert "WorkflowStorageWriters" not in source
    assert "FinanceStorageWriters" not in source
