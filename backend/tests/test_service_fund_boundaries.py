from __future__ import annotations

import ast
import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND = REPO_ROOT / "backend/app/track_anywhere"


def test_fund_use_cases_are_context_scoped():
    expected_modules = {
        "service_fund_catalog.py",
        "service_fund_flows.py",
        "service_funds.py",
    }
    for module in expected_modules:
        assert (BACKEND / module).exists()

    source = (BACKEND / "service_funds.py").read_text()
    tree = ast.parse(source)
    classes = [node for node in tree.body if isinstance(node, ast.ClassDef)]

    assert [node.name for node in classes] == ["FundUseCases"]
    assert len(classes[0].body) == 1
    assert isinstance(classes[0].body[0], ast.Pass)
    assert "CreateFundCommand" not in source
    assert "FundAllocationCommand" not in source
    assert "FundSpendCommand" not in source


def test_fund_flow_use_cases_read_accounts_through_focused_repository():
    source = (BACKEND / "service_fund_flows.py").read_text()
    forbidden = re.compile(r"\bself\.storage\.get_account\b")
    offenders = [
        f"service_fund_flows.py:{line_number}: {line.strip()}"
        for line_number, line in enumerate(source.splitlines(), start=1)
        if forbidden.search(line)
    ]

    assert offenders == []
    assert "_get_account_from_storage" in source
