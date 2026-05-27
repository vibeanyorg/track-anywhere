from __future__ import annotations

import ast
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND = REPO_ROOT / "backend/app/track_anywhere"


def test_recurring_item_use_cases_are_context_scoped():
    expected_modules = {
        "service_recurring_item_commands.py",
        "service_recurring_item_queries.py",
        "service_recurring_item_validation.py",
        "service_recurring_items.py",
    }
    for module in expected_modules:
        assert (BACKEND / module).exists()

    source = (BACKEND / "service_recurring_items.py").read_text()
    tree = ast.parse(source)
    classes = [node for node in tree.body if isinstance(node, ast.ClassDef)]

    assert [node.name for node in classes] == ["RecurringItemUseCases"]
    assert len(classes[0].body) == 1
    assert isinstance(classes[0].body[0], ast.Pass)
    assert "CreateRecurringItemCommand" not in source
    assert "UpdateRecurringItemCommand" not in source
    assert "validate_recurring_item" not in source
