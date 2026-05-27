from __future__ import annotations

import ast
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND = REPO_ROOT / "backend/app/track_anywhere"


def test_recurring_use_cases_are_context_scoped():
    expected_modules = {
        "service_recurring.py",
        "service_recurring_drafts.py",
        "service_recurring_items.py",
        "service_recurring_reminders.py",
    }
    for module in expected_modules:
        assert (BACKEND / module).exists()

    source = (BACKEND / "service_recurring.py").read_text()
    tree = ast.parse(source)
    classes = [node for node in tree.body if isinstance(node, ast.ClassDef)]

    assert [node.name for node in classes] == ["RecurringUseCases"]
    assert len(classes[0].body) == 1
    assert isinstance(classes[0].body[0], ast.Pass)
    assert "CreateRecurringItemCommand" not in source
    assert "GenerateRecurringDraftsCommand" not in source
    assert "due_reminders" not in source
