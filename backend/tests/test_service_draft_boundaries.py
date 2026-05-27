from __future__ import annotations

import ast
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND = REPO_ROOT / "backend/app/track_anywhere"


def test_draft_use_cases_are_context_scoped():
    expected_modules = {
        "service_draft_capture.py",
        "service_draft_confirmation.py",
        "service_draft_lifecycle.py",
        "service_draft_store.py",
        "service_drafts.py",
    }
    for module in expected_modules:
        assert (BACKEND / module).exists()

    source = (BACKEND / "service_drafts.py").read_text()
    tree = ast.parse(source)
    classes = [node for node in tree.body if isinstance(node, ast.ClassDef)]

    assert [node.name for node in classes] == ["DraftUseCases"]
    assert len(classes[0].body) == 1
    assert isinstance(classes[0].body[0], ast.Pass)
    assert "CaptureDraftCommand" not in source
    assert "ConfirmDraftCommand" not in source
    assert "SupersedeDraftCommand" not in source
