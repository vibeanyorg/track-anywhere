from __future__ import annotations

import ast
import re
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


def test_draft_use_cases_read_through_focused_repositories():
    checked_files = [
        BACKEND / "service_draft_capture.py",
        BACKEND / "service_draft_confirmation.py",
        BACKEND / "service_draft_store.py",
    ]
    forbidden = re.compile(r"\bself\.storage\.(get_account|get_category|get_draft)\b")
    offenders = []
    for path in checked_files:
        for line_number, line in enumerate(path.read_text().splitlines(), start=1):
            if forbidden.search(line):
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{line_number}: {line.strip()}")

    capture_source = (BACKEND / "service_draft_capture.py").read_text()
    confirmation_source = (BACKEND / "service_draft_confirmation.py").read_text()
    store_source = (BACKEND / "service_draft_store.py").read_text()

    assert offenders == []
    assert "_get_account_from_storage" in capture_source
    assert "_get_category_from_storage" in confirmation_source
    assert "uow.drafts.get_draft" in store_source
