from __future__ import annotations

import ast
import re
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


def test_category_query_use_cases_read_through_focused_repository():
    source = (BACKEND / "service_category_queries.py").read_text()
    repository_source = (BACKEND / "storage_repositories/categories.py").read_text()
    forbidden = re.compile(r"\bself\.storage\.(list_categories|get_category|find_category_by_path)\b")

    offenders = [
        f"service_category_queries.py:{line_number}: {line.strip()}"
        for line_number, line in enumerate(source.splitlines(), start=1)
        if forbidden.search(line)
    ]

    assert offenders == []
    assert "uow.categories.list_categories" in source
    assert "uow.categories.get_category" in source
    assert "uow.categories.find_category_by_path" in source
    assert "class CategoryRepository" in repository_source
    assert "def list_categories(" in repository_source
    assert "def get_category(" in repository_source
    assert "def find_category_by_path(" in repository_source
