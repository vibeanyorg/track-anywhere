from __future__ import annotations

import ast
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND = REPO_ROOT / "backend/app/track_anywhere"


def test_account_use_cases_are_context_scoped():
    expected_modules = {
        "service_account_commands.py",
        "service_account_factory.py",
        "service_account_queries.py",
        "service_account_summary.py",
        "service_accounts.py",
    }
    for module in expected_modules:
        assert (BACKEND / module).exists()

    source = (BACKEND / "service_accounts.py").read_text()
    tree = ast.parse(source)
    classes = [node for node in tree.body if isinstance(node, ast.ClassDef)]

    assert [node.name for node in classes] == ["AccountUseCases"]
    assert len(classes[0].body) == 1
    assert isinstance(classes[0].body[0], ast.Pass)
    assert "CreateAccountCommand" not in source
    assert "UpdateAccountMetadataCommand" not in source
    assert "build_transaction" not in source
