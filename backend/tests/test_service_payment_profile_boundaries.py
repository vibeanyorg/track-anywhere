from __future__ import annotations

import ast
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND = REPO_ROOT / "backend/app/track_anywhere"


def test_payment_profile_use_cases_are_context_scoped():
    expected_modules = {
        "service_payment_profiles.py",
        "service_payment_profile_expenses.py",
        "service_payment_profile_lifecycle.py",
    }
    for module in expected_modules:
        assert (BACKEND / module).exists()

    source = (BACKEND / "service_payment_profiles.py").read_text()
    tree = ast.parse(source)
    classes = [node for node in tree.body if isinstance(node, ast.ClassDef)]

    assert [node.name for node in classes] == ["PaymentProfileUseCases"]
    assert len(classes[0].body) == 1
    assert isinstance(classes[0].body[0], ast.Pass)
    assert "RecordPaymentProfileExpenseCommand" not in source
    assert "CreatePaymentProfileCommand" not in source
    assert "build_transaction" not in source
