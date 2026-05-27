from __future__ import annotations

import ast
import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND = REPO_ROOT / "backend/app/track_anywhere"


def test_book_use_cases_are_context_scoped():
    expected_modules = {
        "service_book_accounts.py",
        "service_book_budgets.py",
        "service_book_categories.py",
        "service_book_core.py",
        "service_book_ledger.py",
        "service_books.py",
    }
    for module in expected_modules:
        assert (BACKEND / module).exists()

    source = (BACKEND / "service_books.py").read_text()
    tree = ast.parse(source)
    classes = [node for node in tree.body if isinstance(node, ast.ClassDef)]

    assert [node.name for node in classes] == ["BookUseCases"]
    assert len(classes[0].body) == 1
    assert isinstance(classes[0].body[0], ast.Pass)
    assert "CreateBudgetCommand" not in source
    assert "RecordTransactionCommand" not in source
    assert "UpdateCategoryCommand" not in source


def test_book_ledger_use_cases_read_through_focused_repositories():
    source = (BACKEND / "service_book_ledger.py").read_text()
    forbidden = re.compile(r"\bself\.storage\.(get_account|list_confirmed_transactions)\b")
    offenders = [
        f"service_book_ledger.py:{line_number}: {line.strip()}"
        for line_number, line in enumerate(source.splitlines(), start=1)
        if forbidden.search(line)
    ]

    assert offenders == []
    assert "_get_account_from_storage" in source
    assert "_list_transactions_from_storage" in source
