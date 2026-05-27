from __future__ import annotations

import ast
import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND = REPO_ROOT / "backend/app/track_anywhere"


def test_ledger_write_use_cases_are_context_scoped():
    expected_modules = {
        "service_ledger.py",
        "service_ledger_queries.py",
        "service_ledger_records.py",
        "service_ledger_reversals.py",
        "service_ledger_transfers.py",
    }
    for module in expected_modules:
        assert (BACKEND / module).exists()

    source = (BACKEND / "service_ledger.py").read_text()
    tree = ast.parse(source)
    classes = [node for node in tree.body if isinstance(node, ast.ClassDef)]

    assert [node.name for node in classes] == ["LedgerUseCases"]
    assert len(classes[0].body) == 1
    assert isinstance(classes[0].body[0], ast.Pass)
    assert "RecordExpenseCommand" not in source
    assert "RecordTransactionCommand" not in source
    assert "ReverseTransactionCommand" not in source


def test_ledger_query_use_cases_read_through_focused_repositories():
    source = (BACKEND / "service_ledger_queries.py").read_text()
    repository_source = (BACKEND / "storage_repositories/transactions.py").read_text()
    forbidden = re.compile(
        r"\bself\.storage\.(get_account|get_category|get_confirmed_transaction|list_confirmed_transactions)\b"
    )

    offenders = [
        f"service_ledger_queries.py:{line_number}: {line.strip()}"
        for line_number, line in enumerate(source.splitlines(), start=1)
        if forbidden.search(line)
    ]

    assert offenders == []
    assert "_get_account_from_storage" in source
    assert "_get_category_from_storage" in source
    assert "uow.transactions.list_confirmed_transactions" in source
    assert "uow.transactions.get_confirmed_transaction" in source
    assert "class TransactionRepository" in repository_source
    assert "def list_confirmed_transactions(" in repository_source
    assert "def get_confirmed_transaction(" in repository_source


def test_ledger_command_use_cases_read_through_focused_repositories():
    checked_files = [
        BACKEND / "service_ledger_records.py",
        BACKEND / "service_ledger_transfers.py",
        BACKEND / "service_ledger_reversals.py",
    ]
    forbidden = re.compile(
        r"\bself\.storage\.(get_account|get_category|get_confirmed_transaction|list_confirmed_transactions)\b"
    )
    offenders = []
    for path in checked_files:
        for line_number, line in enumerate(path.read_text().splitlines(), start=1):
            if forbidden.search(line):
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{line_number}: {line.strip()}")

    records_source = (BACKEND / "service_ledger_records.py").read_text()
    transfers_source = (BACKEND / "service_ledger_transfers.py").read_text()
    reversals_source = (BACKEND / "service_ledger_reversals.py").read_text()

    assert offenders == []
    assert "_get_account_from_storage" in records_source
    assert "_get_category_from_storage" in records_source
    assert "_get_account_from_storage" in transfers_source
    assert "_get_category_from_storage" in transfers_source
    assert "_get_transaction_from_storage" in reversals_source
