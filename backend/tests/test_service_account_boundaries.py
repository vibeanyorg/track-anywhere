from __future__ import annotations

import ast
import re
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


def test_account_use_cases_read_through_focused_repository():
    checked_files = [
        BACKEND / "service_account_queries.py",
        BACKEND / "service_account_commands.py",
    ]
    offenders = []
    forbidden = re.compile(r"\bself\.storage\.(get_account|list_accounts)\b")
    for path in checked_files:
        for line_number, line in enumerate(path.read_text().splitlines(), start=1):
            if forbidden.search(line):
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{line_number}: {line.strip()}")

    query_source = (BACKEND / "service_account_queries.py").read_text()
    command_source = (BACKEND / "service_account_commands.py").read_text()
    repository_source = (BACKEND / "storage_repositories/ledger.py").read_text()

    assert offenders == []
    assert "uow.accounts.list_accounts" in query_source
    assert "uow.accounts.get_account" in query_source
    assert "_get_account_from_storage(account_id)" in command_source
    assert "class AccountRepository" in repository_source
    assert "def list_accounts(" in repository_source
    assert "def get_account(" in repository_source


def test_account_summary_reads_through_focused_repository():
    source = (BACKEND / "service_account_summary.py").read_text()
    balance_source = (BACKEND / "service_balance_queries.py").read_text()
    forbidden = re.compile(r"\bself\.storage\.(list_accounts|account_balances)\b")
    offenders = [
        f"service_account_summary.py:{line_number}: {line.strip()}"
        for line_number, line in enumerate(source.splitlines(), start=1)
        if forbidden.search(line)
    ]

    assert offenders == []
    assert "_list_accounts_from_storage" in source
    assert "_account_balances_from_storage" in source
    assert "def _account_balances_from_storage(" in balance_source
