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
