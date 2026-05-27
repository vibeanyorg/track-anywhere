from __future__ import annotations

import ast
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SERVICE_PATH = REPO_ROOT / "backend/app/track_anywhere/service.py"


def test_service_module_stays_as_composition_root_only():
    source = SERVICE_PATH.read_text()
    tree = ast.parse(source)
    classes = [node for node in tree.body if isinstance(node, ast.ClassDef)]

    assert [node.name for node in classes] == ["FinanceService"]
    assert "from hashlib import sha256" not in source
    assert "from .storage_json import to_jsonable" not in source
    assert "CredentialReference" not in source

    method_names = [
        node.name
        for class_node in classes
        for node in class_node.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    assert method_names == ["__init__"]
