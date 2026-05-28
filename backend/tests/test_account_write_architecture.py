from __future__ import annotations

import inspect
import re
from pathlib import Path

from track_anywhere.service import FinanceService


def test_service_write_paths_do_not_stage_accounts_in_ledger_mirror():
    repo_root = Path.cwd()
    offenders: list[str] = []
    patterns = [
        re.compile(r"\bledger\.create_account\("),
        re.compile(r"\bledger\.dirty_accounts\("),
    ]
    for root in ("backend/app/track_anywhere/api_routers", "backend/app/track_anywhere"):
        for path in (repo_root / root).glob("service*.py"):
            for line_number, line in enumerate(path.read_text().splitlines(), start=1):
                if any(pattern.search(line) for pattern in patterns):
                    offenders.append(f"{path.relative_to(repo_root)}:{line_number}: {line.strip()}")

    assert offenders == []


def test_partial_writers_do_not_read_ledger_dirty_accounts():
    source = (Path.cwd() / "backend/app/track_anywhere/storage_partial.py").read_text()

    assert "ledger.dirty_accounts(" not in source


def test_startup_foundations_do_not_read_runtime_ledger_mirror():
    source = inspect.getsource(FinanceService._ensure_domain_foundations)

    assert "self.ledger.accounts" not in source
    assert "self.ledger.transactions" not in source


def test_startup_foundations_read_through_focused_helpers():
    source = (Path.cwd() / "backend/app/track_anywhere/service_foundations.py").read_text()
    forbidden = re.compile(
        r"\bself\.storage\.(list_accounts|get_account|list_all_confirmed_transactions)\b"
    )
    offenders = [
        f"service_foundations.py:{line_number}: {line.strip()}"
        for line_number, line in enumerate(source.splitlines(), start=1)
        if forbidden.search(line)
    ]

    assert offenders == []
    assert "_list_accounts_from_storage" in source
    assert "_get_account_from_storage" in source
    assert "_list_all_transactions_from_storage" in source


def test_storage_load_does_not_hydrate_runtime_ledger_mirror():
    source = (Path.cwd() / "backend/app/track_anywhere/storage.py").read_text()

    assert "service.ledger.accounts =" not in source
    assert "service.ledger.transactions =" not in source
    assert "ledger.dirty_accounts(" not in source
