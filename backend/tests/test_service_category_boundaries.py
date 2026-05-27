from __future__ import annotations

import ast
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND = REPO_ROOT / "backend/app/track_anywhere"


def test_category_use_cases_are_context_scoped():
    expected_modules = {
        "service_categories.py",
        "service_category_commands.py",
        "service_category_lines.py",
        "service_category_queries.py",
        "service_category_reporting.py",
    }
    for module in expected_modules:
        assert (BACKEND / module).exists()

    source = (BACKEND / "service_categories.py").read_text()
    tree = ast.parse(source)
    classes = [node for node in tree.body if isinstance(node, ast.ClassDef)]

    assert [node.name for node in classes] == ["CategoryUseCases"]
    assert len(classes[0].body) == 1
    assert isinstance(classes[0].body[0], ast.Pass)
    assert "CreateCategoryCommand" not in source
    assert "EnsureCategoryPathCommand" not in source
    assert "add_transaction_line" not in source
